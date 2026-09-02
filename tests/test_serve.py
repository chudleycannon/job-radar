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

import contextlib
import io
import json
import os
import re
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
from unittest import mock

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


def test_a_screen_answer_can_be_saved_for_later_generation():
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/screen-answer",
                          {"uid": "uid-one",
                           "body": "I have led ITIL-aligned incident reviews."})
        assert code == 200 and body["ok"] is True, (code, body)
        con = store.connect(db)
        try:
            row = con.execute(
                "SELECT kind,summary,body FROM artifacts WHERE uid=?",
                ("uid-one",)).fetchone()
        finally:
            con.close()
        assert row["kind"] == "screen_answer", dict(row)
        assert row["summary"] == "I have led ITIL-aligned incident reviews."
        assert row["body"] == "I have led ITIL-aligned incident reviews."
        con = store.connect(db)
        try:
            ev = store.candidate_evidence(con)
            assert len(ev) == 1
            assert ev[0]["status"] == "proposed"
            assert "ITIL-aligned" in ev[0]["body"]
        finally:
            con.close()


def test_a_role_can_reset_saved_generation_outputs():
    with _lab() as (root, db, home), _server(db) as base:
        con = store.connect(db)
        try:
            store.set_status(con, "uid-one", "interested", "keep this note")
            for kind in ("screen", "screen_answer", "cv", "cover_letter",
                         "evidence_used", "jd_snapshot"):
                store.add_artifact(con, "uid-one", kind, body=f"{kind} body")
            store.add_artifact(con, "uid-one", "other", body="keep me")
            store.enqueue(con, "uid-one", "cv")
        finally:
            con.close()

        code, body = _req(base, "/api/reset-outputs", {"uid": "uid-one"})
        assert code == 200 and body["ok"] is True, (code, body)
        assert body["artifacts"] == 6, body
        assert body["jobs"] == 1, body

        con = store.connect(db)
        try:
            remaining = [r["kind"] for r in store.artifacts_for(con, "uid-one")]
            assert remaining == ["other"], remaining
            assert con.execute(
                "SELECT COUNT(*) c FROM jobs WHERE uid='uid-one'"
            ).fetchone()["c"] == 0
        finally:
            con.close()
        assert _status(db, "uid-one") == ("interested", "keep this note")


def test_dashboard_shows_reset_when_a_role_has_saved_outputs():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.add_artifact(con, "uid-one", "screen_answer",
                               body="extra screening context")
        finally:
            con.close()
        with _server(db) as base:
            code, page = _req(base, "/", method="GET")
            assert code == 200, (code, page)
            assert 'data-reset-outputs="1"' in page, page


def test_a_blank_screen_answer_is_refused():
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/screen-answer",
                          {"uid": "uid-one", "body": "   "})
        assert code == 400, (code, body)
        assert "answer" in body["error"], body


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


