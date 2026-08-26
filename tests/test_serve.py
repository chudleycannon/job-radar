"""The dashboard's write paths, and the ways a click could fail in silence.

Every test here is a request that was sent to a running server and a response
that was not what came back. The shape they mostly share: the handler raised,
`http.server` dropped the connection without writing a status line, the
browser's `fetch` rejected, and the page said nothing at all. A button that
does nothing and a button that failed look identical from the sofa, and the
one that failed has usually just lost a status change.

Nothing in here spends money. `JOB_RADAR_CLAUDE` is pointed at a path that
does not exist for every test that touches `/api/generate`, so `claude_bin()`
returns "" and the job is failed before any subprocess is created. No test
binds a fixed port: the server is asked for port 0 and told to say which one
it got.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import runner, serve, store

LONG_DESC = "We are hiring an engineering leader. " * 12


# --------------------------------------------------------------- scaffolding

@contextmanager
def _lab():
    """A synthetic board, a docs folder, and a home nothing real lives in."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "board.db"
        con = store.connect(db)
        con.execute(
            "INSERT INTO roles (uid,company,title,url,location,salary_label,"
            "description,first_seen,last_seen) VALUES "
            "('uid-one','Tidewater Optics','Head of Platform Engineering',"
            "'https://example.invalid/1','Bristol','120000',?,"
            "date('now'),date('now'))", (LONG_DESC,))
        con.execute(
            "INSERT INTO roles (uid,company,title,url,description,first_seen,"
            "last_seen) VALUES ('uid-bare','Quillon Rail','Staff Engineer',"
            "'https://example.invalid/2','short',date('now'),date('now'))")
        con.close()
        home = root / "home"
        (home / ".claude" / "skills").mkdir(parents=True)
        yield root, db, home


@contextmanager
def _server(db, docs=None, config_path=None):
    """A real server on a port the OS picks, torn down on the way out."""
    serve.Handler.db_path = str(db)
    serve.Handler.docs_base = str(docs) if docs else None
    serve.Handler.config_path = str(config_path) if config_path else None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


