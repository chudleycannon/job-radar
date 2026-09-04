"""A small local server so the dashboard can be worked from, not just read.

Standard library only: `http.server` and `sqlite3`. It binds to 127.0.0.1 and
runs while you are triaging, then stops. It is not a daemon and it is not
something to expose.

`scan` still writes the static file. This renders the same data with the
buttons live, from the same database, so the two cannot disagree.
"""

from __future__ import annotations

import errno
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html as _h
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import ai
from . import profile as candidate_profile
from . import rank, runner, store
from .config import resolve as resolve_config
from .output import interactive
from .output import profile as profile_page
from .output import settings as settings_page
from .output import setup as setup_page

# Nothing this dashboard posts is large: the biggest body is a status plus a
# note. A Content-Length beyond this is a mistake, and reading it would be
# blocking on bytes that are not coming.
MAX_BODY = 1 << 20


def _log_error(msg: str) -> None:
    print(f"  ! {msg}", flush=True)


# The most roles one bulk click will take.
#
# Not a technical limit: `MAX_RUNNING` is what bounds what actually runs, and
# the rest queue. This is a guard against a mis-click on "select all" over a
# four thousand row board turning into four thousand paid agent runs.
_MIME = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8",
         ".txt": "text/plain; charset=utf-8",
         ".docx": ("application/vnd.openxmlformats-officedocument"
                   ".wordprocessingml.document")}


def _download_name(company: str, title: str, kind: str, suffix: str) -> str:
    """A filename you can find in a folder of downloads.

    Every generated CV is called CV.pdf on disk, which is fine in a directory
    named after the role and useless in ~/Downloads, where three of them
    become CV.pdf, CV (1).pdf and CV (2).pdf. The employer and the role go in
    front so the picker's own list is enough.
    """
    what = {"cv": "CV", "cover_letter": "Cover-letter",
            "screen": "Screening"}.get(kind, kind)
    def slug(s, n):
        s = re.sub(r"[^\w\s-]", "", s or "").strip()
        s = re.sub(r"[\s_]+", "-", s)
        return s[:n].strip("-")
    parts = [p for p in (slug(company, 28), slug(title, 40), what) if p]
    return "-".join(parts) + suffix


BULK_LIMIT = 40

_VALIDATION_LOCK = threading.Lock()
_VALIDATION_THREADS: dict[str, threading.Thread] = {}


def _validation_status(db_path) -> dict:
    con = store.connect(db_path)
    try:
        started = store.get_meta(con, "source_validation_started", "") or ""
        try:
            elapsed = max(0, int((datetime.now() -
                                  datetime.fromisoformat(started)).total_seconds()))
        except (TypeError, ValueError):
            elapsed = 0
        return {
            "state": store.get_meta(con, "source_validation_state", "idle"),
            "checked": store.get_meta(con, "source_validation_checked", ""),
            "done": int(store.get_meta(con, "source_validation_done", "0") or 0),
            "total": int(store.get_meta(con, "source_validation_total", "0") or 0),
            "elapsed": elapsed,
            "dead": int(store.get_meta(con, "source_validation_dead", "0") or 0),
            "unreachable": int(store.get_meta(con, "source_validation_unreachable", "0") or 0),
            "mismatch": int(store.get_meta(con, "source_validation_mismatch", "0") or 0),
            "error": store.get_meta(con, "source_validation_error", "") or "",
        }
    finally:
        con.close()


