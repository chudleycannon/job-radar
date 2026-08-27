"""Workday collapses a multi-location posting to a count, and it was stored.

`locationsText` comes back as the literal string "2 Locations". Written into
the location column it becomes a place name: no country logic can read it, no
country filter can act on it, and on the dashboard it sits exactly where a
city would, so a reader cannot tell it from a real one. That is the shape of
bug this project keeps producing, a failure that renders like a success.

Across 12 real tenants on 2026-08-27, 198 of 589 postings said this.

Workday's own `externalPath` names the primary location of the posting, so
there is real data to fall back to rather than a guess. It resolved to a
country for 192 of the 198. Re-parsing the same payloads:

  no country                        36.5%  ->  3.9%
  roles whose location is a count     198  ->  0

The count is not thrown away. A role open in London and New York must not
look like a role open only in whichever of them Workday put in the path, so
the other locations are recorded as a flag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.adapters.platforms import parse_workday  # noqa: E402
from jobradar.screen import _country_of                # noqa: E402
from jobradar.sources import Source                    # noqa: E402

SRC = Source.from_dict({
    "company": "The Atlantic", "platform": "workday",
    "url": "https://x.wd1.myworkdayjobs.com/wday/cxs/theatlantic/Careers/jobs"})


def _jobs(*postings):
    return list(parse_workday({"jobPostings": list(postings)}, SRC))


def test_a_count_never_survives_as_a_location():
    j = _jobs({"title": "Senior Product Manager", "locationsText": "2 Locations",
               "externalPath": "/job/New-York---Prince-St/Senior-PM_R721"})[0]
    assert "Location" not in j.location
    assert j.location == "New York Prince St"
    assert _country_of(j.location) == "US"


def test_the_other_locations_are_recorded_rather_than_dropped():
    j = _jobs({"title": "x", "locationsText": "5 Locations",
               "externalPath": "/job/Madrid-Spain/x_R1"})[0]
    assert any("5 locations" in f for f in j.flags), j.flags


def test_a_real_location_is_left_exactly_as_it_came():
    j = _jobs({"title": "Editor", "locationsText": "London, United Kingdom",
               "externalPath": "/job/London/Editor_R9"})[0]
    assert j.location == "London, United Kingdom"
    assert j.flags == []


def test_the_singular_is_matched_too():
    j = _jobs({"title": "x", "locationsText": "1 Location",
               "externalPath": "/job/Madrid-Spain/x_R1"})[0]
    assert j.location == "Madrid Spain"


def test_a_place_whose_name_contains_the_word_is_not_mistaken_for_a_count():
    """"Locations" is only a count when the whole string is a number and the
    word. A real place is never discarded on a substring match."""
    for real in ("Multiple Locations, London", "Locations Ltd, Leeds",
                 "3 Mills Studios, London"):
        j = _jobs({"title": "x", "locationsText": real,
                   "externalPath": "/job/Nowhere/x_R1"})[0]
        assert j.location == real, f"{real!r} was treated as a count"


def test_a_count_with_no_usable_path_does_not_leave_the_count_behind():
    """Better empty than a fake place: empty is a thing the rest of the tool
    already knows how to say it does not know."""
    j = _jobs({"title": "x", "locationsText": "4 Locations",
               "externalPath": "", "bulletFields": []})[0]
    assert "Location" not in (j.location or "")
