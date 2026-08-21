"""A small local server so the dashboard can be worked from, not just read.

Standard library only: `http.server` and `sqlite3`. It binds to 127.0.0.1 and
runs while you are triaging, then stops. It is not a daemon and it is not
something to expose.

`scan` still writes the static file. This renders the same data with the
buttons live, from the same database, so the two cannot disagree.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html as _h
from pathlib import Path
from urllib.parse import urlparse

from . import runner, store
from .output import interactive


class Handler(BaseHTTPRequestHandler):
    db_path = None
    docs_base = None
    config_path = None

    # ------------------------------------------------------------- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def log_message(self, *a):
        pass                      # the scan output is the interesting log

    # ------------------------------------------------------------- routing
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            con = store.connect(self.db_path)
            try:
                return self._html(interactive.render(con))
            finally:
                con.close()
        if path == "/api/rank":
            # The estimate, plus whatever a run in progress has finished. The
            # click has to be able to show a cost before spending anything.
            con = store.connect(self.db_path)
            try:
                from . import rank as rank_mod
                rows = rank_mod.candidates(con)
                batches, tokens = rank_mod.estimate(rows)
                return self._json({
                    "pending": len(rows), "batches": batches, "tokens": tokens,
                    "screen_tokens": len(rows) * 60_000,
                    "state": store.get_meta(con, "rank_state", "idle"),
                    "done": max(
                        int(store.get_meta(con, "rank_done", "0") or 0),
                        con.execute("SELECT COUNT(*) c FROM roles WHERE fit>=0")
                           .fetchone()["c"]
                        - int(store.get_meta(con, "rank_base", "0") or 0)),
                    "total": int(store.get_meta(con, "rank_total", "0") or 0),
                    "batch_size": rank_mod.BATCH,
                    "elapsed": _rank_elapsed(con),
                    "stopping": store.get_meta(con, "rank_cancel", "") == "1",
                    "error": store.get_meta(con, "rank_error", "") or "",
                    "scored": con.execute(
                        "SELECT COUNT(*) c FROM roles WHERE fit>=0").fetchone()["c"],
                    "unrankable": rank_mod.unrankable(con),
                })
            finally:
                con.close()

        if path == "/api/jobs":
            con = store.connect(self.db_path)
            try:
                # Two things were wrong with the obvious version of this
                # comparison, and together they made a failed job re-assert
                # its error onto the row for the rest of the day. So after a
                # fix, the dashboard kept showing the old failure and it
                # looked like nothing had been fixed.
                #
                #   * finished_at is written by Python as "...T13:59:13" while
                #     SQLite's datetime() returns "... 13:59:13". Comparing
                #     them as strings compares "T" against " ", and "T" always
                #     wins, so every job finished today looked recent.
                #   * datetime('now') is UTC; the stored value is local.
                rows = con.execute(
                    "SELECT id,uid,kind,state,error FROM jobs "
                    "WHERE state IN ('pending','running') OR "
                    "replace(finished_at,'T',' ') > "
                    "datetime('now','localtime','-2 minutes')").fetchall()
                arts = con.execute(
                    "SELECT uid,kind,path,rating,summary FROM artifacts").fetchall()
                states = con.execute("SELECT uid,status FROM role_state").fetchall()
                return self._json({
                    "jobs": [dict(r) for r in rows],
                    "artifacts": [dict(r) for r in arts],
                    "states": {r["uid"]: r["status"] for r in states},
                })
            finally:
                con.close()
        if path.startswith("/open"):
            # Reveal a generated document in Finder rather than serving it.
            import subprocess
            from urllib.parse import parse_qs, unquote
            q = parse_qs(urlparse(self.path).query)
            target = (q.get("path") or [""])[0]
            p = Path(unquote(target)) if target else None
            if p and not p.exists():
                # The file moved or was deleted. The text is in the database,
                # so show that rather than telling someone a document they
                # paid for is gone.
                con = store.connect(self.db_path)
                try:
                    row = con.execute(
                        "SELECT body,kind FROM artifacts WHERE path=? AND "
                        "COALESCE(body,'')<>'' ORDER BY id DESC LIMIT 1",
                        (str(p),)).fetchone()
                finally:
                    con.close()
                if row:
                    return self._html(
                        "<pre style='white-space:pre-wrap;font:14px/1.6 "
                        "ui-monospace,monospace;max-width:44rem;margin:3rem auto;"
                        "padding:0 1.5rem'>"
                        f"<b>{_h.escape(p.name)} is no longer on disk. This is "
                        f"the copy kept in the database.</b>\n\n"
                        f"{_h.escape(row['body'])}</pre>")
            if p and p.exists():
                # check=False does not suppress FileNotFoundError, so on a
                # machine without `open` this raised inside the handler and
                # dropped the connection.
                import sys as _sys
                cmds = ([["open", "-R", str(p)]] if _sys.platform == "darwin"
                        else [["xdg-open", str(p.parent)], ["explorer", str(p.parent)]])
                for c in cmds:
                    try:
                        subprocess.run(c, check=False)
                        return self._json({"ok": True})
                    except (FileNotFoundError, OSError):
                        continue
                return self._json({"ok": False,
                                   "error": f"could not reveal it; the file is at {p}"})
            return self._json({"ok": False, "error": "not found"}, 404)
        self.send_error(404)

    def _same_origin(self) -> bool:
        """Reject cross-site posts.

        This server spends money. Without a check, any page open in the same
        browser could POST to /api/generate, and a text/plain body is a simple
        request so there is no preflight to stop it.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True                      # curl and same-origin form posts
        host = self.headers.get("Host", "")
        return origin.split("//")[-1] == host

    def do_POST(self):
        if not self._same_origin():
            return self._json({"ok": False, "error": "cross-origin request refused"}, 403)
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/pull":
            # `git pull` on the checkout the server is running from. Read-only
            # in the sense that matters: it fetches and fast-forwards, and it
            # is refused outright if the working tree has changes, because a
            # button that silently merges over someone's edits is worse than
            # no button. Nothing is pushed and no history is rewritten.
            import subprocess
            repo = Path(__file__).resolve().parent.parent

            def git(*a, timeout=90):
                return subprocess.run(["git", "-C", str(repo), *a],
                                      capture_output=True, text=True, encoding="utf-8",
                                      timeout=timeout)

            if not (repo / ".git").exists():
                return self._json(
                    {"ok": False,
                     "error": "this is not a git checkout, so there is nothing "
                              "to pull. Re-download the source list by hand."}, 409)
            dirty = git("status", "--porcelain").stdout.strip()
            if dirty:
                return self._json(
                    {"ok": False,
                     "error": "you have uncommitted changes here, so this will "
                              "not merge over them. Commit or stash, then pull "
                              "from a terminal."}, 409)
            before = git("rev-parse", "HEAD").stdout.strip()
            r = git("pull", "--ff-only")
            if r.returncode:
                return self._json(
                    {"ok": False,
                     "error": (r.stderr or r.stdout).strip()[:300] or "pull failed"},
                    409)
            after = git("rev-parse", "HEAD").stdout.strip()
            from . import sources as src_mod
            if before == after:
                return self._json({"ok": True, "changed": False,
                                   "message": "already up to date"})
            n = git("diff", "--shortstat", before, after, "--",
                    "sources/sources.json").stdout.strip()
            return self._json({
                "ok": True, "changed": True,
                "message": f"pulled. source list {n or 'unchanged'}",
                "age": src_mod.age_days()})

        if path == "/api/rank/stop":
            con = store.connect(self.db_path)
            try:
                if store.get_meta(con, "rank_state", "idle") != "running":
                    return self._json({"ok": False, "error": "not running"}, 409)
                store.set_meta(con, "rank_cancel", "1")
            finally:
                con.close()
            # It stops between batches, not mid-request, so say so rather than
            # letting the button imply it halted instantly.
            return self._json({"ok": True,
                               "message": "stopping after the current batch"})

        if path == "/api/rank":
            # Not per-role, so it does not carry a uid and must be handled
            # before the uid check below.
            con = store.connect(self.db_path)
            try:
                if store.get_meta(con, "rank_state", "idle") == "running":
                    return self._json(
                        {"ok": False, "error": "already ranking"}, 429)
                from . import rank as rank_mod
                rows = rank_mod.candidates(con, refresh=bool(data.get("refresh")))
                if not rows:
                    return self._json(
                        {"ok": False,
                         "error": "every role with a description already has a "
                                  "fit score"}, 409)
                from datetime import datetime
                store.set_meta(con, "rank_state", "running")
                store.set_meta(con, "rank_total", str(len(rows)))
                store.set_meta(con, "rank_done", "0")
                store.set_meta(con, "rank_cancel", "")
                store.set_meta(con, "rank_error", "")
                # Where the scored count stood before this run, so progress can
                # be read live off the database rather than only advancing when
                # a batch finishes.
                store.set_meta(con, "rank_base", str(con.execute(
                    "SELECT COUNT(*) c FROM roles WHERE fit>=0").fetchone()["c"]))
                store.set_meta(con, "rank_started",
                               datetime.now().isoformat(timespec="seconds"))
            finally:
                con.close()
            _spawn_rank(self.db_path, self.config_path,
                        refresh=bool(data.get("refresh")))
            return self._json({"ok": True, "roles": len(rows)})

        uid = data.get("uid")
        # An unknown uid used to raise inside the handler and drop the
        # connection, so the browser's fetch rejected and the click silently
        # did nothing. A missing uid inserted a NULL row, because SQL foreign
        # keys ignore NULL.
        if not isinstance(uid, str) or not uid:
            return self._json({"ok": False, "error": "a role id is required"}, 400)
        con = store.connect(self.db_path)
        try:
            if not con.execute("SELECT 1 FROM roles WHERE uid=?", (uid,)).fetchone():
                return self._json({"ok": False, "error": "no such role"}, 404)
            if path == "/api/status":
                status = data.get("status", "")
                if status not in store.STATUSES:
                    return self._json({"ok": False, "error": "bad status"}, 400)
                store.set_status(con, uid, status, data.get("note"))
                return self._json({"ok": True, "uid": uid, "status": status})

            if path == "/api/generate":
                kind = data.get("kind")
                if kind not in runner.KINDS:
                    return self._json({"ok": False, "error": "bad kind"}, 400)
                # The cover letter needs the CV to check itself against.
                if kind == "cover_letter" and not store.has_artifact(con, uid, "cv"):
                    return self._json(
                        {"ok": False,
                         "error": "draft the CV first: the letter is checked "
                                  "against it for repeated phrasing"}, 409)
                if kind == "screen":
                    row = con.execute("SELECT description FROM roles WHERE uid=?",
                                      (uid,)).fetchone()
                    if len((row["description"] or "").strip()) < 200:
                        return self._json(
                            {"ok": False,
                             "error": "this posting has no description, so there "
                                      "is nothing to screen. Open the advert "
                                      "instead."}, 409)
                if store.running_count(con) >= 1:
                    return self._json(
                        {"ok": False, "error": "one generation at a time"}, 429)
                job_id = store.enqueue(con, uid, kind)
                runner.spawn(job_id, db_path=self.db_path, base=self.docs_base,
                             config_path=self.config_path)
                return self._json({"ok": True, "job": job_id, "kind": kind})
        finally:
            con.close()
        self.send_error(404)


