"""A bare number was priced in the reader's currency, not the job's.

`enrich` passed `cfg.salary_currency` -- the reader's own floor currency --
as the default for any figure with no symbol on it, anywhere in the world. So
an Indian posting reading

    Annual salary: 900,000 to 1,100,000

was stored, confirmed, as "$900k - $1,100k" for a reader whose floor was in
dollars. That is about 8,500 pounds presented as most of a million, with
`confirmed=True`, so it could clear a floor it comes nowhere near, and it
would have read as a headline result at the top of the list.

The reader's own currency is the one thing that cannot be evidence about what
an employer meant. The job's country can. Where the country is unknown the
honest answer is to say nothing: `clears_floor` already refuses to compare a
figure with no currency and labels it "not compared", so the role is shown
rather than judged. A figure carrying its own symbol never reaches any of
this.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.salary import (clears_floor, currency_of_country,  # noqa: E402
                            parse_text)

BARE = "Annual salary: 900,000 to 1,100,000"


def test_a_bare_figure_takes_the_currency_of_the_country_it_is_in():
    for country, want in (("IN", "INR"), ("UK", "GBP"), ("US", "USD"),
                          ("DE", "EUR"), ("SG", "SGD"), ("AE", "AED")):
        s = parse_text(BARE, currency_of_country(country))
        assert s.currency == want, f"{country} priced in {s.currency}"


def test_an_unknown_country_produces_no_currency_rather_than_a_guess():
    for country in (None, "", "ZZ", "multiple", "unplaced"):
        assert currency_of_country(country) is None, country
    s = parse_text(BARE, currency_of_country(None))
    assert s.currency is None


def test_a_figure_with_no_currency_is_never_compared_to_a_floor():
    """The safety this leans on. It has to keep working or the change above
    turns a wrong comparison into a wrong drop."""
    s = parse_text(BARE, None)
    for floor, cur in ((140000, "GBP"), (48000, "USD"), (4000000, "INR")):
        keep, why = clears_floor(s, floor, cur)
        assert keep is True, f"a role was dropped on a figure with no currency"
        assert "not compared" in why


def test_a_symbol_in_the_advert_still_wins_over_everything():
    for text, want in (("Salary £140,000 - £160,000", "GBP"),
                       ("Pay: $120,000", "USD"),
                       ("Range: €90,000 to €110,000", "EUR")):
        assert parse_text(text, currency_of_country("IN")).currency == want


def test_the_euro_countries_are_all_there():
    for cc in ("DE", "FR", "NL", "ES", "IT", "IE", "PT", "AT", "BE", "FI"):
        assert currency_of_country(cc) == "EUR", cc


def test_enrich_asks_for_the_country_it_needs():
    """The fix is only real if the row carries the column.

    `candidates()` selects a fixed list, and reading `r["country"]` from a row
    that never selected it would raise or, worse, quietly fall back.
    """
    import inspect
    from jobradar import enrich
    src = inspect.getsource(enrich.candidates)
    assert "r.country" in src, "candidates() no longer selects the country"


def test_enrich_does_not_reach_for_the_readers_floor_currency():
    import inspect
    from jobradar import enrich
    src = inspect.getsource(enrich.run)
    assert "cfg.salary_currency" not in src, \
        "the reader's own floor currency is back in the salary parse"
