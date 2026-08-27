"""A dealbreaker read only the advert, and employers do not only write there.

A role posted at

    Hybrid - New York, NY

was kept for a reader with a hard `hybrid` dealbreaker, because the word was
in the location column and not in the description. The dashboard then printed
it, in that exact form, on the row it was supposed to have hidden. That is
the whole point of the setting failing in the place it is easiest to see.

The location and the title are short and factual, which is why they are safe
to read. The incidental-mention guard exists for the description, where a
long advert can mention another team's policy in passing. A location of
"Hybrid - New York" is not mentioning hybrid working, it is stating it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config, Dealbreaker  # noqa: E402
from jobradar.models import Job                  # noqa: E402
from jobradar.screen import screen               # noqa: E402

ADVERT = ("We are hiring an engineer to work on our payments platform. "
          "You will own services end to end and work with a small team. "
          "We offer a competitive package and a generous holiday allowance. "
          "Our stack is Python and Postgres, running on AWS.")


def _cfg(name, pattern, hard=True):
    c = Config()
    c.dealbreakers = [Dealbreaker(name=name, pattern=pattern, hard=hard)]
    return c


def _job(title="Engineer", location="", description=ADVERT):
    return Job(company="Acme", title=title, url="https://x/1",
               platform="ashby", location=location, description=description)


def test_a_hard_dealbreaker_in_the_location_hides_the_role():
    keep, hits = screen(_job(location="Hybrid - New York, NY"),
                        _cfg("hybrid", r"hybrid"))
    assert keep is False, "the word was in the location and the role was kept"
    assert "hybrid" in hits


def test_a_hard_dealbreaker_in_the_title_hides_the_role():
    keep, _ = screen(_job(title="Night Shift Support Engineer"),
                     _cfg("night shift", r"night shift"))
    assert keep is False


def test_a_location_hit_is_not_downgraded_for_lack_of_a_mention_in_the_advert():
    """The trap in this change.

    The incidental-mention guard is handed the description. With the match in
    the location, the description has zero mentions, and a guard that read
    "all zero of them are incidental" as True would turn every hard hit into
    a soft flag. It does not, and this is here so it cannot start.
    """
    job = _job(location="Hybrid - New York, NY")
    keep, _ = screen(job, _cfg("hybrid", r"hybrid"))
    assert keep is False
    assert not any("only in passing" in f for f in job.flags), job.flags


def test_a_soft_dealbreaker_in_the_location_still_only_flags():
    job = _job(location="Hybrid - New York, NY")
    keep, hits = screen(job, _cfg("hybrid", r"hybrid", hard=False))
    assert keep is True
    assert any("soft flag: hybrid" in f for f in job.flags), job.flags


def test_a_role_matching_nothing_is_untouched():
    job = _job(location="London, UK")
    keep, hits = screen(job, _cfg("hybrid", r"hybrid"))
    assert keep is True and hits == []


def test_the_incidental_guard_is_still_handed_the_advert():
    """Unchanged behaviour, checked on the phrasing the guard recognises.

    Omnea's adverts list "[X] days in office" as an EXAMPLE of the kind of
    hard requirement a posting might state, rather than as this posting's
    policy. That is the case the guard exists for, and adding the title and
    location to the scan must not stop it being consulted.
    """
    body = (ADVERT + " ADDITIONALLY, WHERE ROLES HAVE HARD-SPECIFIED "
            "REQUIREMENTS (E.G. [X] DAYS IN OFFICE, UNABLE TO PROVIDE VISAS, "
            "ETC), AUTOMATIC REJECTION CRITERIA ARE IN PLACE.")
    job = _job(description=body)
    keep, _ = screen(job, _cfg("days in office", r"days in office"))
    assert keep is True, "a worked example was read as this employer's policy"
    assert any("only in passing" in f for f in job.flags), job.flags


def test_a_posting_with_no_text_anywhere_is_kept_and_said_to_be_unscreened():
    job = _job(title="", location="", description="")
    keep, hits = screen(job, _cfg("hybrid", r"hybrid"))
    assert keep is True and hits == []
    assert any("not screened" in f for f in job.flags), job.flags
