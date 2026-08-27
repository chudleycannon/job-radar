"""iCIMS writes a job's location two different ways, and one was unread.

    <span class="field-label">Job Locations</span> <span>UK-London</span>

    <span class="sr-only field-label">Location : Location</span> </dt>
      <dd class="iCIMS_JobHeaderData"><span> US-AZ-Chandler</span></dd>

The pattern only matched the first. iCIMS is 1,744 of the bundled boards, the
second largest platform in the list, and over 858 roles fetched from 40 real
boards on 2026-08-27, 51.6% had no location and 54.4% could not be placed in
a country. Re-parsing the same payloads with the widened pattern: 7.2% and
13.5%.

A role with no location cannot be filtered by country, which is the first
thing anybody asks of a job search, so this was not a cosmetic gap.

Both fixtures are real blocks captured from real boards, one of each shape.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.adapters import platforms as PL      # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"

# What shipped before. Kept so the tests can show the difference rather than
# assert that today's behaviour is today's behaviour.
OLD = re.compile(
    r'field-label">Job Locations?</span>\s*<span[^>]*>\s*(.*?)\s*</span>', re.S)


def _block(variant):
    return (FIX / f"icims_row_{variant}.html").read_text(encoding="utf-8")


def test_the_old_shape_still_parses():
    m = PL._ICIMS_LOC.search(_block("span"))
    assert m and m.group(1).strip() == "US-IN-Columbia City"


def test_the_definition_list_shape_now_parses_too():
    m = PL._ICIMS_LOC.search(_block("dd"))
    assert m and m.group(1).strip() == "US-AZ-Chandler"


def test_the_old_pattern_really_did_miss_the_second_shape():
    """Otherwise this file is testing nothing.

    If iCIMS ever normalises its markup and both fixtures start matching the
    old pattern, this fails and the widening can be reconsidered rather than
    carried forever on trust.
    """
    assert OLD.search(_block("span")), "the fixture for the old shape is wrong"
    assert not OLD.search(_block("dd")), \
        "the second fixture no longer demonstrates the bug"


def test_the_label_match_does_not_run_past_its_own_tag():
    """`[^<]*` around the label, not `.*?`.

    A lazy dot would let the pattern start at some unrelated `field-label`
    earlier in the page and swallow markup up to the next location, which
    reads as a match and produces a location belonging to a different role.
    """
    html = ('<span class="field-label">Category</span><span>Engineering</span>'
            '<span class="field-label">Job Locations</span><span>UK-London</span>')
    m = PL._ICIMS_LOC.search(html)
    assert m and m.group(1).strip() == "UK-London"


def test_a_block_with_no_location_at_all_matches_nothing():
    assert not PL._ICIMS_LOC.search(
        '<span class="field-label">Category</span><span>Engineering</span>')


def test_the_country_code_is_moved_to_the_end_where_the_filter_reads_it():
    """"US-AZ-Chandler" filters as a US role only once it says so in words."""
    from jobradar.screen import _country_of
    for raw, want in (("US-AZ-Chandler", "US"), ("UK-London", "UK")):
        bits = [b for b in raw.split("-") if b]
        tidy = f"{', '.join(bits[1:])}, {bits[0]}"
        assert _country_of(tidy) == want, f"{tidy!r} did not read as {want}"
