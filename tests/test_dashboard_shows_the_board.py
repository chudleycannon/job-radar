"""The written page showed one scan's finds; `serve` showed the database.

After `seed load` of 267 roles and `scan --limit 400`, the file the scan
wrote was headed "15 roles worth a look" and `job-radar serve` showed 270,
with nothing on either page explaining the gap. Both were internally
consistent, and one of them was answering a question nobody asked: a reader
wants their board, not the slice of it that one command happened to touch.

It shows up whenever a scan does not cover everything already stored, which
is every `--limit` run and every run after a seed, and those are the two
first things a new user does.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store            # noqa: E402
from jobradar.models import Job       # noqa: E402


def _job(i, title="Engineering Manager"):
    return Job(company=f"Co{i}", title=title, url=f"https://x/{i}",
               platform="ashby", location="London", description="d" * 300)


def _db():
    return store.connect(Path(tempfile.mkdtemp()) / "t.db")


def test_the_board_is_everything_stored_not_the_last_scans_finds():
    con = _db()
    store.upsert_roles(con, [_job(i) for i in range(20)])
    con.commit()
    assert len(store.live_jobs(con)) == 20


def test_a_role_you_settled_stays_off_it():
    """The same ones `serve` hides. A role you rejected must not reappear on
    the next scan's page."""
    con = _db()
    jobs = [_job(i) for i in range(5)]
    store.upsert_roles(con, jobs)
    store.set_status(con, jobs[0].uid, "rejected")
    con.commit()
    got = {j.uid for j in store.live_jobs(con)}
    assert jobs[0].uid not in got and len(got) == 4


def test_what_comes_back_is_usable_as_a_role():
    con = _db()
    store.upsert_roles(con, [_job(1)])
    con.commit()
    j = store.live_jobs(con)[0]
    assert j.company == "Co1" and j.url == "https://x/1"
    assert j.title and j.uid and isinstance(j.flags, list)


def test_a_malformed_flags_column_does_not_take_the_board_down():
    con = _db()
    store.upsert_roles(con, [_job(1)])
    con.execute("UPDATE roles SET flags='{not json'")
    con.commit()
    j = store.live_jobs(con)[0]
    assert j.flags == [] and j.company == "Co1"


def test_the_scan_renders_the_board_and_a_dry_run_does_not():
    """A dry run has written nothing, so there is no board that reflects it
    and it still shows what it found."""
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_scan)
    assert "board = kept if args.dry_run else store.live_jobs(con)" in src