def _rank_elapsed(con) -> int:
    """Seconds since the current rank run started, or 0.

    The counter only moves once per batch, about two minutes apart, so without
    a clock beside it a run in progress is indistinguishable from one that has
    hung.
    """
    from datetime import datetime
    stamp = store.get_meta(con, "rank_started", "")
    if not stamp:
        return 0
    try:
        return max(0, int((datetime.now() - datetime.fromisoformat(stamp)).total_seconds()))
    except ValueError:
        return 0


def _spawn_rank(db_path, config_path, refresh: bool = False) -> None:
    """Rank on a background thread so the click returns at once.

    Progress goes in `meta` rather than a job row: ranking is about the board
    rather than one role, and the jobs table has a foreign key to a role.
    """
    import threading

    def work():
        from . import rank as rank_mod
        from .config import load as load_cfg
        con = store.connect(db_path)
        try:
            cfg = load_cfg(config_path) if config_path else load_cfg()
            rows = rank_mod.candidates(con, refresh=refresh)
            rank_mod.rank(
                con, cfg, rows,
                on_batch=lambda done, total, scored:
                    store.set_meta(con, "rank_done", str(done)),
                should_stop=lambda: store.get_meta(con, "rank_cancel", "") == "1")
            store.set_meta(con, "rank_state", "idle")
            store.set_meta(con, "rank_cancel", "")
        except Exception as e:
            # Never leave it stuck on "running": the button would spin for
            # ever and refuse every later attempt.
            from .runner import LimitReached
            store.set_meta(con, "rank_state", "idle")
            store.set_meta(con, "rank_error", (
                f"stopped: out of credit or rate limited ({e}). Everything "
                f"scored so far is saved; run it again when the limit resets "
                f"and it picks up where it left off."
                if isinstance(e, LimitReached) else str(e))[:300])
        finally:
            con.close()

    threading.Thread(target=work, daemon=True).start()


