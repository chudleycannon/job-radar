"""The database. One place that knows about a role.

Replaces three stores that used to hold overlapping truths: the seen-set in
`state/seen.json`, the application history in `applications.local.yaml`, and
the last scan in `out/roles.json`. Two defects this week came from the same
fact living in two places, which is the whole argument for this file.

Stays local and gitignored: it holds application history, private notes and
paths to documents, and the repository is public.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PATH = Path("data/job-radar.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS roles (
  uid TEXT PRIMARY KEY,
  company TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  location TEXT DEFAULT '',
  city TEXT DEFAULT '',
  country TEXT DEFAULT '',
  work_mode TEXT DEFAULT 'unstated',
  sector TEXT DEFAULT '',
  platform TEXT DEFAULT '',
  department TEXT DEFAULT '',
  salary_min REAL, salary_max REAL, salary_currency TEXT,
  salary_period TEXT DEFAULT 'year',
  salary_confirmed INTEGER DEFAULT 0,
  salary_label TEXT DEFAULT '',
  posted_at TEXT,
  description TEXT DEFAULT '',
  score REAL DEFAULT 0,
  reasons TEXT DEFAULT '[]',
  flags TEXT DEFAULT '[]',
  first_seen TEXT NOT NULL,
  first_run INTEGER DEFAULT 0,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_roles_last_seen ON roles(last_seen);
CREATE INDEX IF NOT EXISTS idx_roles_company ON roles(company);

CREATE TABLE IF NOT EXISTS role_state (
  uid TEXT PRIMARY KEY REFERENCES roles(uid) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL REFERENCES roles(uid) ON DELETE CASCADE,
  kind TEXT NOT NULL,              -- cv | cover_letter | screen | jd_snapshot
  path TEXT DEFAULT '',
  rating REAL,
  summary TEXT DEFAULT '',
  gates TEXT DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_uid ON artifacts(uid, kind);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL REFERENCES roles(uid) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed
  requested_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT,
  attempts INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  log TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Terminal states: these roles stop appearing in results.
SETTLED = {"rejected", "withdrawn", "skipped", "closed"}
STATUSES = ["new", "interested", "applied", "submitted", "interviewing",
            "offer", "rejected", "withdrawn", "skipped", "closed"]


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path or DEFAULT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # A database made by an older version is missing columns the dashboard
    # now reads. Adding them on open rather than only in the write path means
    # `serve` and `list` cannot crash on a database that has not been scanned
    # since the upgrade.
    _ensure_columns(con)
    return con


@contextmanager
def open_db(path=None):
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


# ------------------------------------------------------------------ roles

def _ensure_columns(con) -> None:
    """Add columns to a database made by an older version."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(roles)")}
    if "first_run" not in cols:
        con.execute("ALTER TABLE roles ADD COLUMN first_run INTEGER DEFAULT 0")
    acols = {r["name"] for r in con.execute("PRAGMA table_info(artifacts)")}
    if "body" not in acols:
        con.execute("ALTER TABLE artifacts ADD COLUMN body TEXT DEFAULT ''")


