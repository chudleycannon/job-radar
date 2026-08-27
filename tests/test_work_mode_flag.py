"""A structured "this is remote" flag is not evidence when the advert disagrees.

`work_mode` returned "remote" on `job.remote is True` before it read a word of
the posting. The comment justifying that is right about Pinpoint, Breezy and
Teamtailor, which set the field deliberately. It is wrong about Ashby.

Measured over 1,316 postings from 30 real Ashby boards on 2026-08-27:
`isRemote` is true on 52.4% of them, and 87.2% of those name a physical city
and never use the word remote anywhere in the title, location or advert.
Roles in New York, London, Chicago and Bogota were all being labelled remote.

Ashby is the largest platform in the fast pass, so this is most of what a new
user sees in their first five minutes, and "remote" is the one label a
remote-only reader filters on. Re-labelling the same 1,316 postings:

    remote  669 -> 324        345 roles corrected
    office   88 -> 101
    unstated 510 -> 842

The rule is not about Ashby. A named town, with no mention of remote work
anywhere in the posting, is the advert's own statement of where the job is,
against a boolean nobody had to look at. Everything that says so in words is
untouched, which is every honest use of the flag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Job          # noqa: E402
from jobradar.screen import work_mode    # noqa: E402


def _mode(location, description="We are hiring an engineer.", remote=None,
          title="Engineer"):
    return work_mode(Job(company="Acme", title=title, url="https://x/1",
                         platform="ashby", location=location,
                         description=description, remote=remote))


def test_a_flag_that_says_remote_over_a_town_that_says_otherwise_loses():
    for city in ("New York", "London", "Chicago", "San Francisco",
                 "Bogota", "Amsterdam"):
        assert _mode(city, remote=True) != "remote", \
            f"{city} with isRemote=true is still being called remote"


def test_the_flag_still_wins_when_nothing_contradicts_it():
    """Pinpoint, Breezy and Teamtailor set this field on purpose, and an
    on-site gym in the body used to file their remote roles as office."""
    assert _mode("", remote=True) == "remote"
    assert _mode("Remote", remote=True) == "remote"
    assert _mode("Anywhere", remote=True) == "remote"
    assert _mode("Remote", remote=True,
                 description="We have an on-site gym at our London office "
                             "for anyone who wants to visit.") == "remote"


def test_a_town_that_also_says_remote_is_still_remote():
    """Every honest use of the flag has the word somewhere."""
    for loc in ("Remote - US", "London (Remote)", "San Francisco (Remote)",
                "Remote-US", "US Remote"):
        assert _mode(loc, remote=True) == "remote", loc


def test_prose_saying_remote_beats_a_town_in_the_location_field():
    assert _mode("New York", remote=True,
                 description="This is a fully remote role.") == "remote"


def test_hybrid_still_wins_over_everything():
    """A role that says hybrid is hybrid, whatever the flag claims."""
    assert _mode("London", remote=True,
                 description="Hybrid: two days a week in the office.") == "hybrid"


def test_nothing_changes_for_a_posting_with_no_flag_set():
    assert _mode("Leeds") == "unstated"
    assert _mode("Leeds", description="Fully remote role.") == "remote"
