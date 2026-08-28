"""The two sentences a dashboard shows when it has nothing to show.

Both of them were wrong, and both were wrong on the page a new user sees
first: a footer that told everybody a salary floor was hiding roles from them
whether or not they had one, and an empty state that blamed filters on a page
where every filter was off.

The scan that produced the page knew the truth in both cases and printed it to
a terminal instead. These tests are the two renderers being made to say what
the run actually did.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.output import html as html_mod
from jobradar.output import interactive


def _static(jobs=(), **kw):
    """The static dashboard, with the arguments `cli.cmd_scan` really passes."""
    args = dict(dropped={}, sources_ok=0, sources_total=0, throttled=[],
                postings=0)
    args.update(kw)
    return html_mod.render(list(jobs), [], **args)


def _footer(page: str) -> str:
    m = re.search(r"<footer>(.*?)</footer>", page, re.S)
    assert m, "the page lost its footer"
    return " ".join(m.group(1).split())


def _empty(page: str) -> str:
    m = re.search(r'<div class="empty"[^>]*>(.*?)</div>', page, re.S)
    assert m, "the page lost its empty state"
    return " ".join(m.group(1).split())


def _con():
    con = store.connect(":memory:")
    store.migrate(con, state_path=str(Path(tempfile.mkdtemp()) / "none.json"))
    return con


def _role(con, uid="u1", flags=()):
    con.execute(
        "INSERT INTO roles (uid,company,title,url,platform,first_seen,last_seen,"
        "flags) VALUES (?,'Acme','Engineering Manager','https://x.invalid/1',"
        "'lever','2026-08-25','2026-08-25',?)",
        (uid, json.dumps(list(flags))))
    return con


# ------------------------------------------------------- the floor sentence

def test_a_reader_with_no_salary_floor_is_not_told_one_is_hiding_roles():
    """The static footer read "Roles with a stated salary below your floor are
    hidden" unconditionally. `floor: null` is what the setup wizard writes when
    somebody answers "I do not know" to the salary question, so that sentence
    was false on the default first dashboard, and it was the only thing on the
    page offering an explanation for a role that was missing."""
    page = _static(dropped={"title did not match": 40}, sources_ok=4,
                   sources_total=4, postings=40)
    assert "floor" not in _footer(page), (
        "the footer claims a salary floor hid something when nothing "
        "recorded a floor drop")


def test_the_floor_sentence_comes_back_with_a_number_when_a_floor_did_hide_roles():
    """The other half. Removing the claim entirely would lose a real
    explanation, so it has to return whenever `screen.run` actually counted
    postings dropped for that reason, and the count is the useful part."""
    page = _static(dropped={"stated pay below floor": 12}, sources_ok=4,
                   sources_total=4, postings=12)
    assert "12 roles with a stated salary below your floor are hidden" in _footer(page)


def test_one_role_below_the_floor_is_not_reported_as_plural():
    page = _static(dropped={"stated pay below floor": 1}, sources_ok=1,
                   sources_total=1, postings=1)
    assert "1 role with a stated salary below your floor is hidden" in _footer(page)


def test_the_live_footer_does_not_invent_a_floor_either():
    """`interactive.render` is handed a connection and a currency and never
    the config, so it had no way to check the same sentence and asserted it
    anyway. A role kept despite an unstated salary carries the flag
    `salary.clears_floor` produces only when a floor exists, so the rows
    themselves answer it."""
    con = _role(_con(), flags=[])
    assert "floor" not in _footer(interactive.render(con))

    con = _role(_con(), flags=["unconfirmed salary"])
    assert ("Roles with a stated salary below your floor are hidden"
            in _footer(interactive.render(con)))


def test_a_cross_currency_flag_is_also_proof_of_a_floor():
    """The other reason `clears_floor` keeps a role and files a flag. Checking
    only for the bare "unconfirmed salary" string would miss a board whose
    salaries are all stated and all foreign."""
    con = _role(_con(), flags=["salary in USD, floor in GBP, not compared"])
    assert ("Roles with a stated salary below your floor are hidden"
            in _footer(interactive.render(con)))


# ---------------------------------------------------------- the empty state

def test_an_empty_first_run_does_not_blame_filters_that_are_all_off():
    """"Nothing matches those filters." on a page whose filters are every one
    of them on All. It is false, and the next step it implies is to go and
    change a filter that is not doing anything."""
    page = _static(dropped={"title did not match": 5000},
                   sources_ok=198, sources_total=17811, postings=5000)
    assert "Nothing matches those filters" not in page
    assert "Nothing got through" in _empty(page)


def test_the_empty_page_carries_the_diagnostic_the_cli_printed_and_the_page_did_not():
    """The scan knew how many sources answered, how many postings arrived and
    why each one went, said all of it to a terminal, and then wrote a page
    that said "Nothing matches those filters." Every number below was already
    an argument to `render`."""
    page = _static(dropped={"title did not match": 4102,
                            "location not recognised": 891,
                            "stated pay below floor": 300},
                   sources_ok=198, sources_total=17811, postings=5293)
    body = _empty(page)
    assert "198" in body and "17,811" in body, "does not say how much was read"
    assert "5,293" in body, "does not say how many postings arrived"
    assert "4,102" in body and "title did not match" in body
    assert "titles.include" in body, "no next step"


def test_the_empty_page_accounts_for_the_postings_the_dedupe_swallowed():
    """The drop reasons are counted after duplicates are merged and `postings`
    is counted before, so a heading promising to account for every posting
    leaves an unexplained hole. On the CLI that gap was 891 on a 300-board run
    and nothing said why, which is the reader's arithmetic going wrong rather
    than ours."""
    page = _static(dropped={"title did not match": 900},
                   sources_ok=10, sources_total=10, postings=1000)
    assert "merged" in _empty(page)
    assert "100" in _empty(page)


def test_a_scan_that_read_nothing_is_not_reported_as_an_empty_market():
    """Zero postings from zero answering sources is a broken scan, and it
    wants the opposite reaction from the reader to a scan that read the world
    and matched none of it."""
    page = _static(dropped={}, sources_ok=0, sources_total=200, postings=0)
    body = _empty(page)
    assert "read nothing" in body
    assert "titles.include" not in body, "wrong advice: the titles are not the problem"


def test_the_filtered_empty_state_keeps_its_sentence_and_gains_a_way_out():
    """With rows on the page the filters really are the only thing that can
    empty it, so the original sentence was right. It just never said how to
    undo what you had done."""
    con = _role(_con())
    body = _empty(interactive.render(con))
    assert "Nothing matches those filters" in body
    assert "All" in body, "no way back to the full list"
    assert 'id="empty" hidden' in interactive.render(con), (
        "the filter message must start hidden, or a full board opens showing it")


def test_a_board_with_no_scan_behind_it_says_to_run_one():
    """A fresh clone between `setup` and the first `scan` has an empty
    database and was told its filters were hiding everything."""
    body = _empty(interactive.render(_con()))
    assert "No scan has run yet" in body
    assert "job-radar scan" in body


def test_a_board_that_scanned_and_stored_nothing_says_that_instead():
    """The other empty database, and a different problem: the scan ran and
    nothing survived the filters, which is a config to look at rather than a
    command to run."""
    con = _con()
    store.set_meta(con, "runs", "3")
    body = _empty(interactive.render(con))
    assert "No scan has run yet" not in body
    assert "titles.include" in body


def test_neither_empty_state_leaves_the_element_the_javascript_looks_for_behind():
    """Both pages toggle `#empty` from script. Renaming or dropping the id
    would break the filter view silently, which is how a dashboard ends up
    showing an empty list and no explanation at all."""
    for page in (_static(), _static([], dropped={"x": 1}, postings=1),
                 interactive.render(_con()),
                 interactive.render(_role(_con()))):
        assert page.count('id="empty"') == 1
