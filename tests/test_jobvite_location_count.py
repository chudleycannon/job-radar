"""Jobvite collapses a multi-location role to a count, in its own div.

    <td class="jv-job-list-location"> Remote<span>,</span>
      <div class="jv-meta"> 4 Locations </div></td>

The count was surviving into the location column, where "4 Locations" reads
as a place name that no country logic can parse and no reader can tell from a
real one. 65 roles in a 12-board sample said this; after the fix, 0, with 41
carrying the count as a flag instead. The cell's real text sits beside the
div and is what the row actually knows.

The subtle part is the split. `_JV_ROW` captures the location cell
non-greedily and stops at the first `</div>`, so the closing tag is NOT
inside the captured text, and the first attempt -- a pattern requiring a
closed `<div>...</div>` -- matched nothing at all. That failure renders
exactly like a cell with no meta div in it, which is why it is tested rather
than commented.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.adapters.platforms import parse_jobvite  # noqa: E402
from jobradar.sources import Source                    # noqa: E402

SRC = Source.from_dict({"company": "Dwt", "platform": "jobvite",
                        "url": "https://jobs.jobvite.com/dwt/jobs"})


def _row(href, title, cell):
    return (f'<tr><td class="jv-job-list-name"><a href="{href}">{title}</a>'
            f'</td><td class="jv-job-list-location">{cell}</td></tr>')


def _jobs(*rows):
    return list(parse_jobvite("<table>" + "".join(rows) + "</table>", SRC))


COUNT_CELL = ' Remote<span>,</span> <div class="jv-meta"> 4 Locations </div> '


def test_a_count_never_survives_as_a_location():
    j = _jobs(_row("/dwt/job/a2", "Data Analyst", COUNT_CELL))[0]
    assert not re.match(r"^\d+\s+Locations?$", j.location or "")
    assert j.location == "Remote"


def test_the_count_is_kept_as_a_flag_rather_than_dropped():
    j = _jobs(_row("/dwt/job/a2", "Data Analyst", COUNT_CELL))[0]
    assert any("4 locations" in f for f in j.flags), j.flags


def test_the_cell_text_beside_the_div_is_not_lost():
    """"Remote" is real information and the row's only statement of place."""
    j = _jobs(_row("/dwt/job/a2", "x", COUNT_CELL))[0]
    assert j.remote is True
    assert j.location == "Remote"


def test_an_ordinary_cell_is_untouched():
    j = _jobs(_row("/dwt/job/a3", "Editor", " London, United Kingdom "))[0]
    assert j.location == "London, United Kingdom"
    assert j.flags == []


def test_a_remote_row_with_a_real_country_still_keeps_the_country():
    j = _jobs(_row("/dwt/job/a1", "AI Developer",
                   " Remote<span>,</span> United States "))[0]
    assert j.location == "United States"
    assert j.remote is True


def test_the_meta_div_is_found_even_though_its_closing_tag_is_not_captured():
    """The bug the first attempt shipped.

    A pattern requiring `<div ...>...</div>` matched nothing, because the row
    capture ends at the first `</div>`. The result was a location column still
    reading "4 Locations" while every test of the pattern in isolation passed.
    """
    from jobradar.adapters.platforms import _JV_ROW, _JV_META
    m = _JV_ROW.search(_row("/dwt/job/a2", "x", COUNT_CELL))
    assert m, "the row itself no longer matches"
    assert "</div>" not in m.group(3), \
        "the capture now includes the closing tag; the split can be simplified"
    assert _JV_META.search(m.group(3)), "the meta div is no longer found"
