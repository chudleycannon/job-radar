"""The migration deleted a role and everything attached to it, silently.

`rekey_uids` handles two ids resolving to one, which happens when two stored
rows are the same posting reached by URLs differing only in a tracking
parameter. It did that with a bare `DELETE FROM roles`, and `roles(uid)` is
referenced ON DELETE CASCADE by `role_state`, `artifacts` and `jobs` with
foreign keys ON. So one line took the status, the note, every generated CV
and every queued job with it, and `done` was not incremented either, so
`migrate` reported `rekeyed: 0` on a run that had just destroyed a role
somebody was interviewing for.

`merge_duplicates` solves the identical problem correctly 240 lines above and
this ignored it.

Reachable two ways, both observed: `repair_smartrecruiters_urls` rewrites a
stored url at the end of every scan without rekeying, so the next scan finds
the pair; and a writer racing the migration leaves one behind.

The survivor was not chosen either. `taken` is seeded with every original id,
so "keep the one already under the new id" meant whichever row the SELECT
returned first, and the copy carrying the history was as likely to be deleted
as kept.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store  # noqa: E402

RICH = ("A", "https://s.com/j?gh_jid=1&utm_source=a", "interviewing",
        "second round 3 Sept", True)
POOR = ("B", "https://s.com/j?gh_jid=1&utm_source=b", "new", "", False)


def _db(order):
    con = store.connect(Path(tempfile.mkdtemp()) / "t.db")
    for uid, url, status, note, art in order:
        con.execute(
            "INSERT INTO roles (uid,company,title,url,location,first_seen,"
            "last_seen) VALUES (?,?,?,?,?,date('now'),date('now'))",
            (uid, "Stripe", "Engineering Manager", url, "London"))
        con.execute("INSERT INTO role_state (uid,status,note,updated_at) "
                    "VALUES (?,?,?,date('now'))", (uid, status, note))
        if art:
            con.execute("INSERT INTO artifacts (uid,kind,path,created_at) "
                        "VALUES (?,'cv','/tmp/cv.docx',date('now'))", (uid,))
    con.commit()
    return con


def _state(con):
    r = con.execute("SELECT status, note FROM role_state").fetchone()
    return (con.execute("SELECT COUNT(*) c FROM roles").fetchone()["c"],
            con.execute("SELECT COUNT(*) c FROM artifacts").fetchone()["c"],
            r["status"], r["note"])


def test_the_interview_and_the_cv_survive_the_merge():
    con = _db([RICH, POOR])
    store.rekey_uids(con)
    roles, arts, status, note = _state(con)
    assert roles == 1
    assert status == "interviewing", "a status somebody set by hand was lost"
    assert note == "second round 3 Sept", "their note was lost"
    assert arts == 1, "a generated CV was deleted"


def test_it_does_not_depend_on_which_row_was_inserted_first():
    """The survivor is chosen by what is attached to it, not by rowid."""
    con = _db([POOR, RICH])
    store.rekey_uids(con)
    roles, arts, status, note = _state(con)
    assert (roles, arts, status) == (1, 1, "interviewing"), (roles, arts, status)
    assert note == "second round 3 Sept"


def test_a_merge_is_reported_rather_than_reported_as_nothing():
    """`rekeyed: 0` on a run that changed the database is the shape of bug
    this whole project is about."""
    con = _db([RICH, POOR])
    assert store.rekey_uids(con) > 0


def test_the_database_is_still_sound_afterwards():
    con = _db([RICH, POOR])
    store.rekey_uids(con)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    orphans = con.execute(
        "SELECT COUNT(*) c FROM role_state s LEFT JOIN roles r ON r.uid=s.uid "
        "WHERE r.uid IS NULL").fetchone()["c"]
    assert orphans == 0


def test_it_is_still_idempotent_after_a_merge():
    con = _db([RICH, POOR])
    store.rekey_uids(con)
    assert store.rekey_uids(con) == 0
    assert _state(con)[0] == 1
