"""Two loose ends from widening the currency list.

A salary label of "INR 4,000k" is correct and useless: the reader has to stop
and multiply it out, and at a glance it reads as four thousand.
Thousands-of-thousands was fine while a floor could only be pounds, dollars
or euros, where a salary rarely passes 1,000k. It stopped being fine the
moment a floor could be in rupees, yen or won.

And the dashboard parsed a role's `flags` column without a guard, in two
places, while `store.py` guards the same field. A malformed value there does
not lose one role's labels, it raises out of the page handler and takes the
whole board down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Salary  # noqa: E402


def _label(v, currency="INR", period="year"):
    return Salary(min=v, max=v, currency=currency, period=period,
                  confirmed=True).label()


def test_a_salary_in_the_millions_reads_as_millions():
    assert _label(4_000_000) == "INR 4M"
    assert _label(12_000_000, "JPY") == "JPY 12M"
    assert _label(1_500_000) == "INR 1.5M"


def test_the_thousands_form_is_unchanged_below_a_million():
    assert _label(120_000, "SGD") == "SGD 120k"
    assert _label(150_000, "USD") == "$150k"
    assert _label(140_000, "GBP") == "£140k"


def test_small_and_non_annual_figures_are_untouched():
    assert _label(600, "GBP", "day") == "£600/day"
    assert _label(45, "GBP", "hour") == "£45/hr"
    assert _label(9_000, "GBP") == "£9,000"


def test_a_range_in_the_millions_reads_on_both_sides():
    s = Salary(min=4_000_000, max=6_000_000, currency="INR", confirmed=True)
    assert s.label() == "INR 4M - INR 6M"


def test_an_unconfirmed_salary_says_so_whatever_the_number():
    assert Salary(min=4_000_000, currency="INR").label() == "unconfirmed salary"


def test_a_malformed_flags_column_loses_the_labels_not_the_dashboard():
    from jobradar.output.interactive import _flags
    for bad in ("{not json", '"a string"', "{}", "123", None, ""):
        assert _flags({"flags": bad}) == [], bad


def test_a_good_flags_column_still_comes_back_whole():
    from jobradar.output.interactive import _flags
    assert _flags({"flags": '["soft flag: hybrid", "posted this week"]'}) \
        == ["soft flag: hybrid", "posted this week"]


def test_nothing_parses_that_column_unguarded_any_more():
    """Both call sites, not just the one that was noticed."""
    from jobradar.output import interactive
    src = Path(interactive.__file__).read_text(encoding="utf-8")
    assert 'json.loads(r["flags"]' not in src
