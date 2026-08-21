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