def serve(db_path=None, host="127.0.0.1", port=8765, open_browser=True,
          docs_base=None, config_path=None) -> int:
    # Gates are recomputed on start, so a fixed check corrects the rows it got
    # wrong rather than only applying to future runs.
    con = store.connect(db_path)
    try:
        # Documents made before there was a column for their text.
        store.backfill_bodies(con)
        # A generation cannot outlive the process that spawned it, so anything
        # still "running" here is from a server that is gone.
        orphans = store.reap_orphans(con, runner.TIMEOUT)
        if orphans:
            print(f"  cleared {orphans} interrupted generation(s)")
        # A rank runs on a thread inside this process, so one marked running
        # here belongs to a server that is gone. Left alone it wedges the
        # button for ever, because starting a rank refuses while one is
        # "running". Everything already scored is kept.
        if store.get_meta(con, "rank_state", "idle") == "running":
            store.set_meta(con, "rank_state", "idle")
            store.set_meta(con, "rank_cancel", "")
            print("  cleared an interrupted ranking run")
        n = runner.regate(con)
        if n:
            print(f"  rechecked {n} document(s)")
    finally:
        con.close()

    Handler.db_path = db_path
    Handler.docs_base = docs_base
    # Without this the runner resolved a config from its working directory, so
    # generation used whatever config.yaml happened to be in cwd rather than
    # the one passed on the command line.
    Handler.config_path = config_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"job-radar is at {url}")
    print("  buttons: screen, CV, cover letter, apply, skip")
    print("  nothing generates unless you click it. Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
