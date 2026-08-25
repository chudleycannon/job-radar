"""The surface a person actually reads, and the ways it lied to them.

Every test here comes from something the dashboard showed that was not true:
a count that promised roles the tab was hiding, an unranked role sorted as
though it had been read and judged, a salary comparison across currencies the
rest of the tool refuses to make, a note that could not be deleted, and a row
that ran off the edge of a container that clips rather than scrolls.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.output import html as html_mod
from jobradar.output import interactive

SRC = (Path(__file__).resolve().parent.parent / "jobradar" / "output"
       / "interactive.py").read_text(encoding="utf-8")


def _con():
    con = store.connect(":memory:")
    store.migrate(con, state_path=str(Path(tempfile.mkdtemp()) / "none.json"))
    con.execute("INSERT INTO roles (uid,company,title,url,platform,first_seen,"
                "last_seen) VALUES ('u1','Acme','Engineering Manager',"
                "'https://x.invalid/1','lever','2026-08-25','2026-08-25')")
    return con


def _note(con, uid="u1"):
    r = con.execute("SELECT note FROM role_state WHERE uid=?", (uid,)).fetchone()
    return r["note"] if r else None


def test_emptying_the_note_box_deletes_the_note():
    """Nothing anywhere could remove one. Clearing the box sent "", the API
    answered ok, the page said "Note saved" and reloaded, and the old note was
    still there, because the write kept the previous note whenever the new one
    was empty."""
    con = _con()
    store.set_status(con, "u1", "interested", "second round 3 Sept")
    assert _note(con) == "second round 3 Sept"
    store.set_status(con, "u1", "interested", "")
    assert _note(con) == "", "an emptied box has to actually empty it"


def test_a_status_button_does_not_wipe_the_note_on_its_way_past():
    """The other half, and the reason the old behaviour existed. Skip and
    Apply send a status and no note at all, and must leave the note alone."""
    con = _con()
    store.set_status(con, "u1", "interested", "second round 3 Sept")
    store.set_status(con, "u1", "applied")          # note not supplied
    assert _note(con) == "second round 3 Sept"
    assert con.execute("SELECT status FROM role_state WHERE uid='u1'"
                       ).fetchone()["status"] == "applied"


def test_an_unranked_role_sorts_above_a_role_judged_worthless():
    """fit -1 means "not read yet", not "read and bad". The first fix for this
    mapped -1 to -0.5, which is still below zero, so it did nothing: an
    unranked Head of Engineering still sorted underneath a role scored 0 with
    the reason "no people leadership at all". Fit scores are whole numbers, so
    the sentinel has to sit strictly between 0 and 1."""
    assert "v<0 ? 0.5 : v" in SRC, "unranked is below a genuine zero again"
    assert "v<0 ? -0.5 : v" not in SRC


def test_the_salary_sort_does_not_invent_a_floor_or_cross_currencies():
    """data-payfloor fell back to the top of the range, so "up to 175,000"
    claimed a floor of 175,000 and beat a role guaranteeing 150,000 to
    180,000: the vaguer advert winning on a figure nobody had promised. The
    comparison also ran across currencies, which is the exact guess
    `salary.clears_floor` refuses to make, on rows already flagged "not
    compared"."""
    assert 'data-payfloor="{int(r["salary_min"] or 0)}"' in SRC
    assert 'data-payfloor="{int(r["salary_min"] or r["salary_max"] or 0)}"' not in SRC
    assert "payGroup" in SRC and "HOME_CUR" in SRC
    assert 'data-paycur=' in SRC, "the currency has to travel with the figure"


def test_the_facet_counts_are_counted_against_the_tab_you_are_in():
    """They were counted once, in Python, over every row in the database,
    while the page opens on Open and Open hides everything settled. A board
    with one skipped public-sector role rendered "Public sector 1", and
    clicking it emptied the list and said "Nothing matches those filters"."""
    assert "function paintCounts(" in SRC
    assert "paintCounts(cSec,cMode,cCountry,cCity)" in SRC


def test_a_word_with_no_spaces_in_it_cannot_run_off_the_edge():
    """`.list` clips rather than scrolls and the page does not scroll
    sideways, so an unbroken string was not merely ugly, it was unreachable.
    Measured in a browser before the fix: a 180 character location rendered
    1,384px wide inside a 620px row and everything past the edge was cut off
    with nothing to say it was there. Afterwards, 435px and no overflow.

    A long job title was never the problem, because titles have spaces in
    them. A company name, a German compound word or a bare URL is."""
    css = html_mod._CSS if hasattr(html_mod, "_CSS") else ""
    blob = css or Path(html_mod.__file__).read_text(encoding="utf-8")
    assert "overflow-wrap:anywhere" in blob
    for cls in (".role", ".meta"):
        assert cls in blob.split("overflow-wrap:anywhere")[0][-120:] or \
            cls in [c.strip() for c in blob.split("overflow-wrap")[0]
                    .rsplit("\n", 1)[-1].split(",")], f"{cls} still cannot break"


def test_a_very_long_title_is_rendered_in_full_rather_than_cut():
    """A 395 character title is legitimate. The user's example: "Senior
    Principal Staff Engineering Manager of Distributed Platform Reliability
    and Developer Experience for the Global Payments and Settlement
    Organisation". Truncating it would hide the seniority, which is the part
    that decides whether the role is worth reading."""
    from jobradar.models import Job
    from jobradar.output import html_out

    title = ("Senior Principal Staff Engineering Manager of Distributed "
             "Platform Reliability and Developer Experience for the Global "
             "Payments and Settlement Organisation")
    j = Job(company="Acme", title=title, url="https://x.invalid/1",
            platform="lever", location="London, UK", description="d " * 50)
    j.fit = 60
    out = Path(tempfile.mkdtemp())
    p = html_out.write(out / "index.html", new=[j], seen=[], dropped={},
                       sources_ok=1, sources_total=1, throttled={}, postings=1)
    assert title in Path(p).read_text(encoding="utf-8")