@contextmanager
def _env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update({k: v for k, v in kw.items() if v is not None})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _req(base, path, body=None, raw=None, headers=None, method=None):
    """Returns (status, parsed body). A dropped connection is (None, reason).

    The distinction this whole file is about: a handler that answers "no" is
    not the same as a handler that vanished, and only one of them can be read
    off the page.
    """
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(
        base + path, data=data, method=method or ("POST" if data else "GET"),
        headers=headers or {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, _parse(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except Exception as e:                       # connection dropped, no reply
        return None, f"{type(e).__name__}: {e}"


def _parse(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw.decode("utf-8", "replace")


def _status(db, uid):
    con = store.connect(db)
    try:
        r = con.execute("SELECT status,note FROM role_state WHERE uid=?",
                        (uid,)).fetchone()
        return (r["status"], r["note"]) if r else (None, None)
    finally:
        con.close()


# ------------------------------------------------- a failure must be readable

def test_a_body_that_is_not_an_object_is_answered_not_dropped():
    """`json.loads("[]")` is valid JSON with no `.get`, so `data.get("uid")`
    raised AttributeError inside the handler. http.server writes no status
    line for that: curl reported "Empty reply from server" and the page's
    `fetch` rejected, so the click did nothing and said nothing."""
    with _lab() as (root, db, home), _server(db) as base:
        for raw in (b"[]", b'"hi"', b"5", b"null", b'{"uid":'):
            code, body = _req(base, "/api/status", raw=raw)
            assert code == 400, (raw, code, body)
            assert "role id" in body["error"], (raw, body)


def test_an_unhashable_kind_is_answered_not_dropped():
    """`kind not in runner.KINDS` hashes `kind`. A JSON list raised
    TypeError: unhashable type: 'list' and killed the connection, where a
    string that is not a kind answered "bad kind" politely."""
    with _lab() as (root, db, home), _server(db) as base:
        for kind in ([], {"a": 1}, 7, None):
            code, body = _req(base, "/api/generate",
                              {"uid": "uid-one", "kind": kind})
            assert code == 400, (kind, code, body)
            assert body["error"] == "bad kind", (kind, body)


def test_a_note_that_is_not_text_is_answered_not_dropped():
    """sqlite refused to bind a dict as a parameter, so the write raised
    InterfaceError after the status had been validated. The connection died
    mid-request and nothing was saved, with nothing on screen to say so."""
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/status",
                          {"uid": "uid-one", "status": "applied",
                           "note": {"a": 1}})
        assert code == 400, (code, body)
        assert "text" in body["error"], body
        assert _status(db, "uid-one") == (None, None), "nothing should be written"


def test_a_content_length_that_is_not_a_number_is_answered():
    """`int(self.headers.get("Content-Length"))` raised ValueError before any
    routing happened, so every endpoint died on a header a client got wrong."""
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/status", raw=b"", method="POST",
                          headers={"Content-Length": "abc"})
        assert code == 400, (code, body)
        assert isinstance(body, dict) and body["ok"] is False, body


def test_a_write_that_loses_to_a_scan_says_so():
    """The one contention case that happens by itself: `scan` holds the write
    lock while it updates the board, sqlite waits its 15 seconds and raises
    "database is locked", and the handler died. The status change was lost and
    the button reported neither success nor failure."""
    with _lab() as (root, db, home), _server(db) as base:
        blocker = sqlite3.connect(db, timeout=2)
        blocker.execute("BEGIN EXCLUSIVE")
        blocker.execute("UPDATE roles SET score=score+1")
        try:
            code, body = _req(base, "/api/status",
                              {"uid": "uid-one", "status": "applied"})
        finally:
            blocker.rollback()
            blocker.close()
        assert code == 503, (code, body)
        assert "busy" in body["error"], body
        assert body["ok"] is False
        # And it must not claim to have saved something it did not.
        assert _status(db, "uid-one") == (None, None)


def test_a_good_request_still_works():
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/status",
                          {"uid": "uid-one", "status": "applied",
                           "note": "second round 3 Sept"})
        assert code == 200 and body["ok"] is True, (code, body)
        assert _status(db, "uid-one") == ("applied", "second round 3 Sept")
        # None keeps the note, "" clears it. Skip and Apply send no note and
        # must not wipe one on the way past.
        _req(base, "/api/status", {"uid": "uid-one", "status": "interviewing"})
        assert _status(db, "uid-one") == ("interviewing", "second round 3 Sept")
        _req(base, "/api/status",
             {"uid": "uid-one", "status": "interviewing", "note": ""})
        assert _status(db, "uid-one") == ("interviewing", "")


def test_the_ordinary_refusals_keep_their_words():
    with _lab() as (root, db, home), _server(db) as base:
        cases = [
            ("/api/status", {"status": "applied"}, 400, "role id"),
            ("/api/status", {"uid": "nope", "status": "applied"}, 404, "no such role"),
            ("/api/status", {"uid": "uid-one", "status": "deleted"}, 400, "bad status"),
            ("/api/status", {"uid": "uid-one", "status": 5}, 400, "bad status"),
            ("/api/generate", {"uid": "uid-one", "kind": "nuke"}, 400, "bad kind"),
            ("/api/generate", {"uid": "uid-bare", "kind": "screen"}, 409, "no description"),
            ("/api/generate", {"uid": "uid-one", "kind": "cover_letter"},
             409, "draft the CV first"),
        ]
        for path, body, want_code, want_text in cases:
            code, got = _req(base, path, body)
            assert code == want_code, (path, body, code, got)
            assert want_text in got["error"], (path, got)


def test_cross_origin_and_rebinding_are_refused():
    """This server spends money, and a text/plain body is a simple request
    with no preflight to stop it."""
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/status",
                          {"uid": "uid-one", "status": "applied"},
                          headers={"Content-Type": "application/json",
                                   "Origin": "http://evil.example"})
        assert code == 403 and "cross-origin" in body["error"], (code, body)
        code, body = _req(base, "/api/status",
                          {"uid": "uid-one", "status": "applied"},
                          headers={"Content-Type": "application/json",
                                   "Host": "evil.example"})
        assert code == 403, (code, body)
        assert _status(db, "uid-one") == (None, None)


# ------------------------------------------------------------ path handling