def upsert_roles(con, jobs: Iterable) -> tuple[int, int]:
    """Insert or refresh roles. Returns (new, seen_before).

    `first_seen` is never overwritten: it is what makes "new since last run"
    meaningful across months rather than across one scan.
    """
    _ensure_columns(con)
    today = date.today().isoformat()
    run = int(get_meta(con, "runs", "0")) + 1      # the run these belong to
    new = seen = 0
    for j in jobs:
        row = con.execute("SELECT uid FROM roles WHERE uid=?", (j.uid,)).fetchone()
        vals = (
            j.company, j.title, j.url, j.location, j.city or "", j.country or "",
            j.work_mode or "unstated", j.sector or "", j.platform, j.department or "",
            j.salary.min, j.salary.max, j.salary.currency, j.salary.period,
            1 if j.salary.confirmed else 0, j.salary.label(),
            j.posted_at, (j.description or "")[:20000], j.score,
            json.dumps(j.reasons), json.dumps(j.flags), today,
        )
        if row:
            seen += 1
            con.execute("""UPDATE roles SET company=?,title=?,url=?,location=?,city=?,
                country=?,work_mode=?,sector=?,platform=?,department=?,salary_min=?,
                salary_max=?,salary_currency=?,salary_period=?,salary_confirmed=?,
                salary_label=?,posted_at=?,description=?,score=?,reasons=?,flags=?,
                last_seen=? WHERE uid=?""", vals + (j.uid,))
        else:
            new += 1
            con.execute("""INSERT INTO roles (company,title,url,location,city,country,
                work_mode,sector,platform,department,salary_min,salary_max,
                salary_currency,salary_period,salary_confirmed,salary_label,posted_at,
                description,score,reasons,flags,last_seen,first_seen,first_run,uid)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                vals + (today, run, j.uid))
            con.execute("INSERT OR IGNORE INTO role_state (uid,status,updated_at) "
                        "VALUES (?,'new',?)", (j.uid, today))
    return new, seen


# How long a role stays on the board after a scan last saw it.
#
# The dashboard used to show only roles whose last_seen equalled the newest
# date in the table. One `--limit 25` run then replaced a 60-role board with
# 4, because those 4 now held the newest date; `list` had no such filter and
# accumulated every role ever seen, so the two views disagreed by 71 rows on
# the same database. A window fixes both: a posting that stops appearing ages
# off in a fortnight instead of the instant a source is throttled.
LIVE_WINDOW_DAYS = 14

LIVE_SQL = (f"r.last_seen >= date((SELECT MAX(last_seen) FROM roles), "
            f"'-{LIVE_WINDOW_DAYS} days')")

# "New" means first seen on the most recent scan DATE, not on the most recent
# run NUMBER.
#
# Keyed on the run number, a second scan the same afternoon bumped the counter
# and every role from the morning stopped being new, so the answer to "what
# arrived today" became zero while twenty-one things had genuinely arrived.
# Rescanning is normal, and a person rescanning should see the same answer,
# not lose it. The date is what they mean by "today".
NEW_SQL = "r.first_seen = (SELECT MAX(last_seen) FROM roles)"


def current_run(con) -> int:
    """The run number the last completed scan wrote."""
    return int(get_meta(con, "runs", "0"))


def new_today(con) -> set[str]:
    """Roles first seen on the latest scan date. Stable across rescans."""
    _ensure_columns(con)
    return {r["uid"] for r in con.execute(
        f"SELECT uid FROM roles r WHERE {NEW_SQL}")}


def new_since_last_run(con, uids: list[str]) -> set[str]:
    """Roles first seen on THIS run, not merely today.

    Keying on the date meant a second scan the same afternoon re-reported every
    role from the morning as new, which is exactly the behaviour the seen-set
    exists to prevent.

    The very first run reports nothing as new: "here are 300 new roles" on day
    one is not an alert, it is the whole database.
    """
    _ensure_columns(con)
    runs = int(get_meta(con, "runs", "0"))
    if runs == 0 or not uids:
        return set()
    q = ",".join("?" * len(uids))
    rows = con.execute(
        f"SELECT uid FROM roles WHERE first_run=? AND uid IN ({q})",
        (runs + 1, *uids))
    return {r["uid"] for r in rows}


def set_status(con, uid: str, status: str, note: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    con.execute("""INSERT INTO role_state (uid,status,note,updated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(uid) DO UPDATE SET status=excluded.status,
          note=COALESCE(NULLIF(excluded.note,''), role_state.note),
          updated_at=excluded.updated_at""",
        (uid, status, note or "", date.today().isoformat()))


def settled_uids(con) -> set[str]:
    q = ",".join("?" * len(SETTLED))
    return {r["uid"] for r in con.execute(
        f"SELECT uid FROM role_state WHERE status IN ({q})", tuple(SETTLED))}


# -------------------------------------------------------------- artifacts

# Text small enough to keep forever. A .docx is a container, so its bytes are
# not worth storing, but the markdown it was built from is.
_TEXT_KINDS = {"screen", "jd_snapshot", "cover_letter", "cv"}


def _read_text(path) -> str:
    """The document's text, for keeping in the database.

    A generated screen costs real money and takes a minute, and storing only
    the file path meant one `rm -rf`, one moved folder, or one cleaned temp
    directory and it had to be bought again. The text is a few kilobytes.
    """
    p = Path(str(path or ""))
    if not p.exists() or p.is_dir():
        return ""
    if p.suffix.lower() == ".docx":
        # Prefer the markdown it was rendered from; fall back to extraction.
        md = p.with_suffix(".md")
        if md.exists():
            p = md
        else:
            try:
                from .runner import docx_to_text
                return docx_to_text(p)[:200_000]
            except Exception:
                return ""
    try:
        return p.read_text(errors="ignore")[:200_000]
    except OSError:
        return ""


def add_artifact(con, uid, kind, path="", rating=None, summary="", gates=None,
                 body=None) -> int:
    _ensure_columns(con)
    if body is None:
        body = _read_text(path) if kind in _TEXT_KINDS else ""
    cur = con.execute(
        """INSERT INTO artifacts (uid,kind,path,rating,summary,gates,created_at,body)
        VALUES (?,?,?,?,?,?,?,?)""",
        (uid, kind, str(path), rating, summary, json.dumps(gates or {}),
         date.today().isoformat(), body))
    return cur.lastrowid


def backfill_bodies(con) -> int:
    """Store the text of documents produced before there was a column for it."""
    _ensure_columns(con)
    n = 0
    for a in con.execute("SELECT id,kind,path FROM artifacts "
                         "WHERE COALESCE(body,'')='' ").fetchall():
        if a["kind"] not in _TEXT_KINDS:
            continue
        text = _read_text(a["path"])
        if text:
            con.execute("UPDATE artifacts SET body=? WHERE id=?", (text, a["id"]))
            n += 1
    return n


def artifacts_for(con, uid) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM artifacts WHERE uid=? ORDER BY created_at DESC, id DESC", (uid,))]


def has_artifact(con, uid, kind) -> bool:
    return con.execute("SELECT 1 FROM artifacts WHERE uid=? AND kind=? LIMIT 1",
                       (uid, kind)).fetchone() is not None


# ------------------------------------------------------------------- jobs

def enqueue(con, uid, kind) -> int:
    """Queue a generation job, unless one is already pending or running."""
    existing = con.execute(
        "SELECT id FROM jobs WHERE uid=? AND kind=? AND state IN ('pending','running')",
        (uid, kind)).fetchone()
    if existing:
        return existing["id"]
    cur = con.execute("INSERT INTO jobs (uid,kind,requested_at) VALUES (?,?,?)",
                      (uid, kind, _now()))
    return cur.lastrowid


def next_pending(con):
    return con.execute("SELECT * FROM jobs WHERE state='pending' "
                       "ORDER BY id LIMIT 1").fetchone()


def mark_job(con, job_id, state, error="", log="") -> None:
    stamp = "started_at" if state == "running" else "finished_at"
    con.execute(f"""UPDATE jobs SET state=?, {stamp}=?, error=?,
        log=substr(?,1,4000), attempts=attempts+? WHERE id=?""",
        (state, _now(), error, log, 1 if state == "running" else 0, job_id))


def running_count(con) -> int:
    return con.execute("SELECT COUNT(*) c FROM jobs WHERE state='running'").fetchone()["c"]


def reap_orphans(con, timeout_s: int = 900) -> int:
    """Fail jobs whose worker is gone, and say why.

    A generation runs on a daemon thread inside the server process, so it
    cannot outlive it. Restart the server mid-generation, or have it crash,
    and the row stays 'running' for ever: the button spins on the dashboard
    with nothing behind it, and because the queue guard is
    `running_count >= 1`, every later generation is refused too. One
    interrupted click silently disabled the whole feature.

    Anything still marked running or pending when the server starts belongs to
    a process that no longer exists. Anything older than the generation
    timeout is dead whatever started it.
    """
    n = con.execute(
        "UPDATE jobs SET state='failed', finished_at=?, error=? "
        "WHERE state IN ('running','pending')",
        (_now(), "interrupted: the server restarted while this was running. "
                 "Nothing was charged for the unfinished part; click again."),
    ).rowcount
    stale = con.execute(
        "UPDATE jobs SET state='failed', finished_at=?, error=? "
        "WHERE state='running' AND "
        "replace(started_at,'T',' ') < datetime('now','localtime',?)",
        (_now(), f"gave up after {timeout_s // 60} minutes", f"-{timeout_s} seconds"),
    ).rowcount
    return n + stale


# ------------------------------------------------------------------- meta

def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_meta(con, k, v) -> None:
    con.execute("INSERT INTO meta (k,v) VALUES (?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def bump_runs(con) -> int:
    n = int(get_meta(con, "runs", "0")) + 1
    set_meta(con, "runs", n)
    set_meta(con, "last_run", _now())
    return n


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# -------------------------------------------------------------- migration

def migrate(con, state_path="state/seen.json", apps_path=None) -> dict:
    """Import the old stores. Idempotent: safe to run on every start.

    Runs only against rows that are not already present, so a second call
    changes nothing. That matters because this is called on every `scan`.
    """
    from .applications import Tracker

    done = get_meta(con, "migrated")
    out = {"roles": 0, "statuses": 0, "already": bool(done)}

    sp = Path(state_path)
    if sp.exists():
        try:
            data = json.loads(sp.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        for uid, e in (data.get("seen") or {}).items():
            if con.execute("SELECT 1 FROM roles WHERE uid=?", (uid,)).fetchone():
                continue
            con.execute("""INSERT INTO roles (uid,company,title,first_seen,last_seen)
                VALUES (?,?,?,?,?)""",
                (uid, e.get("company", ""), e.get("title", ""),
                 e.get("first_seen", date.today().isoformat()),
                 e.get("last_seen", date.today().isoformat())))
            con.execute("INSERT OR IGNORE INTO role_state (uid,status,updated_at) "
                        "VALUES (?,'new',?)", (uid, date.today().isoformat()))
            out["roles"] += 1
        if not get_meta(con, "runs"):
            set_meta(con, "runs", data.get("runs", 0))

    tracker = Tracker.load(apps_path)
    for app in tracker.apps:
        rows = con.execute("SELECT uid, company, title FROM roles").fetchall()
        for r in rows:
            fake = type("J", (), {"company": r["company"], "title": r["title"],
                                  "url": ""})()
            if app.matches(fake):
                cur = con.execute("SELECT status FROM role_state WHERE uid=?",
                                  (r["uid"],)).fetchone()
                if cur and cur["status"] != "new":
                    continue
                set_status(con, r["uid"], app.status, app.note)
                out["statuses"] += 1
    set_meta(con, "migrated", _now())
    return out
