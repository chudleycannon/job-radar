"""Avature writes a job's location four different ways, and one was read.

`list-item-location` was already handled. The other three put the place on an
unlabelled subtitle strip, and every role on those boards was stored with no
location. Real markup from real boards, 2026-08-27:

  Frequentis  ...Offer Management | Österreich | Wien | FREQUENTIS AG
  Lenovo      <span>United States of America, North Carolina, Whitsett</span>
  Xerox       <p><span>City:</span> Webster</p><p><span>State:</span> New York</p>

Nothing on those strips says which part is the location and no two boards
order them the same way, so reading by position would be a guess dressed up
as a parse. Each part is offered to the country logic the tool already uses
to place a role, and a labelled part is taken on its label instead.

Re-parsing one set of payloads, 328 roles from 25 real boards, before and
after:

  no location   70.4%  ->  16.2%
  no country    72.6%  ->  18.3%

What is left is the European Central Bank, Mercadona and Avature's own demo
board, which put no location on the listing page at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.adapters.platforms import _av_subtitle_place  # noqa: E402
from jobradar.screen import _country_of                     # noqa: E402

FREQUENTIS = ('<div class="list__item__text__subtitle collapaseIcon"> Public '
              'Safety &amp; Transport Offer Management | Österreich | Wien | '
              'FREQUENTIS AG</div>')
LENOVO = ('<div class="article__header__text__subtitle"> <span> United States '
          'of America, North Carolina, Whitsett </span><br> <span> Req #: '
          'WD00097880 </span></div>')
XEROX = ('<div class="article__header__text__subtitle"> <span> Ref #20040641 '
         '</span> <p><span class="text--bold">City:</span> Webster</p> '
         '<p><span class="text--bold">State/Province:</span> New York</p></div>')


def test_pipe_separated_parts_are_sorted_with_the_country_last():
    assert _av_subtitle_place(FREQUENTIS, "Frequentis") == "Wien, Österreich"


def test_a_bare_comma_separated_span_is_kept_whole():
    assert (_av_subtitle_place(LENOVO, "Lenovo")
            == "United States of America, North Carolina, Whitsett")


def test_a_labelled_city_and_state_are_both_kept_in_their_own_order():
    """The label is better evidence than a lookup, and "Webster" alone is not
    a place any city list would find."""
    assert _av_subtitle_place(XEROX, "Xerox") == "Webster, New York"


def test_every_shape_produces_something_the_country_filter_can_read():
    for html, co in ((FREQUENTIS, "Frequentis"), (LENOVO, "Lenovo"),
                     (XEROX, "Xerox")):
        got = _av_subtitle_place(html, co)
        assert _country_of(got), f"{got!r} does not resolve to a country"


def test_the_requisition_number_and_the_department_are_dropped():
    for noise in ("Req #: WD00097880", "20040641", "Offer Management"):
        assert noise not in _av_subtitle_place(LENOVO, "Lenovo")
        assert noise not in _av_subtitle_place(XEROX, "Xerox")


def test_the_employers_own_name_is_not_read_as_a_place():
    """A company named after a city would otherwise become its location."""
    html = ('<div class="article__header__text__subtitle"><span>Boston</span>'
            '<br><span>London, United Kingdom</span></div>')
    assert _av_subtitle_place(html, "Boston") == "London, United Kingdom"


def test_a_strip_with_no_place_on_it_returns_nothing_rather_than_a_guess():
    html = ('<div class="article__header__text__subtitle"><span>Req #: 12345'
            '</span><br><span>Full time</span></div>')
    assert _av_subtitle_place(html, "Acme") == ""


def test_no_subtitle_at_all_is_not_an_error():
    assert _av_subtitle_place("<div>nothing here</div>", "Acme") == ""


def test_a_span_label_is_not_split_from_its_value():
    """This is the bug the first attempt shipped.

    Splitting candidates on `</span>` separated "City:" from "Webster", so the
    city was dropped and only the state survived. A test rather than a comment
    because it renders as a plausible location either way.
    """
    assert _av_subtitle_place(XEROX, "Xerox").startswith("Webster")