def test_open_cannot_reach_a_file_this_tool_did_not_make():
    """`/open` runs `open -R` on a path from the query string, so any page in
    the browser could have pointed it anywhere on the disk. The allowlist is
    the `artifacts` table itself."""
    with _lab() as (root, db, home), _server(db) as base:
        secret = root / "not-ours.txt"
        secret.write_text("private", encoding="utf-8")
        con = store.connect(db)
        mine = root / "docs" / "CV.docx"
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("x", encoding="utf-8")
        store.add_artifact(con, "uid-one", "cv", str(mine))
        con.close()
        from urllib.parse import quote
        for target in (str(secret), "/etc/hosts",
                       str(mine.parent) + "/../../../../etc/hosts",
                       str(root / "docs" / ".." / "not-ours.txt")):
            code, body = _req(base, "/open?path=" + quote(target), method="GET")
            assert code == 403, (target, code, body)
            assert "not a document this tool made" in body["error"], (target, body)


def test_open_falls_back_to_the_stored_copy_when_the_file_is_gone():
    """A generated document costs real money. If the folder was cleaned, the
    text is still in the database and that is what should be shown, rather
    than a 404 about something someone paid for."""
    with _lab() as (root, db, home), _server(db) as base:
        gone = root / "docs" / "2026-08-25-role" / "screening.md"
        con = store.connect(db)
        store.add_artifact(con, "uid-one", "screen", str(gone),
                           body="APPLY\n\nNo dealbreakers hit.")
        con.close()
        from urllib.parse import quote
        code, body = _req(base, "/open?path=" + quote(str(gone)), method="GET")
        assert code == 200, (code, body)
        assert "No dealbreakers hit." in body, body
        assert "no longer on disk" in body, body


# ---------------------------------------------------- jobs that go wrong

def test_a_worker_that_dies_before_it_starts_does_not_leave_a_spinner():
    """`run_job` opens its connection before its own try block, so a failure
    there threw straight out of the thread. The row stayed 'pending' with
    nothing behind it, the dashboard spun on it, and nothing clears that state
    until the next restart: `reap_orphans` only runs when `serve` starts."""
    with _lab() as (root, db, home):
        con = store.connect(db)
        job = store.enqueue(con, "uid-one", "cv")
        assert store.claim(con, "generate")
        real = store.connect
        calls = {"n": 0}

        def flaky(p=None):
            calls["n"] += 1
            if calls["n"] == 1:                  # a transient lock, then fine
                raise sqlite3.OperationalError("database is locked")
            return real(p)

        store.connect = flaky
        try:
            with _quiet_threads():
                runner.spawn(job, db_path=str(db))
                _settle(lambda: con.execute("SELECT state FROM jobs WHERE id=?",
                                            (job,)).fetchone()["state"] != "pending")
        finally:
            store.connect = real
        row = con.execute("SELECT state,error FROM jobs WHERE id=?",
                          (job,)).fetchone()
        assert row["state"] == "failed", dict(row)
        assert "database is locked" in row["error"], row["error"]
        held = con.execute("SELECT 1 FROM locks WHERE name='generate'").fetchone()
        assert held is None, "a held lock refuses every later generation"
        con.close()


def test_a_rank_worker_that_dies_before_it_starts_unwedges_the_button():
    """`_spawn_rank` opened its connection outside the handler that exists to
    clear `rank_state`, so a failure there left the flag on "running" with no
    error text: the dashboard showed a run in progress that would never move,
    and every later click was refused with "already ranking" for the life of
    the server."""
    with _lab() as (root, db, home):
        con = store.connect(db)
        store.set_meta(con, "rank_state", "running")
        assert store.claim(con, "rank")
        real = store.connect
        # Keyed to the worker thread, not to a call count.
        #
        # `store.connect` is a module global, so patching it arms every caller
        # in the process, and counting calls hands the injected failure to
        # whichever one gets there first. That is fine on an idle machine and
        # a coin toss on a loaded CI runner: this test passed on three of five
        # runners, twelve times out of twelve in isolation, and failed in the
        # full suite. A flaky test is worse than no test, because it teaches
        # you to stop reading the red.
        #
        # The worker is the only caller on a non-main thread, and the main
        # thread here reads through a connection it already holds, so this
        # cannot be consumed by anything else.
        armed = threading.Event()
        armed.set()

        def flaky(p=None):
            if armed.is_set() and threading.current_thread() is not threading.main_thread():
                armed.clear()
                raise sqlite3.OperationalError("database is locked")
            return real(p)

        store.connect = flaky
        try:
            with _quiet_threads():
                serve._spawn_rank(str(db), None)
                _settle(lambda: store.get_meta(con, "rank_state") == "idle")
        finally:
            store.connect = real
        assert store.get_meta(con, "rank_state") == "idle"
        assert "database" in (store.get_meta(con, "rank_error", "") or "")
        assert con.execute("SELECT 1 FROM locks WHERE name='rank'").fetchone() is None
        assert store.claim(con, "rank"), "the button has to work again"
        con.close()


