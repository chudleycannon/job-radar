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
                rows = con.execute(
                    "SELECT id,uid,kind,state,error FROM jobs "
                    "WHERE state IN ('pending','running') OR "
                    "finished_at > datetime('now','-2 minutes')").fetchall()
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
            if p and p.exists():
                subprocess.run(["open", "-R", str(p)], check=False)
                return self._json({"ok": True})
            return self._json({"ok": False, "error": "not found"}, 404)
        self.send_error(404)

    def do_POST(self):
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
