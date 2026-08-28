"""Answering "no" to remote work did nothing on an American posting.

`remote_ok: false` was checked as `job.remote is True`, which catches a
platform flag and a bare "Remote" and nothing else. With `countries: [US]`,
every one of these was kept:

    Remote - US        US Remote        Remote (US)
    Fully Remote - United States

and US employers write it that way almost every time, so the setting was
close to inert exactly where an American reader needs it.

The fix is not "drop anything containing the word remote". A posting listing
"New York, Denver, Remote, San Francisco" is a role with offices, and
somebody who said no to remote work still wants it: they are asking not to
work from home, not asking to be hidden from employers who let other people.
`city_of` is what tells those apart.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config   # noqa: E402
from jobradar.models import Job      # noqa: E402
from jobradar.screen import match    # noqa: E402

REMOTE_ONLY = ("Remote", "Remote - US", "US Remote", "Remote (US)",
               "Fully Remote - United States")
HAS_AN_OFFICE = ("New York, Denver, Remote, San Francisco",
                 "San Francisco, CA", "New York, NY")


def _keep(location, remote_ok):
    cfg = Config()
    cfg.titles_include = ["engineer"]
    cfg.countries = ["US"]
    cfg.remote_ok = remote_ok
    return match(Job(company="Acme", title="Engineer", url="https://x/1",
                     platform="ashby", location=location,
                     description="We are hiring an engineer."), cfg)[0]


def test_remote_off_hides_every_way_of_writing_remote_only():
    kept = [loc for loc in REMOTE_ONLY if _keep(loc, False)]
    assert not kept, f"remote_ok is off and these came through: {kept}"


def test_remote_off_still_keeps_a_role_that_has_an_office():
    """Saying no to remote is not asking to be hidden from employers who
    offer it to other people."""
    dropped = [loc for loc in HAS_AN_OFFICE if not _keep(loc, False)]
    assert not dropped, f"roles with a real office were dropped: {dropped}"


def test_remote_on_changes_nothing_for_any_of_them():
    for loc in REMOTE_ONLY + HAS_AN_OFFICE:
        assert _keep(loc, True) is True, loc