def test_a_rank_that_fails_after_claiming_gives_the_lock_back():
    """Everything between `claim` and the spawn is a query or a write, so any
    of it can lose a race with a scan. A lock left taken refuses every later
    rank until the server restarts."""
    with _lab() as (root, db, home), _server(db) as base:
        from jobradar import rank as rank_mod
        real = rank_mod.candidates
        rank_mod.candidates = lambda *a, **k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked"))
        try:
            code, body = _req(base, "/api/rank", {})
        finally:
            rank_mod.candidates = real
        assert code == 503, (code, body)
        assert body["ok"] is False and "busy" in body["error"], body
        con = store.connect(db)
        try:
            assert con.execute(
                "SELECT 1 FROM locks WHERE name='rank'").fetchone() is None
            assert store.get_meta(con, "rank_state", "idle") == "idle"
        finally:
            con.close()


def test_generate_without_the_cli_fails_the_job_and_frees_the_lock():
    """No subprocess is created: `claude_bin()` answers "" and the job is
    failed before anything is written."""
    with _lab() as (root, db, home), _env(
            JOB_RADAR_CLAUDE=str(root / "no-such-claude"),
            JOB_RADAR_CV=str(root / "cv.md")), _server(db, docs=root / "docs") as base:
        (root / "cv.md").write_text("Rowan Ashby\n", encoding="utf-8")
        code, body = _req(base, "/api/generate",
                          {"uid": "uid-one", "kind": "screen"})
        assert code == 200 and body["ok"] is True, (code, body)
        con = store.connect(db)
        try:
            _settle(lambda: con.execute(
                "SELECT state FROM jobs WHERE id=?",
                (body["job"],)).fetchone()["state"] == "failed")
            row = con.execute("SELECT state,error FROM jobs WHERE id=?",
                              (body["job"],)).fetchone()
            assert row["state"] == "failed", dict(row)
            assert "claude" in row["error"], row["error"]
            # The lock is given back in the worker's `finally`, just after the
            # row is marked, so wait on the lock rather than on the row.
            _settle(lambda: con.execute(
                "SELECT 1 FROM locks WHERE name='generate'").fetchone() is None)
            assert con.execute(
                "SELECT 1 FROM locks WHERE name='generate'").fetchone() is None
            assert not (root / "docs").exists(), \
                "nothing should be written for a job that cannot run"
        finally:
            con.close()


@contextmanager
def _quiet_threads():
    """The two fault-injection tests want the worker to die, and a dying
    worker prints its traceback on purpose: that is where the real cause
    lives once the error column has truncated it. It is noise here."""
    old = threading.excepthook
    threading.excepthook = lambda args: None
    try:
        yield
    finally:
        threading.excepthook = old


