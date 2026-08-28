"""The rupee, four ways, all of them found on the seed published 2026-08-28.

INR floors were enabled the same day, and this is what a reader in Bengaluru
with a floor of 4,000,000 actually got.

The dangerous one first. India writes a lakh as "16,50,000": two digits, then
groups of TWO, then a final group of three. The number pattern knew the Anglo
grouping and the European one and not that, so the whole figure failed to
match at its first digit, the scan walked forward, and the tail "50,000"
matched on its own. DualEntry's advert, which is in the seed word for word,
says "India: Rs16,50,000 - Rs21,78,000 INR" and parsed as a confirmed 78,000
INR. Twenty-eight times too small, confirmed, and therefore deleted outright
by a floor it clears in reality. Only a confirmed figure is allowed to
disqualify a role, which is why reading a number wrong is worse than not
reading it.

The quiet one second. The rupee sign was not a currency mark at all, so the
101 Indian adverts in the seed that price themselves with one were invisible:
Litmos state "Salary Range: Rs22,00,000 to Rs28,00,000" and came back
unconfirmed, so the figure could neither hide the role nor clear the floor. A
floor in rupees was switched on over a market whose adverts it could not
read. Worse in the range case, because the sign is not whitespace: Airbnb's
"Rs3,080,000 - Rs4,400,000 INR" could not match as a range at all and fell
through to the single-value pattern on the trailing ISO code, reporting the
top of the band as though it were the whole of it.

Third, teaching the parser the sign without the unit made things worse rather
than better, which is why the unit is in here too. India states pay in lakhs
as often as in digits, and Eulerity's "Compensation: Rs13-16 LPA" started
matching as a day rate of thirteen rupees: a 1.3M to 1.6M salary about to be
hidden behind a 4,000,000 floor it fails by three orders of magnitude.

Fourth is not about rupees at all, it just showed up on the same adverts. The
second pass over a description gave up on the whole block at the first number
that was not being discussed as pay, so one "10,000 customers" high in an
advert deleted the real range further down. 400 adverts of 33,918 came back
"unconfirmed salary" while stating one in plain dollars.

The sign is written as ₹ throughout rather than pasted, so that a terminal or
an editor that cannot render it cannot silently change what is being tested.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.salary import clears_floor, parse_text  # noqa: E402

RS = "₹"


def test_a_lakh_grouped_figure_is_not_sliced_into_its_last_six_digits():
    """Verbatim from the DualEntry advert in the published seed."""
    s = parse_text(f"Base Salary India: {RS}16,50,000 - {RS}21,78,000 INR", "INR")
    assert s.confirmed
    assert s.currency == "INR"
    assert (s.min, s.max) == (1_650_000.0, 2_178_000.0), \
        f"read as {s.min}-{s.max}, and 78,000 was the shipped answer"


def test_a_lakh_grouped_figure_does_not_delete_a_role_it_clears():
    """The whole point of getting the number right.

    The floor is the only thing in this tool allowed to remove a role on the
    strength of a figure, so a figure read at a twenty-eighth of its value
    removes roles that pay nearly twice the floor.
    """
    s = parse_text(f"Base Salary India: {RS}16,50,000 - {RS}21,78,000 INR", "INR")
    keep, why = clears_floor(s, 4_000_000, "INR")
    assert not keep, why
    top = parse_text(f"Annual salary: {RS}45,00,000 to {RS}60,00,000", "INR")
    assert top.confirmed and top.max == 6_000_000.0
    assert clears_floor(top, 4_000_000, "INR")[0], \
        "a 60 lakh salary was hidden by a 40 lakh floor"


def test_an_anglo_grouped_figure_still_reads_the_way_it_always_did():
    """The Indian branch must not have taken any number off the other two.

    It sits after the Anglo alternative and the two are disjoint, but that is
    an argument and this is the check.
    """
    for text, lo, hi in ((f"Salary: $150,000 - $180,000", 150_000.0, 180_000.0),
                         ("Salary: 1,234,567 GBP", 1_234_567.0, 1_234_567.0),
                         ("€ 60.000 - € 75.000 per jaar", 60_000.0, 75_000.0),
                         ("Salary: £60k - £75k", 60_000.0, 75_000.0)):
        s = parse_text(text)
        assert s.confirmed, text
        assert (s.min, s.max) == (lo, hi), f"{text} read as {s.min}-{s.max}"


def test_the_rupee_sign_is_a_currency_mark():
    """Litmos, in the seed, on six separate adverts."""
    s = parse_text(f"Salary Range: {RS}22,00,000 to {RS}28,00,000 plus 10% bonus",
                   "INR")
    assert s.confirmed, "priced in rupees and read as no salary at all"
    assert s.currency == "INR"
    assert (s.min, s.max) == (2_200_000.0, 2_800_000.0)
    # And the same advert one grade up, which the floor must keep.
    up = parse_text(f"Salary Range: {RS}50,00,000 to {RS}70,00,000 plus 20% bonus",
                    "INR")
    assert clears_floor(up, 4_000_000, "INR")[0]


def test_a_rupee_marked_range_keeps_the_bottom_of_its_band():
    """Airbnb, in the seed, five adverts.

    The sign is not whitespace, so with it unknown the range pattern could
    not step over the second one and the trailing "INR" rescued the top
    figure alone. A band reported as its own ceiling is the same shape of
    error the S$ fix went after.
    """
    s = parse_text(f"India Annual Pay Range {RS}3,080,000 — {RS}4,400,000 INR",
                   "INR")
    assert s.confirmed and s.currency == "INR"
    assert s.min == 3_080_000.0, f"bottom of the band came back as {s.min}"
    assert s.max == 4_400_000.0


def test_lpa_is_a_hundred_thousand_a_year_and_not_thirteen_rupees_a_day():
    """Eulerity and Humanarchive, both in the seed.

    "PA" is per annum, so the unit overrides whatever period word happens to
    sit near the figure. It said "day" here, off an IST shift time elsewhere
    in the advert.
    """
    s = parse_text("Compensation: " + RS + "13-16 LPA (depending on experience)",
                   "INR")
    assert s.confirmed and s.currency == "INR"
    assert s.period == "year", f"stored as a rate per {s.period}"
    assert (s.min, s.max) == (1_300_000.0, 1_600_000.0)
    ctc = parse_text("COMPENSATION - Total CTC: " + RS + "4.2–7 LPA, based on "
                     "interview performance", "INR")
    assert (ctc.min, ctc.max) == (420_000.0, 700_000.0)
    # 2070Health write the bare unit, and it means the same thing.
    bare = parse_text("Compensation: base of [" + RS + "35–55L] plus an "
                      "aggressive performance variable", "INR")
    assert bare.confirmed and (bare.min, bare.max) == (3_500_000.0, 5_500_000.0)


def test_a_crore_of_revenue_is_not_a_salary():
    """The other half of teaching the parser a unit that multiplies by ten
    million. 2070Health describe their own business as "generating
    approximately RS1 crore in monthly revenue" in the body of a job advert,
    and a bare unit does not get to claim its own period either.
    """
    s = parse_text("currently generating approximately " + RS + "1 crore in "
                   "monthly revenue . This is not a strategy role", "INR")
    assert not s.confirmed, f"read the company's revenue as pay: {s.min}"
    # And a figure with no period word beside it either, which is where the
    # period rule above has nothing to say and pay context is the only thing
    # standing between a funding round and a salary of half a billion rupees.
    for text in (f"The company has raised {RS}50 crore in Series B funding "
                 f"to build the platform.",
                 f"Our merchants process {RS}12 lakh of orders through us "
                 f"every year."):
        got = parse_text(text, "INR")
        assert not got.confirmed, f"{text!r} read as pay of {got.min}"


def test_the_top_of_a_rate_band_may_carry_a_thousands_separator():
    """TechnologyAdvice, in the seed: "Hourly pay range RS500 - RS1,000 INR".

    The rate number pattern refuses a figure followed by a separator, which
    is right for a lone number and wrong for the far end of a range: the
    whole range failed on the "1,000" and the single-value rate pattern then
    reported the 500 as the entire band. Half a rate is half an annualised
    figure, and the floor deletes on that.
    """
    s = parse_text(f"Hourly pay range {RS}500 — {RS}1,000 INR", "INR")
    assert s.confirmed and s.period == "hour"
    assert (s.min, s.max) == (500.0, 1000.0), f"band read as {s.min}-{s.max}"
    # The narrow rate ranges that already worked have to keep working, and
    # they are the reason the rate pattern is tried first.
    hourly = parse_text("$19-$27 per hour")
    assert (hourly.min, hourly.max, hourly.period) == (19.0, 27.0, "hour")


def test_a_number_that_is_not_pay_does_not_end_the_search_for_one():
    """Cohere, DualEntry, Arize and 397 others in the seed.

    Past the opening block every figure has to be introduced by something
    that means pay, which is right. Giving up on the rest of the advert the
    first time one is not, is not: whichever unlabelled number came first won
    the right to decide the posting states no salary.
    """
    body = ("We are hiring. " + "Our platform serves 40,000 to 120,000 "
            "requests per second for 10,000 customers. " * 8 +
            "A" * 400 +
            " The base salary range for this role is $180,000 - $325,000.")
    s = parse_text(body)
    assert s.confirmed, "a stated range was read as no salary at all"
    assert (s.min, s.max, s.currency) == (180_000.0, 325_000.0, "USD")


def test_the_pay_context_gate_is_still_a_gate():
    """The fix above must not have turned it into a pass-through. An
    unsymbolled range in prose is still not a salary, however many of them
    the parser now walks past.
    """
    s = parse_text("Our platform grew from 25,000 to 90,000 members last year, "
                   "and we serve 40,000 to 120,000 requests per second.")
    assert not s.confirmed, f"read a member count as pay: {s.min}-{s.max}"