def test_a_fresh_browser_start_shows_setup_instead_of_a_terminal_instruction():
    """A container starts with no config and no database. `serve` has to be a
    front door in that state, not a command that tells the user to go back to
    the terminal before the browser can do anything useful."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        store.connect(db).close()
        with _server(db, config_path=root / "config.yaml") as base:
            code, body = _req(base, "/")
            assert code == 200, (code, body)
            assert "Set up your search" in body, body
            assert "/data/my-cv.pdf" in body, body


def test_the_browser_setup_route_writes_a_loadable_config():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        cv = root / "cv.md"
        cv.write_text("Rowan Ashby\nEngineering Manager\n", encoding="utf-8")
        store.connect(db).close()
        cfg = root / "config.yaml"
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/setup", {
                "cv_path": str(cv),
                "titles_include": ["engineering manager", "head of engineering"],
                "countries": ["UK"],
                "remote_ok": True,
                "remote_only": True,
                "salary_floor": "70000",
                "salary_currency": "GBP",
                "dealbreakers": ["take-home test"],
                "concurrency": "16",
            })
            assert code == 200, (code, body)
            assert body["ok"] is True, body
            assert cfg.is_file()

        from jobradar.config import load
        got = load(cfg)
        assert got.cv_path == str(cv.resolve())
        assert got.titles_include == ["engineering manager", "head of engineering"]
        assert got.work_modes == ["remote"]
        assert got.salary_floor == 70000
        assert got.dealbreakers[0].name == "take-home test"


def test_setup_imports_the_cv_as_proposed_profile_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        cv = root / "cv.md"
        cv.write_text(
            "# Profile\n\nEngineering leader.\n\n# Selected achievements\n\n"
            "Improved deployment frequency from 2 releases per month to daily.\n",
            encoding="utf-8")
        store.connect(db).close()
        cfg = root / "config.yaml"
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/setup", {
                "cv_path": str(cv),
                "titles_include": ["engineering manager"],
                "countries": ["UK"],
                "remote_ok": True,
                "concurrency": "16",
            })
            assert code == 200, (code, body)
            assert "Imported" in body["profile"], body

        con = store.connect(db)
        try:
            rows = store.candidate_evidence(con)
            assert rows, "the CV should become reviewable evidence"
            assert {r["status"] for r in rows} == {"proposed"}
            assert "deployment frequency" in "\n".join(r["body"] for r in rows)
        finally:
            con.close()


def test_setup_imports_core_expertise_as_keyword_groups():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        cv = root / "cv.md"
        cv.write_text(
            "# Core expertise and keywords\n\n"
            "- Engineering leadership: people management, coaching, hiring, onboarding.\n"
            "- Observability and operations: monitoring, alerting, SLOs, incident management.\n"
            "\n# Profile\n\nEngineering leader.\n",
            encoding="utf-8")
        store.connect(db).close()
        cfg = root / "config.yaml"
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/setup", {
                "cv_path": str(cv),
                "titles_include": ["engineering manager"],
                "countries": ["UK"],
                "remote_ok": True,
                "concurrency": "16",
            })
            assert code == 200, (code, body)

        con = store.connect(db)
        try:
            rows = store.candidate_keywords(con)
            assert [r["title"] for r in rows] == [
                "Engineering leadership", "Observability and operations"]
            assert rows[0]["status"] == "proposed"
            assert "people management" in rows[0]["keywords"]
            assert "incident management" in rows[1]["keywords"]
        finally:
            con.close()


def test_an_existing_cv_config_is_migrated_on_first_dashboard_load():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        cv = root / "cv.md"
        cv.write_text("# Skills\n\nIncident management and audit controls.\n",
                      encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n",
            encoding="utf-8")
        store.connect(db).close()
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/")
            assert code == 200, (code, body)
            assert "job radar" in body

        con = store.connect(db)
        try:
            rows = store.candidate_evidence(con)
            assert len(rows) == 1
            assert rows[0]["status"] == "proposed"
            assert rows[0]["category"] in ("incident_management", "governance", "skill")
        finally:
            con.close()


def test_the_profile_page_can_edit_and_approve_evidence():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            eid = store.add_candidate_evidence(
                con, "Incident reviews", "Led incident reviews.",
                category="incident_management", tags=["incident"],
                source="CV import: cv.md")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/profile")
            assert code == 200, (code, body)
            assert "Candidate Profile" in body
            assert "Led incident reviews." in body
            assert "cardSay(card,'Saving...')" in body
            assert "card.classList.add(status)" in body
            assert 'class="ev-msg" aria-live="polite"' in body

            code, body = _req(base, "/api/profile/evidence", {
                "id": eid,
                "status": "approved",
                "body": "Led ITIL-aligned incident reviews.",
                "category": "incident_management",
                "tags": ["ITIL", "incident"],
            })
            assert code == 200 and body["ok"] is True, (code, body)

        con = store.connect(db)
        try:
            rows = store.candidate_evidence(con, statuses=["approved"])
            assert len(rows) == 1
            assert rows[0]["body"] == "Led ITIL-aligned incident reviews."
            assert rows[0]["tags"] == ["ITIL", "incident"]
        finally:
            con.close()


def test_the_profile_page_can_store_core_expertise_keywords():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            kid = store.add_candidate_keywords(
                con, "Cloud and DevOps", ["AWS", "Terraform"],
                status="proposed", source="CV import: cv.md")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/profile")
            assert code == 200, (code, body)
            assert "Core expertise" in body
            assert "Save keywords" in body
            assert "/api/profile/keywords" in body
            assert "Cloud and DevOps" in body
            assert "AWS, Terraform" in body

            code, body = _req(base, "/api/profile/keywords", {
                "id": kid,
                "title": "Cloud platform",
                "keywords": "AWS, Terraform, Kubernetes",
                "status": "approved",
            })
            assert code == 200 and body["ok"] is True, (code, body)
            code, body = _req(base, "/api/profile/keywords", {
                "title": "Risk and continuity",
                "keywords": ["SOX", "audit", "disaster recovery"],
                "status": "approved",
            })
            assert code == 200 and body["ok"] is True, (code, body)

        con = store.connect(db)
        try:
            rows = store.candidate_keywords(con, statuses=["approved"])
            assert [r["title"] for r in rows] == [
                "Cloud platform", "Risk and continuity"]
            assert rows[0]["keywords"] == ["AWS", "Terraform", "Kubernetes"]
            assert rows[1]["keywords"] == ["SOX", "audit", "disaster recovery"]
        finally:
            con.close()


def test_profile_status_buttons_do_not_require_resending_the_body():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            eid = store.add_candidate_evidence(
                con, "Incident reviews", "Led incident reviews.",
                category="incident_management")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/api/profile/evidence", {
                "id": eid,
                "status": "approved",
            })
            assert code == 200 and body["ok"] is True, (code, body)
        con = store.connect(db)
        try:
            row = store.candidate_evidence(con, statuses=["approved"])[0]
            assert row["body"] == "Led incident reviews."
        finally:
            con.close()


def test_the_profile_page_groups_evidence_by_category_type():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.add_candidate_evidence(
                con, "Career break", "Took a planned career break.",
                category="gap_explanation")
            store.add_candidate_evidence(
                con, "AWS", "AWS Certified Cloud Practitioner.",
                category="certification")
            store.add_candidate_evidence(
                con, "Kubernetes", "Worked with Kubernetes.",
                category="tool")
            store.add_candidate_evidence(
                con, "Incident command", "Led major incident response.",
                category="incident_management")
            store.add_candidate_evidence(
                con, "Bio", "Short personal statement.",
                category="personal_statement")
            store.add_candidate_evidence(
                con, "Remote", "Prefers remote first work.",
                category="preference")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/profile")
            assert code == 200, (code, body)
            for heading in ("Employment History", "Achievements", "Skills",
                            "Evidence", "General Info", "Preferences"):
                assert f"<h3>{heading}</h3>" in body
            assert '<details class="group" open>' in body
            assert '<span class="count">' in body
            assert '<optgroup label="Employment History">' in body
            assert '<option value="education">education</option>' in body
            assert '<option value="personal_information">personal information</option>' in body
            assert "personal_statement_style" not in body


def test_custom_profile_categories_are_created_under_evidence():
    with _lab() as (root, db, home), _server(db) as base:
        code, body = _req(base, "/api/profile/category",
                          {"name": "Operational Resilience"})
        assert code == 200 and body["category"] == "operational_resilience", (
            code, body)
        code, body = _req(base, "/api/profile/evidence", {
            "title": "Resilience",
            "body": "Improved operational resilience.",
            "category": "operational_resilience",
            "status": "approved",
        })
        assert code == 200 and body["ok"] is True, (code, body)

        code, page = _req(base, "/profile")
        assert code == 200, (code, page)
        evidence_group = page[page.index("<h3>Evidence</h3>"):]
        assert '<option value="operational_resilience" selected>operational resilience</option>' in evidence_group
        assert "Improved operational resilience." in evidence_group


def test_the_profile_can_be_rebuilt_from_the_configured_cv():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "board.db"
        cv = root / "cv.md"
        cv.write_text(
            "# Skills\n\nIncident management.\n\n# Achievements\n\n"
            "Improved deployment frequency from monthly to daily.\n",
            encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n",
            encoding="utf-8")
        con = store.connect(db)
        try:
            store.add_custom_evidence_category(con, "Operational Resilience")
            store.add_candidate_evidence(
                con, "Old note", "This should be removed.",
                category="operational_resilience", status="approved")
        finally:
            con.close()

        with _server(db, config_path=cfg) as base:
            code, page = _req(base, "/profile")
            assert code == 200, (code, page)
            assert "Rebuild from CV" in page
            assert "/api/profile/rebuild" in page
            code, body = _req(base, "/api/profile/rebuild", {})
            assert code == 200 and body["ok"] is True, (code, body)
            assert body["removed"] == 1, body
            assert body["added"] >= 1, body

        con = store.connect(db)
        try:
            rows = store.candidate_evidence(con)
            text = "\n".join(r["body"] for r in rows)
            assert "This should be removed." not in text
            assert "deployment frequency" in text
            assert {r["status"] for r in rows} == {"proposed"}
            assert "operational_resilience" in store.custom_evidence_categories(con)
        finally:
            con.close()


def test_approved_profile_cards_are_collapsed_by_default():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.add_candidate_evidence(
                con, "Approved evidence", "Already checked.",
                status="approved")
            store.add_candidate_evidence(
                con, "Proposed evidence", "Needs review.",
                status="proposed")
            store.add_candidate_keywords(
                con, "Approved keywords", ["AWS", "Terraform"],
                status="approved")
            store.add_candidate_keywords(
                con, "Proposed keywords", ["incident", "audit"],
                status="proposed")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/profile")
            assert code == 200, (code, body)
            assert "<strong>Approved evidence</strong>" in body
            assert '<summary class="card-summary"><strong>Approved keywords</strong>' in body
            assert '<details class="evidence approved" data-id=' in body
            assert '<details class="keyword-group approved" data-id=' in body
            assert '<details class="evidence proposed" data-id=' in body
            assert '<details class="keyword-group proposed" data-id=' in body
            assert not re.search(
                r'<details class="evidence approved"[^>]* open>', body)
            assert not re.search(
                r'<details class="keyword-group approved"[^>]* open>', body)
            assert re.search(
                r'<details class="evidence proposed"[^>]* open>', body)
            assert re.search(
                r'<details class="keyword-group proposed"[^>]* open>', body)


def test_rejected_profile_evidence_can_be_deleted():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            keep = store.add_candidate_evidence(
                con, "Keep", "Approved evidence.", status="approved")
            reject = store.add_candidate_evidence(
                con, "Reject", "Rejected evidence.", status="rejected")
        finally:
            con.close()
        with _server(db) as base:
            code, page = _req(base, "/profile")
            assert code == 200, (code, page)
            assert 'data-delete-evidence="1"' in page

            code, body = _req(base, "/api/profile/evidence/delete", {"id": keep})
            assert code == 400, (code, body)
            assert "only rejected" in body["error"]

            code, body = _req(base, "/api/profile/evidence/delete", {"id": reject})
            assert code == 200 and body["ok"] is True, (code, body)

        con = store.connect(db)
        try:
            bodies = [r["body"] for r in store.candidate_evidence(con)]
            assert "Approved evidence." in bodies
            assert "Rejected evidence." not in bodies
        finally:
            con.close()


def test_the_profile_page_saves_personal_info():
    with _lab() as (root, db, home), _server(db) as base:
        code, page = _req(base, "/profile")
        assert code == 200, (code, page)
        assert "Personal info" in page
        assert "/api/profile/personal" in page

        code, body = _req(base, "/api/profile/personal", {
            "name": "Ryan Begen",
            "email": "ryan@example.invalid",
            "linkedin": "https://linkedin.example/ryan",
            "github": "https://github.example/ryan",
            "links": "https://example.invalid",
        })
        assert code == 200 and body["ok"] is True, (code, body)

        code, page = _req(base, "/profile")
        assert "Ryan Begen" in page
        assert "ryan@example.invalid" in page
        assert "https://github.example/ryan" in page


def test_the_profile_page_has_maintenance_filters_bulk_actions_and_exports():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.add_candidate_evidence(
                con, "Incident command", "Led incidents.",
                category="incident_management", source="CV import: cv.md",
                status="approved", pinned=True, needs_metric=True)
            store.add_candidate_evidence(
                con, "Weak note", "Needs work.", status="proposed")
        finally:
            con.close()
        with _server(db) as base:
            code, page = _req(base, "/profile")
            assert code == 200, (code, page)
            for text in ("profile-search", "profile-status", "profile-group",
                         "profile-source", "bulk-choice", "bulk-action"):
                assert text in page
            assert "/api/profile/evidence/bulk" in page
            assert "/profile/export.md" in page
            assert "/profile/export.json" in page
            assert "Needs metric" in page
            assert 'name="pinned" type="checkbox" checked' in page

            code, body = _req(base, "/api/profile/evidence/bulk", {
                "ids": [1, 2], "action": "approve"})
            assert code == 200 and body["changed"] == 2, (code, body)
            code, body = _req(base, "/api/profile/evidence/bulk", {
                "ids": [1], "action": "needs_detail"})
            assert code == 200 and body["changed"] == 1, (code, body)

            code, md = _req(base, "/profile/export.md", method="GET")
            assert code == 200, (code, md)
            assert "Candidate Profile" in md
            assert "Incident command" in md
            code, js = _req(base, "/profile/export.json", method="GET")
            assert code == 200, (code, js)
            assert any(e["title"] == "Incident command" for e in js["evidence"])

        con = store.connect(db)
        try:
            rows = store.candidate_evidence(con, statuses=["approved"])
            assert len(rows) == 2
            assert rows[0]["needs_detail"] or rows[1]["needs_detail"]
        finally:
            con.close()


def test_profile_card_edits_save_maintenance_flags():
    with _lab() as (root, db, home), _server(db) as base:
        con = store.connect(db)
        try:
            eid = store.add_candidate_evidence(
                con, "Evidence", "A useful fact.", status="approved")
        finally:
            con.close()
        code, body = _req(base, "/api/profile/evidence", {
            "id": eid,
            "title": "Evidence",
            "body": "A useful fact.",
            "category": "general",
            "status": "approved",
            "pinned": True,
            "needs_detail": True,
            "needs_metric": True,
        })
        assert code == 200 and body["ok"] is True, (code, body)
        con = store.connect(db)
        try:
            row = store.candidate_evidence(con)[0]
            assert row["pinned"] == 1
            assert row["needs_detail"] == 1
            assert row["needs_metric"] == 1
        finally:
            con.close()


def test_old_personal_statement_style_categories_are_migrated():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "board.db"
        con = store.connect(db)
        try:
            con.execute(
                """INSERT INTO candidate_evidence
                (title,body,category,tags,source,status,created_at,updated_at)
                VALUES ('Statement','Body','personal_statement_style','[]','old',
                        'approved','2026-01-01T00:00:00','2026-01-01T00:00:00')""")
        finally:
            con.close()
        con = store.connect(db)
        try:
            row = store.candidate_evidence(con)[0]
            assert row["category"] == "personal_statement"
        finally:
            con.close()


def test_the_browser_scan_button_says_setup_first_without_a_config():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "empty.db"
        store.connect(db).close()
        with _server(db, config_path=root / "config.yaml") as base:
            code, body = _req(base, "/api/scan", {})
            assert code == 409, (code, body)
            assert "Set up" in body["error"], body


def test_the_browser_scan_status_includes_completion_rate():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.set_meta(con, "scan_state", "running")
            store.set_meta(con, "scan_done", "2")
            store.set_meta(con, "scan_total", "8")
            store.set_meta(con, "scan_responded", "2")
            store.set_meta(con, "scan_postings", "17")
            store.set_meta(con, "scan_phase", "3")
            store.set_meta(con, "scan_phase_label", "Greenhouse")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/api/scan")
            assert code == 200, (code, body)
            assert body["done"] == 2, body
            assert body["total"] == 8, body
            assert body["percent"] == 25, body
            assert body["responded"] == 2, body
            assert body["postings"] == 17, body
            assert body["phase"] == 3, body
            assert body["phase_label"] == "Greenhouse", body
            assert body["stopping"] is False, body


def test_the_browser_can_ask_a_running_scan_to_stop():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.set_meta(con, "scan_state", "running")
            store.set_meta(con, "scan_cancel", "")
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/api/scan/stop", {})
            assert code == 200 and body["ok"] is True, (code, body)
            assert "stopping" in body["message"], body
            code, body = _req(base, "/api/scan")
            assert code == 200, (code, body)
            assert body["stopping"] is True, body


def test_serve_start_clears_a_scan_left_running_by_an_old_container():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.set_meta(con, "scan_state", "running")
            store.set_meta(con, "scan_cancel", "1")
            store.set_meta(con, "scan_error", "old error")
            store.set_meta(con, "scan_done", "12")
            store.set_meta(con, "scan_total", "99")
        finally:
            con.close()
        con = store.connect(db)
        try:
            serve._prepare_start(con)
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/api/scan")
            assert code == 200, (code, body)
            assert body["state"] == "idle", body
            assert body["stopping"] is False, body
            assert body["error"] == "", body
            assert body["stopped"] is True, body


def test_serve_start_does_not_replay_a_stale_rank_error():
    with _lab() as (root, db, home):
        con = store.connect(db)
        try:
            store.set_meta(con, "rank_state", "idle")
            store.set_meta(con, "rank_error", "ranking failed: old max_tokens error")
        finally:
            con.close()
        con = store.connect(db)
        try:
            serve._prepare_start(con)
        finally:
            con.close()
        with _server(db) as base:
            code, body = _req(base, "/api/rank")
            assert code == 200, (code, body)
            assert body["state"] == "idle", body
            assert body["error"] == "", body


def test_settings_page_saves_ai_credentials_without_echoing_them():
    with _lab() as (root, db, home):
        cfg = root / "config.yaml"
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/settings")
            assert code == 200 and body["ok"] is True, (code, body)
            assert body["anthropic_key_set"] is False, body

            secret = "sk-ant-api03-test-secret"
            code, body = _req(base, "/api/settings", {
                "provider": "anthropic",
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com/anthropic",
                "max_tokens": "2048",
                "anthropic_api_key": secret,
            })
            assert code == 200 and body["ok"] is True, (code, body)
            assert secret in cfg.read_text(encoding="utf-8")

            code, body = _req(base, "/api/settings")
            assert code == 200, (code, body)
            assert body["anthropic_key_set"] is True, body
            assert body["base_url"] == "https://api.deepseek.com/anthropic", body
            assert secret not in json.dumps(body), body

            text = cfg.read_text(encoding="utf-8")
            assert "provider: anthropic" in text, text
            assert "model: \"deepseek-v4-pro\"" in text, text
            assert "base_url: \"https://api.deepseek.com/anthropic\"" in text, text
            assert "max_tokens: 2048" in text, text


def test_setup_preserves_saved_ai_credentials():
    with _lab() as (root, db, home):
        cv = root / "cv.md"
        cv.write_text("Rowan Ashby\nEngineering leader.\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: ''\n"
            "ai:\n"
            "  provider: anthropic\n"
            "  model: \"claude-sonnet-5\"\n"
            "  base_url: \"https://api.deepseek.com/anthropic\"\n"
            "  anthropic_api_key: \"sk-ant-api03-kept\"\n"
            "  max_tokens: 2048\n",
            encoding="utf-8")
        with _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/setup", {
                "cv_path": str(cv),
                "titles_include": ["Head of Engineering"],
                "countries": ["UK"],
            })
            assert code == 200 and body["ok"] is True, (code, body)
            text = cfg.read_text(encoding="utf-8")
            assert "anthropic_api_key: \"sk-ant-api03-kept\"" in text, text
            from jobradar.config import load
            parsed = load(cfg)
            assert parsed.ai_provider == "anthropic"
            assert parsed.ai_base_url == "https://api.deepseek.com/anthropic"
            assert parsed.anthropic_api_key == "sk-ant-api03-kept"
            assert parsed.ai_max_tokens == 2048


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


def test_artifact_link_serves_the_generated_cv_markdown_preview():
    """The dashboard should open the formatted Markdown source for a generated
    CV, not download the rendered document."""
    with _lab() as (root, db, home):
        cv = root / "source-cv.md"
        cv.write_text("Rowan Ashby\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n",
            encoding="utf-8")
        con = store.connect(db)
        try:
            made = root / "docs" / "CV.docx"
            made.parent.mkdir(parents=True, exist_ok=True)
            made.write_text("download me", encoding="utf-8")
            (made.parent / "CV.md").write_text(
                "# Rowan Ashby\n\n- Led incidents.\n", encoding="utf-8")
            aid = store.add_artifact(con, "uid-one", "cv", str(made),
                                     body="download me")
            letter = root / "docs" / "cover-letter.docx"
            letter.write_text("letter docx", encoding="utf-8")
            (letter.parent / "cover-letter.md").write_text(
                "# Cover Letter\n\nDear hiring team.\n", encoding="utf-8")
            lid = store.add_artifact(con, "uid-one", "cover_letter", str(letter),
                                     body="letter fallback")
        finally:
            con.close()

        with _server(db, config_path=cfg) as base:
            code, page = _req(base, "/", method="GET")
            assert code == 200, (code, page)
            assert f'/artifact/{aid}' in page, page
            assert f'/artifact/{aid}/download.md' in page, page
            assert f'/artifact/{aid}/download.docx' in page, page
            assert f'/artifact/{lid}' in page, page
            assert f'/artifact/{lid}/download.md' in page, page
            assert f'/artifact/{lid}/download.docx' in page, page

            code, body = _req(base, f"/artifact/{aid}", method="GET")
            assert code == 200, (code, body)
            assert "<h1>Rowan Ashby</h1>" in body, body
            assert "<li>Led incidents.</li>" in body, body
            assert "Rewrite selection" in body, body
            assert "/api/artifact/rewrite" in body, body
            assert f"href='/artifact/{aid}'" in body, body
            assert f"href='/artifact/{lid}'" in body, body
            assert "href='/role/uid-one/job-description'" in body, body
            assert "download me" not in body, body

            code, body = _req(base, f"/artifact/{aid}/download.md", method="GET")
            assert code == 200, (code, body)
            assert "# Rowan Ashby" in body, body
            code, body = _req(base, f"/artifact/{aid}/download.docx", method="GET")
            assert code == 200, (code, body)
            assert body == "download me", body
            code, body = _req(base, f"/artifact/{lid}", method="GET")
            assert code == 200, (code, body)
            assert "<h1>Cover Letter</h1>" in body, body
            assert f"href='/artifact/{aid}'" in body, body
            assert "href='/role/uid-one/job-description'" in body, body
            code, body = _req(base, f"/artifact/{lid}/download.md", method="GET")
            assert code == 200, (code, body)
            assert "# Cover Letter" in body, body
            code, body = _req(base, f"/artifact/{lid}/download.docx", method="GET")
            assert code == 200, (code, body)
            assert body == "letter docx", body

            code, body = _req(base, "/role/uid-one/job-description", method="GET")
            assert code == 200, (code, body)
            assert "<h1>Job description</h1>" in body, body
            assert "Tidewater Optics" in body, body
            assert "We are hiring an engineering leader." in body, body
            assert f"href='/artifact/{aid}'" in body, body
            assert f"href='/artifact/{lid}'" in body, body
            assert "class='active' href='/role/uid-one/job-description'" in body, body


def test_artifact_preview_can_rewrite_and_apply_selected_text():
    with _lab() as (root, db, home):
        cv = root / "source-cv.md"
        cv.write_text("Rowan Ashby\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n"
            "ai:\n"
            "  provider: anthropic\n"
            "  model: \"claude-sonnet-5\"\n"
            "  anthropic_api_key: \"sk-ant-api03-test\"\n",
            encoding="utf-8")
        made = root / "docs" / "CV.docx"
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text("old docx", encoding="utf-8")
        md = made.parent / "CV.md"
        md.write_text("# Rowan Ashby\n\nLed incidents badly.\n", encoding="utf-8")
        con = store.connect(db)
        try:
            aid = store.add_artifact(con, "uid-one", "cv", str(made),
                                     body=md.read_text(encoding="utf-8"))
        finally:
            con.close()

        seen = {}

        def complete(prompt, *a, **k):
            seen["prompt"] = prompt
            return "Led calm incident response with clear ownership."

        with mock.patch("jobradar.ai.complete", complete), \
                _server(db, config_path=cfg) as base:
            code, body = _req(base, "/api/artifact/rewrite", {
                "id": aid,
                "selected": "Led incidents badly.",
                "instruction": "Make it more specific and professional.",
            })
            assert code == 200, (code, body)
            assert body["replacement"] == (
                "Led calm incident response with clear ownership.")
            assert "Led incidents badly." in seen["prompt"]

            code, body = _req(base, "/api/artifact/apply-rewrite", {
                "id": aid,
                "selected": "Led incidents badly.",
                "replacement": body["replacement"],
            })
            assert code == 200 and body["ok"] is True, (code, body)

        text = md.read_text(encoding="utf-8")
        assert "Led calm incident response with clear ownership." in text
        assert "Led incidents badly." not in text
        assert made.exists()
        con = store.connect(db)
        try:
            row = con.execute(
                "SELECT body FROM artifacts WHERE id=?", (aid,)).fetchone()
            assert "Led calm incident response" in row["body"]
        finally:
            con.close()


def test_artifact_preview_resolves_paths_relative_to_the_data_folder():
    """Docker records paths like data/documents/...; serving them must not
    depend on the process still having /data as its current directory."""
    with _lab() as (root, db, home):
        cv = root / "source-cv.md"
        cv.write_text("Rowan Ashby\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n",
            encoding="utf-8")
        made = root / "data" / "documents" / "role" / "CV.docx"
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_bytes(b"fake docx bytes")
        (made.parent / "CV.md").write_text("# CV\n\nDocker path works.\n",
                                           encoding="utf-8")
        con = store.connect(db)
        try:
            aid = store.add_artifact(
                con, "uid-one", "cv", "data/documents/role/CV.docx",
                body="stored fallback")
        finally:
            con.close()

        with _server(db, config_path=cfg) as base:
            code, body = _req(base, f"/artifact/{aid}", method="GET")
            assert code == 200, (code, body)
            assert "<h1>CV</h1>" in body, body
            assert "Docker path works." in body, body
            code, body = _req(base, f"/artifact/{aid}/download.docx", method="GET")
            assert code == 200, (code, body)
            assert body == "fake docx bytes", body


def test_dashboard_view_state_survives_refresh_and_artifact_round_trips():
    with _lab() as (root, db, home):
        cv = root / "source-cv.md"
        cv.write_text("Rowan Ashby\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n",
            encoding="utf-8")
        con = store.connect(db)
        try:
            made = root / "docs" / "CV.docx"
            made.parent.mkdir(parents=True, exist_ok=True)
            made.write_text("download me", encoding="utf-8")
            (made.parent / "CV.md").write_text("# Rowan Ashby\n",
                                               encoding="utf-8")
            aid = store.add_artifact(con, "uid-one", "cv", str(made),
                                     body="download me")
        finally:
            con.close()

        with _server(db, config_path=cfg) as base:
            code, page = _req(base, "/", method="GET")
            assert code == 200, (code, page)
            assert "jobRadar.dashboard.state.v1" in page, page
            assert "jobRadar.dashboard.return.v1" in page, page
            assert "location.hash.slice(1)" in page, page
            assert "history.replaceState" in page, page
            assert "writeState(); return;" in page, page

            code, preview = _req(base, f"/artifact/{aid}", method="GET")
            assert code == 200, (code, preview)
            assert "id='back-dashboard'" in preview, preview
            assert "jobRadar.dashboard.return.v1" in preview, preview
            assert "saved.startsWith('/')" in preview, preview


def test_browser_generation_defaults_documents_beside_the_database():
    """A Docker run that mounts `/data` but does not pass `--docs` should
    still leave generated CVs in the mounted data tree."""
    with tempfile.TemporaryDirectory() as tmp, _env(
            JOB_RADAR_CLAUDE=str(Path(tmp) / "no-such-claude")):
        old_docs = os.environ.pop("JOB_RADAR_DOCS", None)
        root = Path(tmp)
        try:
            db = root / "data" / "job-radar.db"
            cv = root / "source-cv.md"
            cv.write_text("Rowan Ashby. Ran the release train for 3 years.\n",
                          encoding="utf-8")
            cfg = root / "config.yaml"
            cfg.write_text(
                "titles:\n  include: [Engineering Manager]\n"
                "locations:\n  countries: [UK]\n"
                "cv:\n  path: " + json.dumps(str(cv)) + "\n"
                "ai:\n"
                "  provider: anthropic\n"
                "  model: \"claude-sonnet-5\"\n"
                "  anthropic_api_key: \"sk-ant-api03-test\"\n",
                encoding="utf-8")
            con = store.connect(db)
            try:
                con.execute(
                    "INSERT INTO roles (uid,company,title,url,description,"
                    "first_seen,last_seen) VALUES "
                    "('uid-one','Tidewater Optics','Head of Platform Engineering',"
                    "'https://example.invalid/1',?,date('now'),date('now'))",
                    (LONG_DESC,))
                job_id = store.enqueue(con, "uid-one", "cv")
            finally:
                con.close()

            answer = (
                "===== BEGIN FILE: CV.md =====\n"
                "# Rowan Ashby\n\nRan the release train for 3 years.\n"
                "===== END FILE: CV.md =====\n"
                "===== BEGIN FILE: cv-rating.txt =====\n"
                "68/100\n"
                "===== END FILE: cv-rating.txt =====\n")
            with mock.patch("jobradar.ai.complete", lambda *a, **k: answer), \
                    mock.patch("jobradar.runner._quality",
                               lambda *a, **k: (True, [], {"slop": 99})), \
                    mock.patch("jobradar.runner._gates", lambda *a, **k: {}):
                runner.run_job(job_id, db_path=db, config_path=cfg)

            con = store.connect(db)
            try:
                art = con.execute(
                    "SELECT path FROM artifacts WHERE kind='cv'").fetchone()
            finally:
                con.close()
            assert art is not None
            path = Path(art["path"])
            assert path.exists(), path
            assert path.is_relative_to(root / "data" / "documents"), path
        finally:
            if old_docs is not None:
                os.environ["JOB_RADAR_DOCS"] = old_docs


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
        # Keyed to the worker thread, not to a call count, and settling on the
        # lock rather than on the row. Both faults are the ones its rank twin
        # was fixed for, and this one kept them: `store.connect` is a module
        # global, so a counter hands the injected failure to whichever caller
        # arrives first, and `spawn` marks the row failed before it releases
        # the lock, so waiting on the row leaves a window the assertion below
        # can land in. Green on four runners and lost on a loaded Windows one.
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
                runner.spawn(job, db_path=str(db))
                _settle(lambda: con.execute(
                    "SELECT state FROM jobs WHERE id=?", (job,)
                ).fetchone()["state"] != "pending"
                    and con.execute(
                        "SELECT 1 FROM locks WHERE name='generate'"
                    ).fetchone() is None)
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
                # Wait for the thing this test actually asserts, not for a
                # proxy for it. `_spawn_rank` sets rank_state to idle and then
                # releases the lock, so settling on the state alone leaves a
                # window where the state has flipped and the lock has not been
                # given back. Wide enough to lose on a loaded Windows runner
                # and never on this machine, which is how it failed: green on
                # four runners of five, and then a PermissionError on the way
                # out because the assertion raised before the connection was
                # closed and Windows will not delete an open file.
                _settle(lambda: store.get_meta(con, "rank_state") == "idle"
                        and con.execute(
                            "SELECT 1 FROM locks WHERE name='rank'"
                        ).fetchone() is None)
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
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, body = _req(base, "/api/generate",
                              {"uid": "uid-one", "kind": "screen"})
            assert code == 200 and body["ok"] is True, (code, body)
            _settle(lambda: "generation job" in out.getvalue())
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
        assert "failed" in out.getvalue()
        assert "claude" in out.getvalue()


def test_generate_can_use_the_anthropic_api_without_the_claude_cli():
    with _lab() as (root, db, home), _env(
            JOB_RADAR_CLAUDE=str(root / "no-such-claude")):
        cv = root / "cv.md"
        cv.write_text("Rowan Ashby\nEngineering leader.\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n"
            "ai:\n"
            "  provider: anthropic\n"
            "  model: \"claude-sonnet-5\"\n"
            "  anthropic_api_key: \"sk-ant-api03-test\"\n",
            encoding="utf-8")

        answer = (
            "===== BEGIN FILE: screening.md =====\n"
            "APPLY, strong match.\n\nNo dealbreakers found.\n"
            "===== END FILE: screening.md =====\n"
            "===== BEGIN FILE: verdict.txt =====\n"
            "APPLY\n"
            "===== END FILE: verdict.txt =====\n")
        with mock.patch("jobradar.ai.complete", lambda *a, **k: answer), \
                _server(db, docs=root / "docs", config_path=cfg) as base:
            code, body = _req(base, "/api/generate",
                              {"uid": "uid-one", "kind": "screen"})
            assert code == 200 and body["ok"] is True, (code, body)

            con = store.connect(db)
            try:
                _settle(lambda: con.execute(
                    "SELECT state FROM jobs WHERE id=?",
                    (body["job"],)).fetchone()["state"] == "done")
                art = con.execute(
                    "SELECT summary,body FROM artifacts WHERE kind='screen'"
                ).fetchone()
                assert art["summary"] == "APPLY", dict(art)
                assert "No dealbreakers" in art["body"], dict(art)
            finally:
                con.close()


def test_generation_prompt_includes_a_saved_screen_answer():
    with _lab() as (root, db, home), _env(
            JOB_RADAR_CLAUDE=str(root / "no-such-claude")):
        cv = root / "cv.md"
        cv.write_text("Rowan Ashby\nEngineering leader.\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n"
            "ai:\n"
            "  provider: anthropic\n"
            "  model: \"claude-sonnet-5\"\n"
            "  anthropic_api_key: \"sk-ant-api03-test\"\n",
            encoding="utf-8")
        con = store.connect(db)
        try:
            store.add_artifact(
                con, "uid-one", "screen_answer",
                body="I have led ITIL-aligned incident reviews.")
        finally:
            con.close()

        seen = {}
        answer = (
            "===== BEGIN FILE: screening.md =====\n"
            "APPLY, strong match.\n\nThe added evidence answers the gap.\n"
            "===== END FILE: screening.md =====\n"
            "===== BEGIN FILE: verdict.txt =====\n"
            "APPLY\n"
            "===== END FILE: verdict.txt =====\n")

        def complete(prompt, *a, **k):
            seen["prompt"] = prompt
            return answer

        with mock.patch("jobradar.ai.complete", complete), \
                _server(db, docs=root / "docs", config_path=cfg) as base:
            code, body = _req(base, "/api/generate",
                              {"uid": "uid-one", "kind": "screen"})
            assert code == 200 and body["ok"] is True, (code, body)
            con = store.connect(db)
            try:
                _settle(lambda: con.execute(
                    "SELECT state FROM jobs WHERE id=?",
                    (body["job"],)).fetchone()["state"] == "done")
            finally:
                con.close()
        assert "===== BEGIN LOCAL FILE: role-evidence.txt =====" in seen["prompt"]
        assert "I have led ITIL-aligned incident reviews." in seen["prompt"]


def test_generation_prompt_includes_approved_profile_evidence():
    with _lab() as (root, db, home), _env(
            JOB_RADAR_CLAUDE=str(root / "no-such-claude")):
        cv = root / "cv.md"
        cv.write_text("Rowan Ashby\nEngineering leader.\n", encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            "titles:\n  include: [Engineering Manager]\n"
            "locations:\n  countries: [UK]\n"
            "cv:\n  path: " + json.dumps(str(cv)) + "\n"
            "ai:\n"
            "  provider: anthropic\n"
            "  model: \"claude-sonnet-5\"\n"
            "  anthropic_api_key: \"sk-ant-api03-test\"\n",
            encoding="utf-8")
        con = store.connect(db)
        try:
            store.add_candidate_evidence(
                con, "Incident command", "Led major incident response.",
                category="incident_management", status="approved")
            store.add_candidate_evidence(
                con, "Search preference", "Prefer remote-first employers.",
                category="preference", status="approved")
            store.add_candidate_evidence(
                con, "Search constraint", "Cannot relocate outside the UK.",
                category="constraint", status="approved")
            store.add_candidate_keywords(
                con, "Operations", ["incident management", "root cause analysis"],
                status="approved")
            store.set_personal_info(con, {"name": "Rowan Ashby"})
        finally:
            con.close()

        seen = {}
        answer = (
            "===== BEGIN FILE: screening.md =====\nAPPLY\n===== END FILE: screening.md =====\n"
            "===== BEGIN FILE: verdict.txt =====\nAPPLY\n===== END FILE: verdict.txt =====\n")

        def complete(prompt, *a, **k):
            seen["prompt"] = prompt
            return answer

        with mock.patch("jobradar.ai.complete", complete), \
                _server(db, docs=root / "docs", config_path=cfg) as base:
            code, body = _req(base, "/api/generate",
                              {"uid": "uid-one", "kind": "screen"})
            assert code == 200 and body["ok"] is True, (code, body)
            con = store.connect(db)
            try:
                _settle(lambda: con.execute(
                    "SELECT state FROM jobs WHERE id=?",
                    (body["job"],)).fetchone()["state"] == "done")
            finally:
                con.close()
        assert "===== BEGIN LOCAL FILE: candidate-profile.txt =====" in seen["prompt"]
        assert "Led major incident response." in seen["prompt"]
        assert "Prefer remote-first employers." not in seen["prompt"]
        assert "Cannot relocate outside the UK." not in seen["prompt"]
        assert "incident management, root cause analysis" in seen["prompt"]
        assert "Rowan Ashby" in seen["prompt"]


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
            # Was `AttributeError`, which pinned a bug rather than a
            # behaviour: a `titles:` written as a list reached `load` and
            # raised `'list' object has no attribute 'get'`. It is a
            # ConfigError naming the key now, and the point of this test is
            # that the reason reaches the user at all, not which exception
            # class carried it.
            assert "ConfigError" in row["error"], row["error"]
            assert "titles" in row["error"], row["error"]
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
