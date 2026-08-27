"""apply.workable.com only sends the advert when it is asked to.

The bundled list held 2,094 widget URLs with no `details=true` on any of them,
and the response without it carries no `description` key at all. So the
slowest phase of the whole scan, fifty minutes against one host, was storing
roles that `rank` refuses to score (it requires 200 characters), that no
dealbreaker could be checked against, and that had no salary, because
`parse_text` was handed an empty string. On 2026-08-27 that was 219 of 219
stored Workable roles, while `workable_search`, reading the same company's
adverts through a different host, had 96 of 97.

The two fixtures here are the same real board read both ways, trimmed. They
exist so the difference is a thing a test can see rather than a thing a
comment claims.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters                      # noqa: E402
from jobradar.adapters import platforms            # noqa: E402
from jobradar.sources import Source                # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
WIDGET = "https://apply.workable.com/api/v1/widget/accounts/1000heads"


def _src(url=WIDGET):
    return adapters.prepare(Source.from_dict(
        {"company": "1000Heads", "url": url, "platform": "workable"}))


def test_prepare_asks_the_widget_for_details():
    assert _src().url.endswith("?details=true")


def test_a_trailing_slash_does_not_produce_a_double_question_mark():
    assert _src(WIDGET + "/").url == WIDGET + "?details=true"


def test_it_is_not_added_twice():
    once = _src().url
    assert adapters.prepare(Source.from_dict(
        {"company": "x", "url": once})).url == once


def test_other_workable_hosts_are_left_alone():
    for u in ("https://jobs.workable.com/api/v1/jobs?query=engineer",
              "https://jobs.workable.com/api/v1/companies/acme",
              "https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
        assert _src(u).url == u


def test_every_bundled_workable_board_is_asked_for_details():
    """The pipeline, not the helper.

    A normalisation that works in isolation and is not reached by `load` is
    the failure this whole file is about, so this reads the shipped list
    through the same call the scan uses.
    """
    from jobradar import sources as S
    raw = json.loads((Path(__file__).resolve().parent.parent /
                      "sources" / "sources.json").read_text(encoding="utf-8"))
    items = raw.get("sources", raw) if isinstance(raw, dict) else raw
    widgets = [d for d in items
               if "apply.workable.com/api" in str(d.get("url", ""))
               and "/widget/accounts/" in str(d.get("url", ""))]
    assert widgets, "the bundled list no longer has any Workable widget boards"
    prepared = [adapters.prepare(Source.from_dict(dict(d))) for d in widgets]
    missing = [s.url for s in prepared if "details=true" not in s.url]
    assert not missing, f"{len(missing)} bundled Workable boards ask for no advert"


def test_the_plain_response_really_does_carry_no_description():
    payload = json.loads((FIX / "workable_widget_plain.json")
                         .read_text(encoding="utf-8"))
    jobs = list(platforms.parse_workable(payload, _src()))
    assert jobs, "fixture parsed to nothing"
    assert all(not (j.description or "").strip() for j in jobs)


def test_the_details_response_carries_one_and_the_parser_keeps_it():
    payload = json.loads((FIX / "workable_widget_details.json")
                         .read_text(encoding="utf-8"))
    jobs = list(platforms.parse_workable(payload, _src()))
    assert jobs, "fixture parsed to nothing"
    assert all(len((j.description or "").strip()) >= 200 for j in jobs), \
        "a description under 200 characters is one `rank` will refuse to score"
    assert all(j.title and j.url for j in jobs)


# --------------------------------------------------------------------------
# The same adapter's other half: where the job is.
#
# `parse_workable` read `j["location"]` for a dict of city/region/country. The
# widget has never sent a `location` key. It sends `city`, `state` and
# `country` at the top level and a `locations` array with `countryCode` in it,
# so the dict was always empty and every posting from 2,094 boards was stored
# with no location.
#
# Measured over the same 180-board sample on 2026-08-27: 2,509 of 5,479
# postings had no location string, and those were 93% of everything the tool
# could not place in a country. Fixing it took unresolved country from 49.4%
# to 3.6%, no-location from 45.8% to 0%, and doubled the UK roles the sample
# found, 132 to 263.

def test_the_widget_payload_has_no_location_key_which_is_the_whole_bug():
    payload = json.loads((FIX / "workable_widget_details.json")
                         .read_text(encoding="utf-8"))
    assert all("location" not in j for j in payload["jobs"]), \
        "if Workable added a `location` key, this adapter can be simplified"
    assert all(j.get("city") or j.get("locations") for j in payload["jobs"])


def test_every_posting_in_the_real_fixture_gets_a_location():
    payload = json.loads((FIX / "workable_widget_details.json")
                         .read_text(encoding="utf-8"))
    jobs = list(platforms.parse_workable(payload, _src()))
    assert jobs
    for j in jobs:
        assert (j.location or "").strip(), f"{j.title} has no location"


def test_a_country_can_be_read_off_what_the_parser_produces():
    from jobradar.screen import _country_of
    payload = json.loads((FIX / "workable_widget_details.json")
                         .read_text(encoding="utf-8"))
    got = {_country_of(j.location or "")
           for j in platforms.parse_workable(payload, _src())}
    assert None not in got, "a location the country logic cannot read is no use"
    assert {"US", "UK"} <= got


def _one(**job):
    return list(platforms.parse_workable({"jobs": [job]}, _src()))[0]


def test_the_structured_locations_array_is_preferred():
    j = _one(title="x", url="u", city="Ignored",
             locations=[{"city": "Leeds", "region": "England",
                         "country": "United Kingdom"}])
    assert j.location == "Leeds, England, United Kingdom"


def test_it_falls_back_to_the_top_level_fields():
    j = _one(title="x", url="u", city="Leeds", state="England",
             country="United Kingdom")
    assert j.location == "Leeds, England, United Kingdom"


def test_a_hidden_location_is_not_advertised():
    """Workable marks a location hidden when the employer does not publish it.

    Printing it anyway would put an address on the dashboard that the advert
    itself does not show.
    """
    j = _one(title="x", url="u", city="Leeds", country="United Kingdom",
             locations=[{"city": "Secret", "country": "France", "hidden": True}])
    assert "Secret" not in (j.location or "")
    assert j.location == "Leeds, United Kingdom"


def test_several_locations_are_joined_the_way_the_country_logic_reads_them():
    from jobradar.screen import _country_of
    j = _one(title="x", url="u", locations=[
        {"city": "London", "country": "United Kingdom"},
        {"city": "New York", "country": "United States"}])
    assert j.location == "London, United Kingdom / New York, United States"
    assert _country_of(j.location) is not None


def test_a_repeated_location_is_not_repeated():
    j = _one(title="x", url="u", locations=[
        {"city": "London", "country": "United Kingdom"},
        {"city": "London", "country": "United Kingdom"}])
    assert j.location == "London, United Kingdom"


def test_telecommuting_still_marks_the_role_remote():
    assert _one(title="x", url="u", city="Leeds", country="United Kingdom",
                telecommuting=True).remote is True


def test_a_posting_with_nothing_at_all_does_not_invent_a_location():
    j = _one(title="x", url="u")
    assert j.location == ""
