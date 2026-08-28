"""Two things the dashboard said that were not true.

A posting's age only ever ADDED points. Fresh got +15 or +8 and everything
older got nothing, so a two-year-old listing was scored as though its date
were unknown and sat wherever the rest of the scoring left it. Measured on one
board: 89 of 442 roles were over 180 days old, 26 over a year, the oldest
posted 2022-02-23, and a 2023 posting scored 85 and outranked fresher roles
with nothing anywhere saying how old it was.

And `score` printed `salary.raw`, which is the snippet the parser matched in.
On Greenhouse that is routinely the HEADING sitting in the same field as the
numbers, so the top row of a dashboard read "pay stated (Annual base salary
range (excluding equity and bonus):)" beside a perfectly good label of INR
6.6M. The `None` case was fixed earlier and the heading case was not, which
is the same fault with something in the variable.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config    # noqa: E402
from jobradar.models import Job, Salary  # noqa: E402
from jobradar.screen import score     # noqa: E402

BODY = "We are hiring an engineer to build and run our payments platform. " * 6


def _scored(days_old=None, salary=None):
    cfg = Config()
    cfg.titles_include = ["engineer"]
    job = Job(company="Acme", title="Engineer", url="https://x/1",
              platform="greenhouse", location="London", description=BODY,
              posted_at=None if days_old is None else
              (date.today() - timedelta(days=days_old)).isoformat(),
              salary=salary or Salary())
    return score(job, cfg), job


def test_a_fresh_posting_still_outranks_an_old_one():
    fresh, _ = _scored(2)
    old, _ = _scored(400)
    assert fresh > old, (fresh, old)


def test_a_posting_over_six_months_old_says_so():
    _, job = _scored(200)
    assert any("200 days ago" in f for f in job.flags), job.flags
    assert any("still open" in f for f in job.flags), job.flags


def test_a_recent_posting_is_not_flagged():
    for days in (2, 15, 100, 179):
        _, job = _scored(days)
        assert not any("still open" in f for f in job.flags), days


def test_an_old_posting_is_flagged_and_not_dropped():
    """Some of those URLs still answer 200, and an employer who never takes a
    listing down is not the same as a role that has gone. Silence was the
    fault, not the presence of the role."""
    sc, job = _scored(900)
    assert sc > 0
    assert any("900 days ago" in f for f in job.flags)


def test_a_posting_with_no_date_is_untouched():
    _, job = _scored(None)
    assert not any("still open" in f for f in job.flags)


def test_a_heading_is_not_printed_where_the_pay_should_be():
    cfg = Config()
    cfg.titles_include = ["engineer"]
    cfg.salary_floor = 100000
    cfg.salary_currency = "GBP"
    # Empty is covered by `test_a_posting_with_no_pay_snippet_uses_the_label`
    # below rather than here: `"" in anything` is True, so asserting it is
    # absent can never fail and would be a test that guards nothing.
    for heading in ("Annual base salary range (excluding equity and bonus):",
                    "Local Pay Range", "Compensation"):
        job = Job(company="Acme", title="Engineer", url="https://x/1",
                  platform="greenhouse", location="London", description=BODY,
                  salary=Salary(min=140000, max=160000, currency="GBP",
                                confirmed=True, raw=heading))
        score(job, cfg)
        pay = [r for r in job.reasons if "pay stated" in r]
        assert pay, job.reasons
        assert heading not in pay[0], pay[0]
        assert "140" in pay[0], pay[0]


def test_a_real_figure_in_raw_is_still_preferred():
    """`raw` is the employer's own wording when it has one, and that is worth
    more than a reformatted label."""
    cfg = Config()
    cfg.titles_include = ["engineer"]
    cfg.salary_floor = 100000
    cfg.salary_currency = "GBP"
    job = Job(company="Acme", title="Engineer", url="https://x/1",
              platform="greenhouse", location="London", description=BODY,
              salary=Salary(min=140000, max=160000, currency="GBP",
                            confirmed=True, raw="£140,000 - £160,000 per annum"))
    score(job, cfg)
    assert any("per annum" in r for r in job.reasons), job.reasons


def test_a_posting_with_no_pay_snippet_uses_the_label():
    cfg = Config()
    cfg.titles_include = ["engineer"]
    cfg.salary_floor = 100000
    cfg.salary_currency = "GBP"
    for raw in ("", None):
        job = Job(company="Acme", title="Engineer", url="https://x/1",
                  platform="ashby", location="London", description=BODY,
                  salary=Salary(min=140000, max=160000, currency="GBP",
                                confirmed=True, raw=raw))
        score(job, cfg)
        pay = [r for r in job.reasons if "pay stated" in r]
        assert pay and "140k" in pay[0], (raw, job.reasons)
        assert "None" not in pay[0], pay[0]