def _run_source_validation(db_path, config_path, data_home: Path) -> None:
    """Validate the bundled list without deleting from it, then record success."""
    from . import sources as src_mod

    report = data_home / "source-validation.json"
    cmd = [sys.executable, "-m", "jobradar.cli"]
    config = resolve_config(config_path)
    if config.exists():
        cmd.extend(["--config", str(config)])
    cmd.extend(["validate", "--file", str(src_mod.BUNDLED),
                "--report", str(report)])
    error = ""
    result = None
    try:
        total = len(src_mod.load_file(src_mod.BUNDLED))
        con = store.connect(db_path)
        try:
            store.set_meta(con, "source_validation_total", str(total))
            con.commit()
        finally:
            con.close()
        process = subprocess.Popen(
            cmd, cwd=str(data_home), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        tail = deque(maxlen=30)
        assert process.stdout is not None
        for line in process.stdout:
            tail.append(line.rstrip())
            match = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*$", line)
            if match:
                con = store.connect(db_path)
                try:
                    store.set_meta(con, "source_validation_done", match.group(1))
                    store.set_meta(con, "source_validation_total", match.group(2))
                    con.commit()
                finally:
                    con.close()
        returncode = process.wait()
        if returncode:
            error = "\n".join(tail).strip()[-1000:] or "validation failed"
        else:
            result = json.loads(report.read_text(encoding="utf-8"))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    con = store.connect(db_path)
    try:
        if error or not isinstance(result, dict):
            store.set_meta(con, "source_validation_state", "failed")
            store.set_meta(con, "source_validation_error",
                           error or "validation produced no report")
        else:
            rows = result.get("rows") or []
            store.set_meta(con, "source_validation_state", "complete")
            store.set_meta(con, "source_validation_checked",
                           str(result.get("checked") or "")[:10])
            store.set_meta(con, "source_validation_total",
                           str(result.get("total") or len(rows)))
            store.set_meta(con, "source_validation_done",
                           str(result.get("total") or len(rows)))
            store.set_meta(con, "source_validation_dead",
                           str(len(result.get("dead") or [])))
            store.set_meta(con, "source_validation_unreachable",
                           str(sum(r.get("verdict") == "unreachable" for r in rows)))
            store.set_meta(con, "source_validation_mismatch",
                           str(len(result.get("mismatch") or [])))
            store.set_meta(con, "source_validation_error", "")
        con.commit()
    finally:
        con.close()


def _start_source_validation(db_path, config_path, data_home: Path) -> bool:
    key = str(db_path)
    with _VALIDATION_LOCK:
        current = _VALIDATION_THREADS.get(key)
        if current and current.is_alive():
            return False
        con = store.connect(db_path)
        try:
            store.set_meta(con, "source_validation_state", "running")
            store.set_meta(con, "source_validation_error", "")
            store.set_meta(con, "source_validation_done", "0")
            store.set_meta(con, "source_validation_total", "0")
            store.set_meta(con, "source_validation_started", store._now())
            con.commit()
        finally:
            con.close()
        thread = threading.Thread(
            target=_run_source_validation,
            args=(db_path, config_path, data_home),
            name="job-radar-source-validation", daemon=True)
        _VALIDATION_THREADS[key] = thread
        thread.start()
        return True


class Handler(BaseHTTPRequestHandler):
    db_path = None
    docs_base = None
    config_path = None
    # The host `serve()` was asked to bind to, so `_expected_hosts` can accept
    # the name the browser will actually send. Empty means loopback only,
    # which is what every test and the default both want.
    bind_host = ""
    # `salary.currency` from the config, so the salary sort groups by the
    # currency the FLOOR is in rather than by whichever one happens to be
    # commonest on the board. Read once at startup: it cannot change while
    # the process runs, and re-reading it per request would put a file read
    # on the page load.
    home_currency = ""

    def _config_path(self) -> Path:
        return resolve_config(self.config_path)

    def _data_home(self) -> Path:
        return self._config_path().expanduser().resolve().parent

    # ------------------------------------------------------------- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self._answered = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self._answered = True
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, name: str = ""):
        body = path.read_bytes()
        ctype = (mimetypes.guess_type(str(path))[0]
                 or "application/octet-stream")
        self._answered = True
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{_safe_download_name(name or path.name)}"')
        self.end_headers()
        self.wfile.write(body)

    def _download_text(self, name: str, ctype: str, text: str):
        body = text.encode("utf-8")
        self._answered = True
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{_safe_download_name(name)}"')
        self.end_headers()
        self.wfile.write(body)

    def _markdown_page(self, title: str, text: str,
                       artifact_id: int | None = None, nav: str = ""):
        html = _markdown_document(title, text, artifact_id=artifact_id, nav=nav)
        return self._html(html)

    def _artifact_path(self, raw: str) -> Path:
        return _resolve_artifact_path(raw, self.db_path, self._data_home())

    def _body(self):
        """The posted JSON object, or {} if there is not one.

        Everything here is defensive for the same reason: an exception thrown
        out of a handler is not an error message, it is a dropped connection.
        The browser's `fetch` rejects, the click does nothing, and the page
        says nothing, which is indistinguishable from a button that is not
        wired up. So a body that is not an object, or a Content-Length that is
        not a number, becomes {} and the handler's own validation answers with
        a sentence.
        """
        raw = self.headers.get("Content-Length") or "0"
        try:
            n = int(raw)
        except ValueError:
            return {}
        if n <= 0 or n > MAX_BODY:
            return {}
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return {}
        # `json.loads('[]')` and `json.loads('"hi"')` are both valid JSON and
        # neither has `.get`. Reaching `data.get("uid")` on one raised
        # AttributeError inside the handler and dropped the connection.
        return data if isinstance(data, dict) else {}

    def log_message(self, *a):
        pass                      # the scan output is the interesting log

    # ------------------------------------------------------------- routing
    #
    # Every request runs inside this. Four separate defects here all had the
    # same shape and the same symptom -- a malformed body, an unhashable
    # `kind`, a note that was not a string, and a write that lost a race with
    # a scan -- and each one killed the connection instead of answering:
    # http.server writes no status line when a handler raises, so the
    # browser's `fetch` rejected and the page said nothing at all. A click
    # that did nothing and a click that failed looked identical, and the one
    # that failed had usually just lost a status change.
    #
    # Wrapped here rather than around each `do_*` so there is one place that
    # cannot be forgotten, and so a handler added later gets it for free.
    def handle_one_request(self):
        self._answered = False
        try:
            super().handle_one_request()
        except Exception as e:
            if getattr(self, "_answered", False):
                raise            # too late to answer; let the server log it
            locked = (isinstance(e, sqlite3.OperationalError)
                      and ("locked" in str(e).lower() or "busy" in str(e).lower()))
            if locked:
                # The one failure here with an obvious cause and an obvious
                # thing to do about it: a scan holds the write lock while it
                # updates the board, and 15 seconds was not long enough.
                msg = ("the database is busy, most likely a scan is writing "
                       "to it. Nothing was saved. Try again in a moment.")
                code = 503
            else:
                msg = f"{type(e).__name__}: {e}"[:300]
                code = 500
            _log_error(msg)
            self.close_connection = True
            try:
                self._json({"ok": False, "error": msg}, code)
            except OSError:
                pass             # the client went away mid-answer

    def do_GET(self):
        # Reads were exempt from this and should not have been.
        #
        # The Host check exists to stop DNS rebinding, and it was only on the
        # POSTs and on /open. So a page on evil.example that had rebound its
        # own name to 127.0.0.1 could not WRITE anything -- but it could ask
        # for `/` and `/api/jobs` same-origin, and read back the whole board:
        # every employer, every application status, the private note on each
        # role, the fit scores, the text of every screening, and the paths of
        # the generated documents under ~/job-applications. For someone job
        # hunting out of a current job that is the most sensitive thing this
        # tool holds, and it was the one page not behind the check.
        #
        # Verified by sending `Host: evil.example:PORT` to `/`: 200 and the
        # full dashboard before this, 403 after.
        if not self._same_origin():
            return self._json(
                {"ok": False, "error": "cross-origin request refused"}, 403)
        path = urlparse(self.path).path
        if path == "/setup":
            return self._html(setup_page.render(str(self._config_path())))
        if path == "/settings":
            return self._html(settings_page.render(str(self._config_path())))
        if path == "/profile":
            con = store.connect(self.db_path)
            try:
                note = _ensure_profile_import(con, self._config_path())
                return self._html(profile_page.render(
                    store.candidate_evidence(con), note,
                    store.custom_evidence_categories(con),
                    store.candidate_keywords(con),
                    store.personal_info(con)))
            finally:
                con.close()
        if path in ("/profile/export.md", "/profile/export.json"):
            con = store.connect(self.db_path)
            try:
                if path.endswith(".json"):
                    return self._download_text(
                        "candidate-profile.json", "application/json",
                        _profile_export_json(con))
                return self._download_text(
                    "candidate-profile.md", "text/markdown; charset=utf-8",
                    _profile_export_markdown(con))
            finally:
                con.close()
        if path == "/api/settings":
            ok, result = _read_ai_settings(self._config_path())
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)
        if path == "/api/scan":
            con = store.connect(self.db_path)
            try:
                done = int(store.get_meta(con, "scan_done", "0") or 0)
                total = int(store.get_meta(con, "scan_total", "0") or 0)
                return self._json({
                    "state": store.get_meta(con, "scan_state", "idle"),
                    "started": store.get_meta(con, "scan_started", ""),
                    "finished": store.get_meta(con, "scan_finished", ""),
                    "error": store.get_meta(con, "scan_error", ""),
                    "stopping": store.get_meta(con, "scan_cancel", "") == "1",
                    "stopped": store.get_meta(con, "scan_stopped", "") == "1",
                    "done": done,
                    "total": total,
                    "percent": int(done * 100 / total) if total else 0,
                    "responded": int(store.get_meta(con, "scan_responded", "0") or 0),
                    "postings": int(store.get_meta(con, "scan_postings", "0") or 0),
                    "phase": int(store.get_meta(con, "scan_phase", "0") or 0),
                    "phase_label": store.get_meta(con, "scan_phase_label", ""),
                })
            finally:
                con.close()
        if path == "/api/source-validation":
            return self._json(_validation_status(self.db_path))
        if path in ("/", "/index.html"):
            con = store.connect(self.db_path)
            try:
                if _needs_setup(con, self._config_path()):
                    return self._html(setup_page.render(str(self._config_path())))
                _ensure_profile_import(con, self._config_path())
                return self._html(
                    interactive.render(con, self.home_currency))
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
                    "screen_tokens": len(rows) * rank.SCREEN_TOKENS,
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
                    # `started_at` travels so the browser can show elapsed
                    # time. A spinner with no clock on an eight minute job is
                    # the same failure as a page that paints nothing for a
                    # second: it reads as hung, and the reader kills work that
                    # was going fine.
                    "SELECT id,uid,kind,state,error,started_at FROM jobs "
                    "WHERE state IN ('pending','running') OR "
                    "replace(finished_at,'T',' ') > "
                    "datetime('now','localtime','-2 minutes')").fetchall()
                arts = con.execute(
                    "SELECT uid,kind,path,rating,summary FROM artifacts").fetchall()
                states = con.execute("SELECT uid,status FROM role_state").fetchall()
                return self._json({
                    "jobs": [dict(r) for r in rows],
                    # Per kind, and only the kinds actually in flight, so a
                    # quiet poll stays a small answer.
                    "typical": {k: store.typical_seconds(con, k)
                                for k in {r["kind"] for r in rows}},
                    "now": store._now(),
                    "artifacts": [dict(r) for r in arts],
                    "states": {r["uid"]: r["status"] for r in states},
                })
            finally:
                con.close()
        if path.startswith("/artifact/"):
            parts = [x for x in path.removeprefix("/artifact/").split("/") if x]
            raw_id = parts[0] if parts else ""
            download = parts[1] if len(parts) == 2 and parts[1].startswith("download.") else ""
            if not raw_id.isdigit() or len(parts) > 2 or (len(parts) == 2 and not download):
                return self._json({"ok": False, "error": "bad artifact"}, 400)
            con = store.connect(self.db_path)
            try:
                row = con.execute(
                    "SELECT id,uid,kind,path,body FROM artifacts WHERE id=?",
                    (int(raw_id),)).fetchone()
                nav = _review_nav(con, row["uid"], row["kind"], int(row["id"])) if row else ""
            finally:
                con.close()
            if not row:
                return self._json({"ok": False, "error": "not found"}, 404)
            p = self._artifact_path(row["path"] or "")
            if download:
                if row["kind"] not in ("cv", "cover_letter"):
                    return self._json({"ok": False, "error": "bad artifact"}, 400)
                md = _artifact_markdown_path(row["kind"], p)
                if download == "download.md":
                    if md.exists() and md.is_file():
                        try:
                            return self._file(md, md.name)
                        except OSError as exc:
                            _log_error(f"could not read artifact markdown {row['id']}: {exc}")
                    if (row["body"] or "").strip():
                        name = "CV.md" if row["kind"] == "cv" else "cover-letter.md"
                        return self._download_text(name, "text/markdown; charset=utf-8",
                                                   row["body"])
                    return self._json({"ok": False, "error": "not found"}, 404)
                if download == "download.docx":
                    docx = p if p.suffix.lower() == ".docx" else p.with_suffix(".docx")
                    if docx.exists() and docx.is_file():
                        try:
                            return self._file(docx, docx.name)
                        except OSError as exc:
                            _log_error(f"could not read artifact {row['id']}: {exc}")
                    return self._json({"ok": False, "error": "not found"}, 404)
                return self._json({"ok": False, "error": "bad artifact"}, 400)
            if row["kind"] in ("cv", "cover_letter"):
                md = _artifact_markdown_path(row["kind"], p)
                if md.exists() and md.is_file():
                    try:
                        return self._markdown_page(
                            md.name,
                            md.read_text(encoding="utf-8", errors="ignore"),
                            int(row["id"]), nav)
                    except OSError as exc:
                        _log_error(f"could not read artifact markdown {row['id']}: {exc}")
                if (row["body"] or "").strip():
                    return self._markdown_page(
                        "CV.md" if row["kind"] == "cv" else "cover-letter.md",
                        row["body"], int(row["id"]), nav)
            if p.exists() and p.is_file():
                try:
                    return self._file(p)
                except OSError as exc:
                    _log_error(f"could not read artifact {row['id']}: {exc}")
            if (row["body"] or "").strip():
                return self._html(
                    "<pre style='white-space:pre-wrap;font:14px/1.6 "
                    "ui-monospace,monospace;max-width:44rem;margin:3rem auto;"
                    "padding:0 1.5rem'>"
                    f"{_h.escape(row['body'])}</pre>")
            return self._json({"ok": False, "error": "not found"}, 404)
        if path.startswith("/role/"):
            parts = [x for x in path.removeprefix("/role/").split("/") if x]
            if len(parts) != 2 or parts[1] != "job-description":
                return self._json({"ok": False, "error": "bad role document"}, 400)
            uid = unquote(parts[0])
            con = store.connect(self.db_path)
            try:
                row = con.execute(
                    "SELECT uid,company,title,description FROM roles WHERE uid=?",
                    (uid,)).fetchone()
                nav = _review_nav(con, uid, "job_description", None) if row else ""
            finally:
                con.close()
            if not row:
                return self._json({"ok": False, "error": "not found"}, 404)
            title = "Job description"
            head = " - ".join(x for x in (row["company"], row["title"]) if x)
            text = f"# {title}\n\n"
            if head:
                text += f"**{head}**\n\n"
            text += (row["description"] or "_No stored job description._")
            return self._markdown_page(title, text, nav=nav)

        if path.startswith("/download"):
            # Serve the document as a download rather than revealing it in
            # Finder.
            #
            # `open -R` puts a Finder window in front of you, which is the
            # right answer when you want to look at the file and the wrong one
            # when you are about to attach it: Chrome's upload dialog does not
            # know about that window, so you are hunting through ninety
            # `2026-09-01-company-role-hash` folders for `CV.pdf`. A download
            # lands in ~/Downloads, which is where the picker already opens.
            #
            # The name matters as much as the route. Every generated CV is
            # called CV.pdf, so downloading three of them gives you CV.pdf,
            # CV (1).pdf and CV (2).pdf, and the picker's most useful column
            # tells you nothing. The employer and the role go in the filename.
            if not self._same_origin():
                return self._json(
                    {"ok": False, "error": "cross-origin request refused"}, 403)
            q = parse_qs(urlparse(self.path).query)
            p = Path(unquote((q.get("path") or [""])[0] or ""))
            con = store.connect(self.db_path)
            try:
                row = con.execute(
                    "SELECT a.kind, r.company, r.title FROM artifacts a "
                    "JOIN roles r ON r.uid = a.uid WHERE a.path=? LIMIT 1",
                    (str(p),)).fetchone()
            finally:
                con.close()
            # The same allowlist by construction as /open: the path has to be
            # one already recorded in `artifacts`, so no amount of traversal
            # in the query string reaches anything else on the disk.
            if not row:
                return self._json(
                    {"ok": False,
                     "error": "that is not a document this tool made"}, 403)
            if not p.exists():
                return self._json({"ok": False, "error": "not found"}, 404)
            try:
                blob = p.read_bytes()
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            name = _download_name(row["company"], row["title"], row["kind"],
                                  p.suffix)
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(p.suffix.lower(),
                                                       "application/octet-stream"))
            # ASCII only in the quoted form: a header is latin-1 and an
            # employer called "Nestlé" would raise inside the handler and drop
            # the connection. RFC 5987 carries the real one beside it.
            ascii_name = name.encode("ascii", "ignore").decode() or "document"
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(name)}")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        if path.startswith("/open"):
            # Checked like a POST. It is a GET, but it runs `open -R` on a
            # path from the query string, so any page in the browser could
            # have pointed it at anything on the disk.
            if not self._same_origin():
                return self._json(
                    {"ok": False, "error": "cross-origin request refused"}, 403)
            # Reveal a generated document in Finder rather than serving it.
            import subprocess
            q = parse_qs(urlparse(self.path).query)
            target = (q.get("path") or [""])[0]
            p = Path(unquote(target)) if target else None
            # Only documents this tool made. An allowlist by construction:
            # the path has to be one already recorded in `artifacts`, so no
            # amount of traversal in the query string reaches anything else.
            if p is not None:
                con = store.connect(self.db_path)
                try:
                    known = con.execute(
                        "SELECT 1 FROM artifacts WHERE path=? LIMIT 1",
                        (str(p),)).fetchone()
                finally:
                    con.close()
                if not known:
                    return self._json(
                        {"ok": False,
                         "error": "that is not a document this tool made"}, 403)
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

    def _expected_hosts(self) -> set[str]:
        """The names this server is allowed to be reached by.

        The loopback names, plus whatever `--host` was actually given. Without
        the last one, `job-radar serve --host 0.0.0.0` opened the browser at
        `http://0.0.0.0:8765/`, and every write from that page was refused with
        "cross-origin request refused" -- a flag that silently broke the
        buttons. It is not a hole: a rebinding attack needs the ATTACKER's
        name in Host, and that is a name the person running this never typed.
        """
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if self.bind_host:
            allowed.add(f"{self.bind_host}:{port}")
        return allowed

    def _same_origin(self) -> bool:
        """Reject cross-site posts, and reject rebinding.

        This server spends money. Without a check, any page open in the same
        browser could POST to /api/generate, and a text/plain body is a simple
        request so there is no preflight to stop it.

        Comparing Origin to Host was not enough. Both headers are attacker
        controlled together: a page on evil.example that has rebound its own
        DNS to 127.0.0.1 sends Origin: http://evil.example and Host:
        evil.example, they match, and the request went through to
        /api/generate. So Host is checked against what this server actually
        bound to, and Origin against the same set.
        """
        host = self.headers.get("Host", "")
        allowed = self._expected_hosts()
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True                      # curl and same-origin form posts
        return origin.split("//")[-1] in allowed

    def _start_generation(self, con, uid, kind):
        """Queue one role. Returns (job_id, "") or (None, reason).

        Shared by the single button and the bulk one so a role cannot be
        accepted by one path and refused by the other. Every check here is a
        refusal that costs nothing; the thing being guarded costs money.
        """
        if kind == "cover_letter" and not store.has_artifact(con, uid, "cv"):
            return None, ("draft the CV first: the letter is checked against "
                          "it for repeated phrasing")
        if kind in ("screen", "cv", "cover_letter"):
            # Not screen alone. A CV drafted against "_No description
            # available from this source._" is a full agent run, a rating, and
            # a document tailored to nothing.
            row = con.execute("SELECT description FROM roles WHERE uid=?",
                              (uid,)).fetchone()
            if row is None:
                return None, "no such role"
            if len((row["description"] or "").strip()) < 200:
                return None, ("this posting has no description, so there is "
                              "nothing to screen. Open the advert instead.")
        # Queue it, then let the pump decide what runs. The cap belongs on
        # what is RUNNING, not on what may be asked for: it used to sit here,
        # so nine selected roles became three started and six refused while
        # the dialog that took the click promised the rest would queue.
        #
        # `enqueue` is idempotent per role and kind, so a double-click returns
        # the same job rather than a second one writing the same folder.
        job_id = store.enqueue(con, uid, kind)
        con.commit()
        runner.pump(db_path=self.db_path, base=self.docs_base,
                    config_path=self.config_path)
        return job_id, ""

    def do_POST(self):
        if not self._same_origin():
            return self._json({"ok": False, "error": "cross-origin request refused"}, 403)
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/source-validation":
            started = _start_source_validation(
                self.db_path, self.config_path, self._data_home())
            if not started:
                return self._json(
                    {"ok": False, "error": "source validation is already running"},
                    409)
            return self._json({"ok": True, "state": "running",
                               "message": "validating sources in the background"})

        if path == "/api/setup":
            ok, result = _write_web_config(self._config_path(), data)
            if not ok:
                return self._json({"ok": False, "error": result}, 400)
            try:
                from .config import load as _load_cfg
                Handler.home_currency = (_load_cfg(result).salary_currency or "").upper()
            except Exception:
                Handler.home_currency = ""
            con = store.connect(self.db_path)
            try:
                note = _ensure_profile_import(con, Path(result))
            finally:
                con.close()
            return self._json({"ok": True, "path": str(result), "profile": note})

        if path == "/api/settings":
            ok, result = _write_ai_settings(self._config_path(), data)
            if not ok:
                return self._json({"ok": False, "error": result}, 400)
            return self._json({"ok": True, "path": str(result)})

        if path == "/api/profile/evidence":
            ok, result = _write_profile_evidence(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/evidence/delete":
            ok, result = _delete_profile_evidence(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/evidence/bulk":
            ok, result = _bulk_profile_evidence(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/personal":
            ok, result = _write_personal_info(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/category":
            ok, result = _write_profile_category(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/keywords":
            ok, result = _write_profile_keywords(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/profile/rebuild":
            ok, result = _rebuild_profile_from_cv(self.db_path, self._config_path())
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/artifact/rewrite":
            ok, result = _rewrite_artifact_selection(
                self.db_path, self._config_path(), data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/artifact/apply-rewrite":
            ok, result = _apply_artifact_rewrite(self.db_path, data)
            return self._json(result if ok else {"ok": False, "error": result},
                              200 if ok else 400)

        if path == "/api/scan":
            ok, result = _start_scan(self.db_path, self._config_path())
            if not ok:
                return self._json({"ok": False, "error": result}, 409)
            return self._json({"ok": True, "message": result})

        if path == "/api/scan/stop":
            con = store.connect(self.db_path)
            try:
                if store.get_meta(con, "scan_state", "idle") != "running":
                    return self._json({"ok": False, "error": "not running"}, 409)
                store.set_meta(con, "scan_cancel", "1")
            finally:
                con.close()
            return self._json({"ok": True,
                               "message": "stopping after the current requests"})

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
                # An atomic claim, not a read-then-decide: three parallel
                # requests all passed the old check and started three full
                # runs, which on a 300-role board is triple the spend.
                if not store.claim(con, "rank"):
                    return self._json(
                        {"ok": False, "error": "already ranking"}, 429)
                # Everything from here to the spawn has to give the lock back
                # if it throws. `candidates` is a query and the `set_meta`
                # calls are writes, so any of them can lose a race with a scan
                # and raise "database is locked" -- and a lock left taken
                # refuses every later rank with "already ranking" until the
                # server is restarted, which is the exact wedge `clear_locks`
                # exists to undo.
                try:
                    from . import rank as rank_mod
                    rows = rank_mod.candidates(
                        con, refresh=bool(data.get("refresh")))
                    if not rows:
                        store.release(con, "rank")
                        return self._json(
                            {"ok": False,
                             "error": "every role with a description already "
                                      "has a fit score"}, 409)
                    from datetime import datetime
                    store.set_meta(con, "rank_state", "running")
                    store.set_meta(con, "rank_total", str(len(rows)))
                    store.set_meta(con, "rank_done", "0")
                    store.set_meta(con, "rank_cancel", "")
                    store.set_meta(con, "rank_error", "")
                    # Where the scored count stood before this run, so progress
                    # can be read live off the database rather than only
                    # advancing when a batch finishes.
                    store.set_meta(con, "rank_base", str(con.execute(
                        "SELECT COUNT(*) c FROM roles WHERE fit>=0")
                        .fetchone()["c"]))
                    store.set_meta(con, "rank_started",
                                   datetime.now().isoformat(timespec="seconds"))
                except BaseException:
                    _abandon_rank(con)
                    raise
            finally:
                con.close()
            _spawn_rank(self.db_path, self.config_path,
                        refresh=bool(data.get("refresh")))
            return self._json({"ok": True, "roles": len(rows)})

        if path == "/api/generate/bulk":
            con = store.connect(self.db_path)
            try:
                # One request, many roles, and a per-role answer. A bulk
                # button that returns a single ok/failed is unusable: a
                # shortlist where two roles have no description and one is
                # already running has to say WHICH, or the reader has to open
                # forty rows to find out what actually started.
                kind = data.get("kind")
                if not isinstance(kind, str) or kind not in runner.KINDS:
                    return self._json({"ok": False, "error": "bad kind"}, 400)
                uids = data.get("uids")
                if not isinstance(uids, list) or not uids:
                    return self._json(
                        {"ok": False, "error": "no roles selected"}, 400)
                if len(uids) > BULK_LIMIT:
                    return self._json(
                        {"ok": False,
                         "error": f"{len(uids)} roles asked for at once; "
                                  f"{BULK_LIMIT} is the most this will take "
                                  f"in one go"}, 400)
                accepted, skipped = [], []
                for one in uids:
                    if not isinstance(one, str):
                        continue
                    job, why = self._start_generation(con, one, kind)
                    if job:
                        accepted.append(one)
                    else:
                        skipped.append({"uid": one, "why": why})
                running = len(store.busy_uids(con))
                return self._json({"ok": True, "kind": kind,
                                   "queued": accepted, "started": accepted,
                                   "running": running,
                                   "skipped": skipped})
            finally:
                con.close()

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
                if not isinstance(status, str) or status not in store.STATUSES:
                    return self._json({"ok": False, "error": "bad status"}, 400)
                # None means "not supplied, keep the note that is there"; a
                # string means "use this one, even if it is empty". Anything
                # else is neither, and sqlite refused to bind it: the request
                # died with no response and the click did nothing.
                note = data.get("note")
                if note is not None and not isinstance(note, str):
                    return self._json(
                        {"ok": False, "error": "a note has to be text"}, 400)
                store.set_status(con, uid, status, note)
                return self._json({"ok": True, "uid": uid, "status": status})

            if path == "/api/screen-answer":
                text = data.get("body")
                if not isinstance(text, str):
                    return self._json(
                        {"ok": False, "error": "an answer has to be text"}, 400)
                text = text.strip()
                if not text:
                    return self._json(
                        {"ok": False, "error": "write an answer first"}, 400)
                if len(text) > 20_000:
                    return self._json(
                        {"ok": False, "error": "answer is too long"}, 400)
                aid = store.add_artifact(
                    con, uid, "screen_answer", body=text,
                    summary=text.splitlines()[0][:120])
                suggested = candidate_profile.suggest_from_screen_answer(
                    con, uid, text)
                return self._json({"ok": True, "uid": uid, "artifact": aid,
                                   "suggested_evidence": suggested})

            if path == "/api/reset-outputs":
                counts = store.reset_role_outputs(con, uid)
                total = counts["artifacts"] + counts["jobs"]
                return self._json({"ok": True, "uid": uid, "cleared": total,
                                   **counts})

            if path == "/api/generate":
                kind = data.get("kind")
                # `kind not in runner.KINDS` hashes `kind`, so a list or a
                # dict raised TypeError rather than answering "bad kind".
                if not isinstance(kind, str) or kind not in runner.KINDS:
                    return self._json({"ok": False, "error": "bad kind"}, 400)
                # The cover letter needs the CV to check itself against.
                ok, why = self._start_generation(con, uid, kind)
                if not ok:
                    return self._json({"ok": False, "error": why},
                                      429 if "running" in why else 409)
                return self._json({"ok": True, "job": ok, "kind": kind})
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


def _safe_download_name(name: str) -> str:
    """A conservative Content-Disposition filename."""
    cleaned = re.sub(r'[^A-Za-z0-9._ -]+', "-", name).strip(" .")
    return cleaned or "document"


def _resolve_artifact_path(raw: str, db_path=None,
                           data_home: Path | None = None) -> Path:
    p = Path(raw or "")
    if p.is_absolute():
        return p
    candidates = [Path.cwd() / p]
    if data_home is not None:
        candidates.append(data_home / p)
    db = Path(db_path or store.DEFAULT_PATH).expanduser()
    if db.is_absolute():
        candidates.extend([db.parent / p, db.parent.parent / p])
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return p


def _artifact_markdown_path(kind: str, artifact_path: Path) -> Path:
    name = "CV.md" if kind == "cv" else "cover-letter.md"
    return artifact_path.with_name(name)


def _review_nav(con, uid: str, current: str, current_id: int | None = None) -> str:
    rows = con.execute(
        "SELECT id,kind FROM artifacts WHERE uid=? "
        "AND kind IN ('cv','cover_letter') ORDER BY id DESC",
        (uid,)).fetchall()
    latest = {}
    for r in rows:
        latest.setdefault(r["kind"], int(r["id"]))
    items = []
    for kind, label in (("cv", "CV"), ("cover_letter", "Cover letter")):
        aid = latest.get(kind)
        if not aid:
            continue
        active = (current_id == aid) or (current == kind and current_id is None)
        items.append(
            f"<a class='{'active' if active else ''}' href='/artifact/{aid}'>"
            f"{_h.escape(label)}</a>")
    jd_active = current == "job_description"
    items.append(
        f"<a class='{'active' if jd_active else ''}' "
        f"href='/role/{quote(uid, safe='')}/job-description'>Job description</a>")
    return "<div class='tabs'>" + "".join(items) + "</div>"


def _markdown_document(title: str, text: str, *,
                       artifact_id: int | None = None, nav: str = "") -> str:
    body = _markdown_body(text)
    panel = _rewrite_panel(artifact_id)
    css = """
    body{margin:0;background:#f7f5ef;color:#25211b;font:16px/1.58 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{max-width:760px;margin:0 auto;padding:48px 24px 72px;background:#fff;min-height:100vh;box-shadow:0 0 0 1px rgba(30,25,15,.08)}
    nav{max-width:760px;margin:0 auto;padding:18px 24px 0}
    a{color:#285f79;text-decoration:none}a:hover{text-decoration:underline}
    .tabs{max-width:760px;margin:14px auto 0;padding:0 24px;display:flex;gap:8px;flex-wrap:wrap}
    .tabs a{border:1px solid #d6cdbd;border-radius:6px;background:#fff;padding:7px 11px;font-size:14px}
    .tabs a.active{background:#285f79;color:#fff;border-color:#285f79}
    h1{font-size:30px;line-height:1.15;margin:0 0 22px}
    h2{font-size:21px;line-height:1.25;margin:30px 0 10px;border-top:1px solid #e7e0d2;padding-top:18px}
    h3{font-size:17px;margin:22px 0 8px}
    p{margin:0 0 12px}ul{margin:6px 0 16px 22px;padding:0}li{margin:0 0 7px}
    strong{font-weight:700}em{font-style:italic}
    .review{position:sticky;bottom:0;max-width:760px;margin:0 auto;background:#fffdf8;border-top:1px solid #e7e0d2;box-shadow:0 -8px 20px rgba(35,28,18,.08);padding:14px 24px}
    .review textarea{width:100%;box-sizing:border-box;border:1px solid #d6cdbd;border-radius:6px;padding:10px;font:14px/1.45 ui-sans-serif,system-ui;margin:7px 0;min-height:54px}
    .review .selected{font-size:13px;color:#60584d;max-height:68px;overflow:auto;background:#f7f1e5;border-radius:6px;padding:8px}
    .review .actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
    .review button{border:1px solid #cbbfae;background:#fff;border-radius:6px;padding:8px 11px;cursor:pointer}
    .review button.primary{background:#285f79;color:white;border-color:#285f79}
    .review button:disabled{opacity:.55;cursor:not-allowed}
    .review .msg{font-size:13px;color:#60584d}
    """
    script = _rewrite_script(artifact_id)
    back_script = """
    const back=document.querySelector('#back-dashboard');
    try{
      const saved=localStorage.getItem('jobRadar.dashboard.return.v1');
      if(back && saved && saved.startsWith('/')) back.setAttribute('href',saved);
    }catch(e){}
    """
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_h.escape(title)}</title><style>{css}</style></head>"
            f"<body><nav><a id='back-dashboard' href='/'>Dashboard</a></nav>"
            f"{nav}<main>{body}</main>{panel}<script>{back_script}{script}</script>"
            f"</body></html>")


def _rewrite_panel(artifact_id: int | None) -> str:
    if artifact_id is None:
        return ""
    return f"""
<section class="review" data-artifact="{artifact_id}">
  <div class="selected" id="selected-text">Select a sentence or paragraph to rewrite.</div>
  <textarea id="rewrite-instruction" placeholder="Optional: make it punchier, warmer, shorter, less formal..."></textarea>
  <textarea id="rewrite-replacement" placeholder="Suggested rewrite appears here. You can edit it before applying."></textarea>
  <div class="actions">
    <button class="primary" id="rewrite-button" type="button">Rewrite selection</button>
    <button id="apply-rewrite" type="button">Apply rewrite</button>
    <span class="msg" id="rewrite-msg"></span>
  </div>
</section>"""


def _rewrite_script(artifact_id: int | None) -> str:
    if artifact_id is None:
        return ""
    return r"""
const panel=document.querySelector('.review');
const selectedBox=document.querySelector('#selected-text');
const instruction=document.querySelector('#rewrite-instruction');
const replacement=document.querySelector('#rewrite-replacement');
const msg=document.querySelector('#rewrite-msg');
let selected='';
function setMsg(t){msg.textContent=t||'';}
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.error||'Request failed');
  return d;
}
document.addEventListener('selectionchange',()=>{
  const s=window.getSelection();
  const text=s ? s.toString().trim() : '';
  if(text && document.querySelector('main').contains(s.anchorNode)){
    selected=text;
    selectedBox.textContent=text;
    setMsg('');
  }
});
document.querySelector('#rewrite-button').addEventListener('click',async ()=>{
  if(!selected){setMsg('Select text in the document first.');return;}
  const b=document.querySelector('#rewrite-button');
  b.disabled=true; setMsg('Rewriting...');
  try{
    const d=await post('/api/artifact/rewrite',{id:+panel.dataset.artifact,selected,instruction:instruction.value});
    replacement.value=d.replacement||'';
    setMsg('Review the suggestion, then apply it if it works.');
  }catch(e){setMsg(e.message);}
  finally{b.disabled=false;}
});
document.querySelector('#apply-rewrite').addEventListener('click',async ()=>{
  if(!selected||!replacement.value.trim()){setMsg('Select text and create or enter a replacement first.');return;}
  const b=document.querySelector('#apply-rewrite');
  b.disabled=true; setMsg('Saving...');
  try{
    await post('/api/artifact/apply-rewrite',{id:+panel.dataset.artifact,selected,replacement:replacement.value});
    setMsg('Saved. Reloading preview...');
    setTimeout(()=>location.reload(),500);
  }catch(e){setMsg(e.message);}
  finally{b.disabled=false;}
});
"""


def _markdown_body(text: str) -> str:
    out: list[str] = []
    para: list[str] = []
    in_list = False

    def inline(s: str) -> str:
        s = _h.escape(s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
        return s

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush_para()
            close_list()
            continue
        if line.startswith("#"):
            flush_para()
            close_list()
            level = min(3, len(line) - len(line.lstrip("#")))
            label = line[level:].strip()
            if label:
                out.append(f"<h{level}>{inline(label)}</h{level}>")
            continue
        if line.startswith(("- ", "* ")):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:].strip())}</li>")
            continue
        para.append(line)
    flush_para()
    close_list()
    return "\n".join(out) or "<p>No content.</p>"


def _abandon_rank(con, error: str = "") -> None:
    """Put the rank button back, from whatever state a failure left it in.

    Called on every path that gives up after the lock was taken. Each write is
    separately guarded because this runs when something is already wrong with
    the database, and a second failure here must not stop the remaining
    writes: leaving `rank_state` on "running" or the lock in place is a wedge
    that lasts until the next restart.
    """
    for k, v in (("rank_state", "idle"), ("rank_cancel", ""),
                 ("rank_error", error[:300] if error else None)):
        if v is None:
            continue
        try:
            store.set_meta(con, k, v)
        except Exception:
            pass
    try:
        store.release(con, "rank")
    except Exception:
        pass


def _needs_setup(con, config_path: Path) -> bool:
    """A fresh web start should show setup instead of an empty board."""
    if config_path.exists():
        return False
    runs = int(store.get_meta(con, "runs", "0") or 0)
    roles = con.execute("SELECT COUNT(*) c FROM roles").fetchone()["c"]
    return runs == 0 and roles == 0


def _ensure_profile_import(con, config_path: Path) -> str:
    """Create proposed candidate evidence from the configured CV once."""
    if con.execute("SELECT COUNT(*) c FROM candidate_evidence").fetchone()["c"]:
        return ""
    if not config_path.exists():
        return ""
    try:
        from .config import load as load_cfg
        cv = load_cfg(config_path).cv_path
    except Exception as exc:
        return f"Candidate profile was not imported because the config could not be read: {exc}"
    if not cv:
        return ""
    try:
        n = candidate_profile.import_cv(con, cv)
    except Exception as exc:
        return f"Candidate profile was not imported from the CV: {exc}"
    return (f"Imported {n} proposed evidence item{'s' if n != 1 else ''} "
            "from your CV for review.") if n else ""


def _write_profile_evidence(db_path, data: dict) -> tuple[bool, dict | str]:
    """Create or update editable candidate evidence from the Profile page."""
    if not isinstance(data, dict):
        return False, "evidence has to be an object"
    fields = {
        "title": str(data.get("title") or "").strip(),
        "body": str(data.get("body") or "").strip(),
        "category": str(data.get("category") or "general").strip(),
        "employer": str(data.get("employer") or "").strip(),
        "role_title": str(data.get("role_title") or "").strip(),
        "date_range": str(data.get("date_range") or "").strip(),
        "source": str(data.get("source") or "").strip(),
        "status": str(data.get("status") or "proposed").strip(),
        "pinned": bool(data.get("pinned")),
        "needs_detail": bool(data.get("needs_detail")),
        "needs_metric": bool(data.get("needs_metric")),
    }
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = _list(tags)
    if not isinstance(tags, list):
        return False, "tags have to be text"
    fields["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if fields["status"] not in store.EVIDENCE_STATUSES:
        return False, "bad evidence status"
    con = store.connect(db_path)
    try:
        fields["category"] = store.normalize_evidence_category(
            fields["category"], con)
    finally:
        con.close()
    con = store.connect(db_path)
    try:
        raw_id = data.get("id")
        if raw_id:
            try:
                eid = int(raw_id)
            except (TypeError, ValueError):
                return False, "bad evidence id"
            if set(data.keys()) <= {"id", "status"}:
                update = {"status": fields["status"]}
            else:
                update = {k: v for k, v in fields.items()
                          if v or k in ("body", "tags", "status", "pinned",
                                        "needs_detail", "needs_metric")}
            store.update_candidate_evidence(con, eid, **update)
            return True, {"ok": True, "id": eid}
        if not fields["body"]:
            return False, "evidence body is required"
        eid = store.add_candidate_evidence(
            con, confidence=1.0, **fields)
        return True, {"ok": True, "id": eid}
    except (KeyError, ValueError) as exc:
        return False, str(exc)
    finally:
        con.close()


def _artifact_source(con, artifact_id: int, db_path=None) -> tuple[dict, Path, str] | None:
    row = con.execute(
        "SELECT id,uid,kind,path,body FROM artifacts WHERE id=?",
        (artifact_id,)).fetchone()
    if not row or row["kind"] not in ("cv", "cover_letter"):
        return None
    p = _resolve_artifact_path(row["path"] or "", db_path)
    md = _artifact_markdown_path(row["kind"], p)
    if md.exists() and md.is_file():
        text = md.read_text(encoding="utf-8", errors="ignore")
    else:
        text = row["body"] or ""
    return dict(row), md, text


def _rewrite_artifact_selection(db_path, config_path: Path,
                                data: dict) -> tuple[bool, dict | str]:
    if not isinstance(data, dict):
        return False, "rewrite request has to be an object"
    try:
        artifact_id = int(data.get("id"))
    except (TypeError, ValueError):
        return False, "bad artifact id"
    selected = str(data.get("selected") or "").strip()
    instruction = str(data.get("instruction") or "").strip()
    if len(selected) < 8:
        return False, "select a sentence or paragraph first"
    con = store.connect(db_path)
    try:
        src = _artifact_source(con, artifact_id, db_path)
    finally:
        con.close()
    if not src:
        return False, "not a CV or cover letter artifact"
    row, _md, document = src
    if selected not in document:
        return False, "the selected text is no longer in the document"
    try:
        from .config import load as load_cfg
        cfg = load_cfg(config_path)
        replacement = ai.complete(
            _rewrite_prompt(row["kind"], document, selected, instruction),
            cfg, timeout=120, max_tokens=800).strip()
    except Exception as exc:
        return False, f"rewrite failed: {exc}"
    replacement = _strip_rewrite_answer(replacement)
    if not replacement:
        return False, "rewrite failed: AI response was blank"
    return True, {"ok": True, "replacement": replacement}


def _apply_artifact_rewrite(db_path, data: dict) -> tuple[bool, dict | str]:
    if not isinstance(data, dict):
        return False, "rewrite request has to be an object"
    try:
        artifact_id = int(data.get("id"))
    except (TypeError, ValueError):
        return False, "bad artifact id"
    selected = str(data.get("selected") or "")
    replacement = str(data.get("replacement") or "").strip()
    if not selected.strip() or not replacement:
        return False, "selected text and replacement are required"
    con = store.connect(db_path)
    try:
        src = _artifact_source(con, artifact_id, db_path)
        if not src:
            return False, "not a CV or cover letter artifact"
        row, md, document = src
        if document.count(selected) != 1:
            return False, "the selected text is no longer unique in the document"
        updated = document.replace(selected, replacement, 1)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(updated, encoding="utf-8")
        docx = md.with_suffix(".docx")
        try:
            from .docx import markdown_to_docx
            markdown_to_docx(updated, docx)
        except Exception as exc:
            _log_error(f"could not regenerate docx for artifact {artifact_id}: {exc}")
        con.execute("UPDATE artifacts SET body=? WHERE id=?", (updated, artifact_id))
        return True, {"ok": True, "id": artifact_id}
    finally:
        con.close()


def _rewrite_prompt(kind: str, document: str, selected: str,
                    instruction: str) -> str:
    label = "CV" if kind == "cv" else "cover letter"
    extra = instruction or "Make it clearer, sharper and more natural."
    return f"""Rewrite the selected text from this {label}.

Return only the replacement text. Keep it truthful to the document, preserve
Markdown list markers if the selected text is a bullet, and do not add facts,
metrics, employers, tools or qualifications that are not already supported.

Requested change: {extra}

Selected text:
```
{selected}
```

Full document for context:
```
{document[:12000]}
```"""


def _strip_rewrite_answer(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip().strip('"')


def _write_profile_category(db_path, data: dict) -> tuple[bool, dict | str]:
    name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
    if not name:
        return False, "category name is required"
    con = store.connect(db_path)
    try:
        slug = store.add_custom_evidence_category(con, name)
        return True, {"ok": True, "category": slug}
    except ValueError as exc:
        return False, str(exc)
    finally:
        con.close()


def _delete_profile_evidence(db_path, data: dict) -> tuple[bool, dict | str]:
    try:
        eid = int(data.get("id")) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        return False, "bad evidence id"
    con = store.connect(db_path)
    try:
        if not store.delete_rejected_candidate_evidence(con, eid):
            return False, "only rejected evidence can be deleted"
        return True, {"ok": True, "id": eid}
    finally:
        con.close()


def _bulk_profile_evidence(db_path, data: dict) -> tuple[bool, dict | str]:
    if not isinstance(data, dict):
        return False, "bulk action has to be an object"
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        return False, "choose at least one evidence card"
    action = str(data.get("action") or "").strip()
    con = store.connect(db_path)
    try:
        if action in {"approve", "reject", "archive"}:
            status = {"approve": "approved", "reject": "rejected",
                      "archive": "archived"}[action]
            n = store.bulk_update_candidate_evidence(con, ids, status=status)
        elif action in {"pin", "unpin"}:
            n = store.bulk_update_candidate_evidence(
                con, ids, pinned=(action == "pin"))
        elif action in {"needs_detail", "clear_needs_detail"}:
            n = store.bulk_update_candidate_evidence(
                con, ids, needs_detail=(action == "needs_detail"))
        elif action in {"needs_metric", "clear_needs_metric"}:
            n = store.bulk_update_candidate_evidence(
                con, ids, needs_metric=(action == "needs_metric"))
        elif action == "delete_rejected":
            n = store.delete_rejected_candidate_evidence_many(con, ids)
        else:
            return False, "bad bulk action"
        return True, {"ok": True, "changed": n}
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    finally:
        con.close()


def _write_personal_info(db_path, data: dict) -> tuple[bool, dict | str]:
    if not isinstance(data, dict):
        return False, "personal info has to be an object"
    info = {
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "linkedin": data.get("linkedin", ""),
        "github": data.get("github", ""),
        "links": data.get("links", ""),
    }
    con = store.connect(db_path)
    try:
        store.set_personal_info(con, info)
        return True, {"ok": True}
    finally:
        con.close()


def _profile_export_json(con) -> str:
    return json.dumps({
        "personal_info": store.personal_info(con),
        "keywords": store.candidate_keywords(con),
        "evidence": store.candidate_evidence(con),
        "custom_evidence_categories": store.custom_evidence_categories(con),
    }, indent=2)


def _profile_export_markdown(con) -> str:
    parts = ["# Candidate Profile"]
    info = store.personal_info(con)
    if any(info.values()):
        parts.append("## Personal Info\n" + "\n".join(
            f"- {k.replace('_', ' ').title()}: {v}"
            for k, v in info.items() if v))
    keywords = store.candidate_keywords(con)
    if keywords:
        parts.append("## Core Expertise\n" + "\n".join(
            f"- {r['title']} ({r['status']}): {', '.join(r['keywords'])}"
            for r in keywords))
    evidence = store.candidate_evidence(con)
    if evidence:
        lines = []
        for r in evidence:
            flags = ", ".join(x for x in (
                "pinned" if r.get("pinned") else "",
                "needs detail" if r.get("needs_detail") else "",
                "needs metric" if r.get("needs_metric") else "") if x)
            meta = " · ".join(x for x in (
                r["category"], r["status"], r["employer"], r["date_range"], flags) if x)
            lines.append(f"### {r['title']}\n{meta}\n\n{r['body']}")
        parts.append("## Evidence\n" + "\n\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _rebuild_profile_from_cv(db_path, config_path: Path) -> tuple[bool, dict | str]:
    if not config_path.exists():
        return False, "Set up your search first."
    try:
        from .config import load as load_cfg
        cv = load_cfg(config_path).cv_path
    except Exception as exc:
        return False, f"could not read your config: {exc}"
    if not cv:
        return False, "No CV configured."
    con = store.connect(db_path)
    try:
        removed, removed_keywords = store.clear_candidate_profile(con)
        added = candidate_profile.import_cv(con, cv)
        return True, {
            "ok": True,
            "removed": removed,
            "removed_keywords": removed_keywords,
            "added": added,
            "message": (
                f"Rebuilt profile from CV: removed {removed} evidence items "
                f"and {removed_keywords} keyword groups, imported {added} "
                f"proposed evidence item{'s' if added != 1 else ''}.")
        }
    except Exception as exc:
        return False, str(exc)
    finally:
        con.close()


def _write_profile_keywords(db_path, data: dict) -> tuple[bool, dict | str]:
    if not isinstance(data, dict):
        return False, "keyword group has to be an object"
    title = str(data.get("title") or "").strip()
    raw = data.get("keywords") or []
    if isinstance(raw, str):
        keywords = _list(raw)
    elif isinstance(raw, list):
        keywords = [str(k).strip() for k in raw if str(k).strip()]
    else:
        return False, "keywords have to be text"
    status = str(data.get("status") or "approved").strip()
    if status not in store.EVIDENCE_STATUSES:
        return False, "bad keyword status"
    con = store.connect(db_path)
    try:
        raw_id = data.get("id")
        if raw_id:
            try:
                kid = int(raw_id)
            except (TypeError, ValueError):
                return False, "bad keyword group id"
            if set(data.keys()) <= {"id", "status"}:
                store.update_candidate_keywords(con, kid, status=status)
            else:
                store.update_candidate_keywords(
                    con, kid, title=title, keywords=keywords, status=status)
            return True, {"ok": True, "id": kid}
        kid = store.add_candidate_keywords(
            con, title, keywords, status=status, source="Manual")
        return True, {"ok": True, "id": kid}
    except (KeyError, ValueError) as exc:
        return False, str(exc)
    finally:
        con.close()


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").replace("\n", ",").split(",") if x.strip()]


def _write_web_config(path: Path, data: dict) -> tuple[bool, str | Path]:
    """Validate and write a browser-created config through the setup writer."""
    from . import setup_wizard
    from .config import ConfigError, load as load_cfg, _num
    from .state import atomic_write_text

    cv = Path(str(data.get("cv_path") or "").strip().strip("\"'")).expanduser()
    if not cv:
        return False, "CV path is required."
    if not cv.exists() or not cv.is_file():
        return False, f"No CV file at {cv}."

    titles = _list(data.get("titles_include"))
    if not titles:
        return False, "At least one job title is required."

    floor_raw = str(data.get("salary_floor") or "").strip()
    try:
        floor = int(_num(floor_raw, "salary.floor")) if floor_raw else None
    except (ConfigError, TypeError, ValueError) as exc:
        return False, str(exc)

    try:
        concurrency = int(str(data.get("concurrency") or "16").strip())
    except ValueError:
        return False, "fetch.concurrency is not a whole number. Write it plainly, like 16."

    answers = dict(setup_wizard.DEFAULTS)
    answers.update({
        "cv_path": str(cv.resolve()),
        "titles_include": titles,
        "titles_exclude": _list(data.get("titles_exclude")),
        "countries": [c.upper() for c in _list(data.get("countries"))],
        "remote_ok": bool(data.get("remote_ok", True)),
        "work_modes": ["remote"] if data.get("remote_only") else [],
        "relocate_to": [c.upper() for c in _list(data.get("relocate_to"))],
        "need_sponsorship": [c.upper() for c in _list(data.get("need_sponsorship"))],
        "exclude_locations": _list(data.get("exclude_locations")),
        "salary_floor": floor,
        "salary_currency": str(data.get("salary_currency") or "GBP").strip().upper(),
        "dealbreakers": {w: setup_wizard._word_pattern(w)
                         for w in _list(data.get("dealbreakers"))},
        "sectors": _list(data.get("sectors")),
        "source_countries": [c.upper() for c in _list(data.get("source_countries"))],
        "concurrency": concurrency,
    })

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.web-setup")
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    ai_match = re.search(r"(?ms)^ai:\n(?:^[ \t].*\n|^\s*$)*", old_text)
    try:
        setup_wizard.write_config(tmp, answers)
        if ai_match:
            text = _replace_top_level_block(
                tmp.read_text(encoding="utf-8"), "ai", ai_match.group(0))
            atomic_write_text(tmp, text)
        load_cfg(tmp)
        setup_wizard.write_config(path, answers)
        if ai_match:
            text = _replace_top_level_block(
                path.read_text(encoding="utf-8"), "ai", ai_match.group(0))
            atomic_write_text(path, text)
    except Exception as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False, str(exc)
    try:
        tmp.unlink()
    except OSError:
        pass
    return True, path


def _read_ai_settings(path: Path) -> tuple[bool, dict | str]:
    """Return AI settings without exposing saved secrets."""
    import yaml

    raw = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return False, str(exc)
    ai = raw.get("ai") if isinstance(raw, dict) else {}
    ai = ai if isinstance(ai, dict) else {}
    env_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    saved_key = bool(str(ai.get("anthropic_api_key") or "").strip())
    try:
        max_tokens = int(ai.get("max_tokens") or 4096)
    except (TypeError, ValueError):
        return False, "ai.max_tokens is not a whole number."
    return True, {
        "ok": True,
        "provider": str(ai.get("provider") or "claude_cli"),
        "model": str(ai.get("model") or "claude-sonnet-5"),
        "base_url": str(ai.get("base_url") or ""),
        "max_tokens": max_tokens,
        "anthropic_key_set": saved_key or env_key,
        "anthropic_key_source": "environment" if env_key and not saved_key else
                                "config" if saved_key else "",
    }


def _write_ai_settings(path: Path, data: dict) -> tuple[bool, str | Path]:
    """Write only the top-level `ai:` config block."""
    import yaml
    from .config import ConfigError, _ai_provider, _int
    from .state import atomic_write_text

    try:
        provider = _ai_provider(data.get("provider"))
        model = str(data.get("model") or "claude-sonnet-5").strip()
        base_url = str(data.get("base_url") or "").strip()
        if not model:
            return False, "ai.model is required."
        if base_url and not re.match(r"^https?://", base_url, re.I):
            return False, "ai.base_url must start with http:// or https://."
        max_tokens = _int(data.get("max_tokens"), "ai.max_tokens", 4096)
        if max_tokens < 256:
            return False, "ai.max_tokens should be at least 256."
    except (ConfigError, TypeError, ValueError) as exc:
        return False, str(exc)

    existing_key = ""
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            block = raw.get("ai") if isinstance(raw, dict) else {}
            if isinstance(block, dict):
                existing_key = str(block.get("anthropic_api_key") or "").strip()
        except Exception:
            existing_key = ""

    incoming = str(data.get("anthropic_api_key") or "").strip()
    key = "" if data.get("clear_anthropic_key") else (incoming or existing_key)
    text = path.read_text(encoding="utf-8") if path.exists() else "# job-radar config\n"
    block = _ai_block(provider, model, base_url, key, max_tokens)
    next_text = _replace_top_level_block(text, "ai", block)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.ai-settings")
    try:
        atomic_write_text(tmp, next_text)
        yaml.safe_load(tmp.read_text(encoding="utf-8"))
        atomic_write_text(path, next_text)
    except Exception as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False, str(exc)
    try:
        tmp.unlink()
    except OSError:
        pass
    return True, path


def _ai_block(provider: str, model: str, base_url: str, api_key: str,
              max_tokens: int) -> str:
    def q(v: str) -> str:
        return "\"" + str(v).replace("\\", "\\\\").replace("\"", "\\\"") + "\""
    return (
        "ai:\n"
        f"  provider: {provider}\n"
        f"  model: {q(model)}\n"
        f"  base_url: {q(base_url)}\n"
        f"  anthropic_api_key: {q(api_key)}\n"
        f"  max_tokens: {int(max_tokens)}\n"
    )


def _replace_top_level_block(text: str, key: str, block: str) -> str:
    pattern = re.compile(rf"(?ms)^{re.escape(key)}:\n(?:^[ \t].*\n|^\s*$)*")
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + block


def _start_scan(db_path, config_path: Path) -> tuple[bool, str]:
    """Run a scan from the browser, against the same paths setup wrote."""
    import threading
    from datetime import datetime
    from . import cli

    if not config_path.exists():
        return False, "Set up your search first."

    home = config_path.expanduser().resolve().parent
    db = str(db_path or home / "data" / "job-radar.db")
    con = store.connect(db)
    try:
        if not store.claim(con, "scan"):
            return False, "a scan is already running"
        store.set_meta(con, "scan_state", "running")
        store.set_meta(con, "scan_started", datetime.now().isoformat(timespec="seconds"))
        store.set_meta(con, "scan_finished", "")
        store.set_meta(con, "scan_error", "")
        store.set_meta(con, "scan_cancel", "")
        store.set_meta(con, "scan_stopped", "")
        store.set_meta(con, "scan_done", "0")
        store.set_meta(con, "scan_total", "0")
        store.set_meta(con, "scan_responded", "0")
        store.set_meta(con, "scan_postings", "0")
        store.set_meta(con, "scan_phase", "0")
        store.set_meta(con, "scan_phase_label", "")
    finally:
        con.close()

    class _Args:
        pass

    args = _Args()
    args.config = str(config_path)
    args.db = db
    args.state = str(home / "state" / "seen.json")
    args.out = str(home / "out")
    args.docs = None
    args.limit = 0
    args.dry_run = False
    args.no_enrich = False
    args.no_caffeine = False
    args.no_open = True

    progress_last = {"done": -10}

    def progress(update: dict) -> None:
        done = int(update.get("done") or 0)
        total = int(update.get("total") or 0)
        is_phase = bool(update.get("phase_label"))
        if not is_phase and done < total and done - progress_last["done"] < 10:
            return
        progress_last["done"] = done
        try:
            c = store.connect(db)
            try:
                for key, meta_key in (
                    ("done", "scan_done"),
                    ("total", "scan_total"),
                    ("responded", "scan_responded"),
                    ("postings", "scan_postings"),
                    ("phase", "scan_phase"),
                    ("phase_label", "scan_phase_label"),
                ):
                    store.set_meta(c, meta_key, update.get(key, ""))
            finally:
                c.close()
        except Exception:
            pass

    def should_stop() -> bool:
        c = store.connect(db)
        try:
            return store.get_meta(c, "scan_cancel", "") == "1"
        finally:
            c.close()

    args.progress = progress
    args.should_stop = should_stop

    def work():
        err = ""
        rc = 1
        stopped = False
        try:
            rc = cli.cmd_scan(args)
            try:
                c = store.connect(db)
                try:
                    stopped = store.get_meta(c, "scan_cancel", "") == "1"
                finally:
                    c.close()
            except Exception:
                stopped = False
            if rc:
                err = f"scan exited with status {rc}"
        except BaseException as exc:
            err = str(exc) or type(exc).__name__
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            c = store.connect(db)
            try:
                store.set_meta(c, "scan_state", "idle")
                store.set_meta(c, "scan_finished",
                               datetime.now().isoformat(timespec="seconds"))
                store.set_meta(c, "scan_error", err[:300])
                store.set_meta(c, "scan_stopped", "1" if stopped and not err else "")
                store.set_meta(c, "scan_cancel", "")
                store.release(c, "scan")
            finally:
                c.close()
            if err:
                _log_error(f"scan failed: {err[:300]}")

    threading.Thread(target=work, daemon=True).start()
    return True, "Scan started. Reload the dashboard as roles arrive."


def _spawn_rank(db_path, config_path, refresh: bool = False) -> None:
    """Rank on a background thread so the click returns at once.

    Progress goes in `meta` rather than a job row: ranking is about the board
    rather than one role, and the jobs table has a foreign key to a role.
    """
    import threading

    def work():
        from . import rank as rank_mod
        from .config import load as load_cfg
        try:
            con = store.connect(db_path)
        except Exception as e:
            # This sat outside the try below, so a connection that failed --
            # a scan holding the write lock, a full disk -- killed the thread
            # before any of the recovery underneath could run. `rank_state`
            # stayed "running" with no error text and the "rank" lock stayed
            # taken, so the dashboard showed a run in progress that would
            # never move and refused every later click with "already ranking"
            # for the life of the server. Same failure, one line higher up.
            try:
                with store.open_db(db_path) as c2:
                    _abandon_rank(c2, f"could not open the database: {e}")
            except Exception:
                pass
            raise
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
        except BaseException as e:
            # Never leave it stuck on "running": the button would spin for
            # ever and refuse every later attempt.
            #
            # `BaseException`, not `Exception`, and the comment above is the
            # reason. `rank._call` raises SystemExit when the `claude` binary
            # is missing, SystemExit is a BaseException, and it walked straight
            # past the handler that exists to stop exactly this. `rank_state`
            # then stayed "running" for the life of the database, the button
            # was disabled for ever, and no error text ever reached the page:
            # a missing binary bricked the dashboard. Whatever a worker raises,
            # the state gets cleared and the reader gets a sentence.
            from .runner import LimitReached
            store.set_meta(con, "rank_state", "idle")
            # A cancel flag left set would stop the next run before its first
            # batch, which reads as the button doing nothing at all.
            store.set_meta(con, "rank_cancel", "")
            # What was actually written, which is not always nothing. The
            # connection is in autocommit, so a failure part way through the
            # apply loop leaves real scores in the database, and saying "the
            # roles are unchanged" about them sends someone looking for a bug
            # that is not there. `rank` stamps the count onto the exception.
            saved = int(getattr(e, "scored", 0) or 0)
            if isinstance(e, LimitReached):
                msg = (f"stopped: out of credit or rate limited ({e}). "
                       f"Everything scored so far is saved; run it again when "
                       f"the limit resets and it picks up where it left off.")
            else:
                what = (f"{saved} role(s) were scored and saved before it "
                        f"stopped." if saved else
                        "Nothing was scored; the roles are unchanged.")
                msg = f"ranking failed: {e}. {what}"
            store.set_meta(con, "rank_error", msg[:300])
            _log_error(msg[:300])
            # Cleared first, then honoured. A KeyboardInterrupt or a
            # SystemExit still means what it says; `finally` below releases the
            # lock and closes the connection on the way out, and this runs on a
            # daemon thread where re-raising ends the thread and nothing else.
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            store.release(con, "rank")
            con.close()

    threading.Thread(target=work, daemon=True).start()


def already_serving(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """Whether something is already listening there.

    Asked before starting a dashboard rather than after, because binding a
    port that is taken raises out of `serve` before it prints anything useful,
    and the second copy would be racing the first for the same database.
    """
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.4)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


def open_in_background(db_path=None, host: str = "127.0.0.1", port: int = 8765,
                       docs_base=None, config_path=None) -> str | None:
    """Start a dashboard that outlives this process, and return its URL.

    A scan takes over an hour and the first pass is usable after five minutes,
    so the dashboard should be there when it becomes worth looking at rather
    than when the scan happens to end. Detached, because the scan has another
    seventy minutes to run and the person wants to click things now.

    Returns None and starts nothing if a dashboard is already up: they would
    contend for the same database, and the one that lost would print a SQLite
    traceback at somebody who had done nothing wrong.
    """
    import subprocess
    import sys

    if already_serving(host, port):
        return None
    cmd = [sys.executable, "-m", "jobradar.cli"]
    if config_path:
        cmd += ["-c", str(config_path)]
    cmd += ["serve", "--no-browser", "--host", host, "--port", str(port)]
    if db_path:
        cmd += ["--db", str(db_path)]
    if docs_base:
        cmd += ["--docs", str(docs_base)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return None
    # Wait for the bind rather than guessing at it, so the browser is not
    # opened at a port nothing is answering yet.
    import time
    for _ in range(40):
        if already_serving(host, port):
            return f"http://{host}:{port}/"
        time.sleep(0.25)
    return None


def serve(db_path=None, host="127.0.0.1", port=8765, open_browser=True,
          docs_base=None, config_path=None) -> int:
    # Take the port FIRST, before touching the database.
    #
    # Everything below assumes "I am starting, therefore no other server is
    # running", and clears interrupted generations, locks and rank state on
    # the strength of it. A launchd job with KeepAlive breaks that assumption
    # in the worst possible way: it retries every few seconds, loses the bind
    # to the server that is already up, and on the way to losing it reaps the
    # healthy server's work. Seven queued screenings were killed four seconds
    # after they started, by a process that never served a single request.
    #
    # A failed bind is the only reliable way to know another server owns this
    # database. So bind first, and if the port is taken, leave everything
    # alone and say so.
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        # A second `serve` in another window, or the last one still running,
        # is the ordinary way to hit this, and it came out as a nine-frame
        # socketserver traceback ending in "Address already in use", which
        # reads as a broken tool rather than as a port that is taken.
        if getattr(exc, "errno", None) in (errno.EADDRINUSE, errno.EACCES):
            why = ("is already in use, most likely by a `job-radar serve` "
                   "that is still running"
                   if exc.errno == errno.EADDRINUSE
                   else "needs privileges this process does not have")
            print(f"Port {port} {why}. Either stop that one, or start this "
                  f"one somewhere else with `--port {port + 1}`.", flush=True)
            return 1
        raise

    # Gates are recomputed on start, so a fixed check corrects the rows it got
    # wrong rather than only applying to future runs.
    con = store.connect(db_path)
    try:
        _prepare_start(con)
    finally:
        con.close()

    Handler.db_path = db_path
    Handler.docs_base = str(docs_base) if docs_base else str(
        runner.default_docs_base(db_path))
    Handler.bind_host = host or ""
    # Without this the runner resolved a config from its working directory, so
    # generation used whatever config.yaml happened to be in cwd rather than
    # the one passed on the command line.
    Handler.config_path = config_path
    # A config that will not load must not stop the dashboard starting: the
    # board and everything already on it are in the database, not the config.
    # Losing the sort grouping is a fair price; losing the page is not.
    try:
        from .config import load as _load_cfg
        Handler.home_currency = (
            _load_cfg(config_path).salary_currency or "").upper()
    except Exception:
        Handler.home_currency = ""
    url = f"http://{host}:{port}/"
    print(f"job-radar is at {url}", flush=True)
    print("  buttons: screen, CV, cover letter, apply, skip", flush=True)
    # Flushed, all three. Python block-buffers stdout when it is not a
    # terminal, so `job-radar serve > serve.log &` -- which is how anyone
    # running it in the background or under a supervisor starts it -- wrote
    # an empty file and kept it empty for the life of the process. A server
    # that is up and silent is indistinguishable from one that hung on the
    # bind, and the URL is the one thing the reader came for.
    print("  nothing generates unless you click it. Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def _prepare_start(con) -> None:
    # Documents made before there was a column for their text.
    store.backfill_bodies(con)
    # A generation cannot outlive the process that spawned it, so anything
    # still "running" here is from a server that is gone.
    orphans = store.reap_orphans(con, runner.TIMEOUT)
    if orphans:
        print(f"  cleared {orphans} interrupted generation(s)")
    # A rank runs on a thread inside this process, so one marked running here
    # belongs to a server that is gone. Left alone it wedges the button for
    # ever, because starting a rank refuses while one is "running". Everything
    # already scored is kept.
    if store.get_meta(con, "rank_state", "idle") == "running":
        store.set_meta(con, "rank_state", "idle")
        store.set_meta(con, "rank_cancel", "")
        print("  cleared an interrupted ranking run")
    if store.get_meta(con, "rank_error", ""):
        store.set_meta(con, "rank_error", "")
        print("  cleared a stale ranking error")
    # Same story for browser-started scans: they run on a background thread in
    # this process. A rebuild or Ctrl-C kills the thread but leaves durable
    # meta rows behind, and the next container should show the last completed
    # scan, not a ghost run from an older image.
    if store.get_meta(con, "scan_state", "idle") == "running":
        store.set_meta(con, "scan_state", "idle")
        store.set_meta(con, "scan_cancel", "")
        store.set_meta(con, "scan_error", "")
        store.set_meta(con, "scan_stopped", "1")
        print("  cleared an interrupted scan")
    # A lock outlives the process that took it, so a crash mid-run would
    # otherwise refuse every generation and every rank for ever.
    if store.clear_locks(con):
        print("  released locks held by a previous run")
    # Housekeeping, and housekeeping must never be the reason the dashboard
    # will not start. `regate` rewrites the quality gates on every stored
    # document, so it writes, and a write loses to anything else holding the
    # database: a scan, a rank, a second window. That raised straight out of
    # `serve` and the server never reached its bind, so a scan running in
    # another terminal meant the dashboard simply would not open, with a
    # SQLite traceback as the explanation.
    #
    # Observed doing exactly that. The gates it refreshes are already correct
    # on disk from when each document was written; re-checking them is an
    # upgrade path for documents written before the gate was fixed, and that
    # can wait for the next start.
    try:
        n = runner.regate(con)
        if n:
            print(f"  rechecked {n} document(s)", flush=True)
    except sqlite3.OperationalError as e:
        print(f"  skipped re-checking documents: {e}. "
              f"Something else is using the database.", flush=True)
