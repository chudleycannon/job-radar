"""A bulk import is not "everything arrived today".

`seed load` stamped every imported role with today's date, and `--new` is
keyed on the date deliberately, so on the day of the import "what is new"
was the entire database. Following the sequence the README recommends, seed
then scan, the scan reported 3 new roles and `job-radar list --new` reported
437, one minute apart, against the same rows. The three the scan genuinely
found were indistinguishable from the 434 it did not.

The date-keying is right and stays. `first_seen` means when the job was
first OBSERVED, and for a seeded row that is the day the shard set was built,
not the day somebody downloaded it. `last_seen` is still today, because the
role is still on the board as far as we know, and that is what keeps it live.
"""
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store             # noqa: E402
from jobradar.models import Job        # noqa: E402

WEEK_AGO = (date.today() - timedelta(days=7)).isoformat()
TODAY = date.today().isoformat()


def _jobs(n=3):
    return [Job(company=f"Co{i}", title="Engineer", url=f"https://x/{i}",
                platform="ashby", location="London") for i in range(n)]


def _db():
    return store.connect(Path(tempfile.mkdtemp()) / "t.db")


def test_an_import_can_say_when_the_roles_were_actually_first_seen():
    con = _db()
    store.upsert_roles(con, _jobs(), first_seen=WEEK_AGO)
    seen = {r["first_seen"] for r in con.execute("SELECT first_seen FROM roles")}
    assert seen == {WEEK_AGO}


def test_it_is_still_live_today_even_though_it_was_first_seen_last_week():
    """`last_seen` drives the live window. A seeded role must not age out on
    arrival."""
    con = _db()
    store.upsert_roles(con, _jobs(), first_seen=WEEK_AGO)
    seen = {r["last_seen"] for r in con.execute("SELECT last_seen FROM roles")}
    assert seen == {TODAY}


def test_a_week_old_import_is_not_reported_as_new_today():
    con = _db()
    store.upsert_roles(con, _jobs(), first_seen=WEEK_AGO)
    assert store.new_today(con) == set()


def test_a_scan_the_same_day_still_reports_its_own_finds_as_new():
    """The half that must keep working. Importing a seed must not make the
    next scan's genuine finds invisible."""
    con = _db()
    store.upsert_roles(con, _jobs(3), first_seen=WEEK_AGO)
    fresh = [Job(company="New", title="Engineer", url="https://x/new",
                 platform="ashby", location="London")]
    store.upsert_roles(con, fresh)
    assert store.new_today(con) == {fresh[0].uid}


def test_a_scan_with_no_seed_is_unchanged():
    con = _db()
    store.upsert_roles(con, _jobs(2))
    assert len(store.new_today(con)) == 2


def test_the_loader_passes_the_build_date_through():
    """The fix only exists if `cmd_seed_load` reads it off the index."""
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_seed_load)
    assert 'first_seen=idx.get("generated")' in src, \
        "seed load is stamping its own import date again"