def _settle(done, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if done():
            return
        time.sleep(0.05)


# ------------------------------------------------------------------- gates

def _doc_folder(root, text="# Rowan Ashby\n\nRan the release train.\n"):
    from jobradar.docx import markdown_to_docx
    d = root / "docs" / "2026-08-25-tidewater-optics-head-aaaaaa"
    d.mkdir(parents=True, exist_ok=True)
    (d / "source-cv.txt").write_text(
        "Rowan Ashby. Ran the release train for 3 years.", encoding="utf-8")
    (d / "CV.md").write_text(text, encoding="utf-8")
    markdown_to_docx(text, d / "CV.docx")
    return d


def test_gates_read_the_document_not_the_docx_container():
    """`_record` gates "CV.md" but stores a row pointing at "CV.docx", and
    `regate` -- which runs on every `serve` start -- fed that .docx back in. A
    deflate-compressed zip read as text got every gate wrong in both
    directions: a CV containing an em-dash passed `no_em_dash`, because the
    character is inside the compressed stream, and a clean CV failed
    `unsourced_specifics` with "20", "7%", "0b" listed as figures it had
    invented. None of those bytes are in the document."""
    with _lab() as (root, db, home), _env(HOME=str(home)):
        d = _doc_folder(root, "# Rowan Ashby\n\nRan the release train "
                              "for 3 years — and it worked.\n")
        from_md = runner._gates(d, "CV.md")
        from_docx = runner._gates(d, "CV.docx")
        assert from_md["no_em_dash"] is False, from_md
        assert from_docx["no_em_dash"] is False, \
            f"the em-dash is in the document: {from_docx}"
        assert from_md["unsourced_specifics"] is True, from_md
        assert from_docx["unsourced_specifics"] is True, from_docx
        assert "unsourced_found" not in from_docx, from_docx


def test_regate_keeps_the_overlap_gate_when_it_cannot_measure_it():
    """`_record` writes False when there is no CV to compare against, on the
    rule that an unmeasurable gate is a failed gate. `regate` left the key out
    instead, so a restart turned "never checked" into a key that is not there
    -- and the dashboard counts only `is False`, so it rendered as a pass."""
    with _lab() as (root, db, home), _env(HOME=str(home)):
        from jobradar.docx import markdown_to_docx
        d = root / "docs" / "role"
        d.mkdir(parents=True)
        letter = "Dear hiring manager,\n\nI would like to apply.\n"
        (d / "cover-letter.md").write_text(letter, encoding="utf-8")
        markdown_to_docx(letter, d / "cover-letter.docx")
        con = store.connect(db)
        try:
            store.add_artifact(con, "uid-one", "cover_letter",
                               d / "cover-letter.docx",
                               gates={"no_overlap_with_cv": False})
            runner.regate(con)                    # no CV.md beside the letter
            gates = json.loads(con.execute(
                "SELECT gates FROM artifacts WHERE kind='cover_letter'"
            ).fetchone()["gates"])
            assert gates.get("no_overlap_with_cv") is False, gates
            assert "not checked" in con.execute(
                "SELECT summary FROM artifacts WHERE kind='cover_letter'"
            ).fetchone()["summary"]
            # And with a CV beside it, it is measured for real.
            (d / "CV.md").write_text("# Rowan Ashby\n\nRan the release "
                                     "train.\n", encoding="utf-8")
            runner.regate(con)
            gates = json.loads(con.execute(
                "SELECT gates FROM artifacts WHERE kind='cover_letter'"
            ).fetchone()["gates"])
            assert gates["no_overlap_with_cv"] is True, gates
        finally:
            con.close()


def test_one_unreadable_artifact_row_does_not_stop_the_server_starting():
    """`serve` calls `regate` before it binds a port. An artifact row with an
    empty path resolves to the working directory, and `read_text` on a
    directory raises IsADirectoryError, so one bad row meant no dashboard."""
    with _lab() as (root, db, home), _env(HOME=str(home)):
        con = store.connect(db)
        try:
            store.add_artifact(con, "uid-one", "cv", "")
            store.add_artifact(con, "uid-one", "cv", str(root / "gone.docx"))
            d = _doc_folder(root)
            store.add_artifact(con, "uid-one", "cv", str(d / "CV.docx"))
            assert runner.regate(con) == 1, "the good row is still rechecked"
        finally:
            con.close()


def test_a_broken_config_is_not_reported_as_a_missing_cv():
    """The config load sat in a bare `except: pass`, so a `titles:` block that
    would not parse came back as "No CV configured. Set `cv.path`" -- pointing
    at a setting that was already right."""
    with _lab() as (root, db, home), _env(HOME=str(home),
                                          JOB_RADAR_CLAUDE=str(root / "claude"),
                                          JOB_RADAR_CV=""):
        # Present so the CLI check passes, never executed: the job stops at
        # the config, several steps before anything is spawned.
        (root / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (root / "claude").chmod(0o755)
        bad = root / "config.yaml"
        bad.write_text("titles:\n  - Head of Engineering\n", encoding="utf-8")
        con = store.connect(db)
        try:
            job = store.enqueue(con, "uid-one", "screen")
            runner.run_job(job, db_path=str(db), base=str(root / "docs"),
                           config_path=str(bad))
            row = con.execute("SELECT state,error FROM jobs WHERE id=?",
                              (job,)).fetchone()
            assert row["state"] == "failed", dict(row)
            # Not "No CV configured": `cv.path` is not the thing that is
            # wrong, and sending someone to check it wastes the trip.
            assert "could not read your config" in row["error"], row["error"]
            assert "AttributeError" in row["error"], row["error"]
        finally:
            con.close()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except BaseException as e:
                fails += 1
                print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    sys.exit(1 if fails else 0)
