"""A currency mark that was not one of three symbols was read as one, or not at all.

Two faults, and the first is the worse of the two because it is confident.

`parse_text("S$120,000 - S$160,000")` returned `$120k`, `confirmed=True`. The
currency class in every pattern was `[£$€]`, so the leading S broke the RANGE
match; the single-value pattern then found "$120,000" sitting inside the same
string and matched that instead. Two errors out of one missing letter: the
figure was stamped USD when SGD is about 0.74 of it, and the top of the band
was silently deleted. `confirmed=True` is the part that hurts, because only a
confirmed figure is allowed to disqualify a posting against a floor. C$, A$,
NZ$, HK$ and R$ all did the same thing, so Canada, Australia, New Zealand,
Hong Kong and Brazil were being priced in dollars they do not use.

The second: `_CUR` already mapped "gbp", "usd" and "eur", and no pattern in
the file ever accepted a letter, so those three entries were dead code and
"USD 150,000", "150,000 USD", "SGD 120,000" and "INR 4,000,000" all came back
unconfirmed. US and Asian boards write the code far more often than the
symbol, so this was not an edge case, it was most of two markets.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.salary import clears_floor, parse_text  # noqa: E402


def test_a_dollar_with_a_country_letter_in_front_is_not_a_us_dollar():
    for text, want in (("S$120,000", "SGD"), ("C$140,000", "CAD"),
                       ("A$150,000", "AUD"), ("NZ$90,000", "NZD"),
                       ("HK$500,000", "HKD"), ("R$ 200,000", "BRL"),
                       ("US$95,000", "USD")):
        s = parse_text(text)
        assert s.confirmed, text
        assert s.currency == want, f"{text} priced in {s.currency}"


def test_a_prefixed_range_keeps_the_top_of_its_band():
    """The half of the bug nobody would have noticed from the currency alone.

    The range never matched, so the answer came from the single-value pattern,
    which knows about one number. 160,000 became 120,000 with no trace.
    """
    s = parse_text("S$120,000 - S$160,000")
    assert (s.min, s.max) == (120000.0, 160000.0), s
    assert s.currency == "SGD"


def test_a_wrongly_stamped_currency_is_what_deletes_a_role():
    """Why the currency matters at all, in the only place it can hurt.

    Read as USD, a Singapore band of 120,000 to 160,000 was a confirmed
    120,000 against a 130,000 dollar floor, so the posting was dropped. Read
    correctly it is a figure in another currency, which `clears_floor` refuses
    to compare and shows with a label instead.
    """
    keep, why = clears_floor(parse_text("S$120,000 - S$160,000"), 130000, "USD")
    assert keep is True, "a Singapore role was dropped on a US dollar floor"
    assert "not compared" in why


def test_an_iso_code_in_front_of_the_figure_is_read():
    for text, want, amount in (("USD 150,000", "USD", 150000.0),
                               ("SGD 120,000", "SGD", 120000.0),
                               ("INR 4,000,000", "INR", 4000000.0),
                               ("EUR 65,000", "EUR", 65000.0),
                               ("AED 400,000", "AED", 400000.0),
                               ("CAD 130,000", "CAD", 130000.0)):
        s = parse_text(text)
        assert s.confirmed, f"{text} came back unconfirmed"
        assert s.currency == want and s.max == amount, s


def test_an_iso_code_after_the_figure_is_read():
    """"150,000 USD" is as common as "USD 150,000" and neither worked."""
    s = parse_text("150,000 USD")
    assert s.confirmed and s.currency == "USD" and s.max == 150000.0, s


def test_a_range_with_the_code_trailing_it_keeps_its_currency():
    s = parse_text("Salary: 150,000 - 160,000 USD")
    assert (s.min, s.max) == (150000.0, 160000.0), s
    assert s.currency == "USD" and s.confirmed


def test_the_code_and_the_symbol_written_together():
    """"AUD$100,000" used to match on its bare "$" and come back as USD."""
    s = parse_text("AUD$100,000")
    assert s.currency == "AUD" and s.max == 100000.0, s


def test_a_day_rate_carries_its_country_dollar_too():
    s = parse_text("C$800 per day")
    assert s.currency == "CAD" and s.period == "day" and s.max == 800.0, s


def test_a_lower_case_code_is_not_a_currency():
    """The reason the ISO patterns only read capitals.

    These patterns are case-insensitive, and several currency codes are
    ordinary English words. Lower-cased, "try" would turn a sentence about
    effort into a confirmed salary of 100,000 Turkish lira, and confirmed is
    the only kind of figure that can delete a role.
    """
    s = parse_text("We try 100,000 to 200,000 requests a second on that box.")
    assert not s.confirmed, f"a sentence about throughput parsed as pay: {s}"


def test_a_word_that_starts_with_a_currency_code_is_not_one():
    """A guard on the fix rather than a bug that happened. Accepting letters
    at all is what makes this possible, and USDA, CADENCE and TRYING all start
    with a currency code."""
    s = parse_text("The USDA 100,000 to 120,000 acre programme")
    assert not s.confirmed, f"USDA was read as a currency: {s}"


def test_the_plain_symbols_still_mean_what_they_always_did():
    """The other guard on the fix. Every one of these worked before and the
    whole change is worthless if any of them stopped."""
    for text, want, lo, hi in (("Salary £60,000 - £70,000", "GBP", 60000.0, 70000.0),
                               ("Pay: $120,000", "USD", 120000.0, 120000.0),
                               ("Range: €90,000 to €110,000", "EUR", 90000.0, 110000.0)):
        s = parse_text(text)
        assert s.confirmed and s.currency == want, s
        assert (s.min, s.max) == (lo, hi), s


def test_a_stated_figure_survives_the_word_competitive():
    """The early exit for "we are not telling you" tested a literal `[£$€]`,
    so "Competitive, up to SGD 180,000" threw the figure away before any
    pattern saw it."""
    s = parse_text("Competitive salary, up to SGD 180,000 depending on experience")
    assert s.confirmed and s.currency == "SGD" and s.max == 180000.0, s
