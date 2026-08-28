"""A band that straddles the floor was reported as clearing it.

Both the filter and the score read the top of the band, which is deliberate
and argued on `Salary.top`: 100k-150k against a 120k floor is a role you would
still talk to them about. The sentence printed next to it was not so careful.

Found by importing the published seed as a senior product designer with a
$150,000 floor. 32 of her 179 priced roles were told "comfortably above your
floor" while advertising a bottom below $150,000, Accenture Federal Services'
"$90k - $184k" among them. That is not a filter that let something through:
it is the tool stating, in a sentence written for the reader, something the
advert on the same row contradicts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config          # noqa: E402
from jobradar.models import Job, Salary     # noqa: E402
from jobradar.screen import score           # noqa: E402

CLAIM = "comfortably above your floor"


def _cfg():
    c = Config()
    c.titles_include = ["product designer"]
    c.countries = ["US"]
    c.salary_floor = 150000
    c.salary_currency = "USD"
    return c


def _job(lo, hi, period="year"):
    return Job(company="Acme", title="Senior Product Designer",
               url="https://acme.example/jobs/1", platform="greenhouse",
               location="Remote - US", description="A product design role.",
               salary=Salary(min=lo, max=hi, currency="USD", period=period,
                             confirmed=True))


def test_a_band_that_starts_below_the_floor_does_not_claim_to_clear_it():
    job = _job(90000, 184000)
    score(job, _cfg())
    assert CLAIM not in job.reasons, \
        "a $90k-$184k band was called comfortably above a $150k floor"
    assert any("bottom is not" in r for r in job.reasons)


def test_a_band_wholly_above_the_floor_still_says_so():
    job = _job(180000, 240000)
    score(job, _cfg())
    assert CLAIM in job.reasons


def test_the_points_are_unchanged_either_way():
    """Only the sentence moved.

    The top-of-band rule is the documented one and this is not the place to
    relitigate it, so a band that straddles the floor must score exactly what
    it scored before. If these two ever diverge, somebody has changed the
    filter while meaning to change the wording.
    """
    straddles, clears = _job(90000, 184000), _job(180000, 240000)
    cfg = _cfg()
    assert score(straddles, cfg) == score(clears, cfg)


def test_an_hourly_band_is_annualised_before_the_claim_is_made():
    """The floor is annual and the bottom of the band may not be.

    $60/hour is about $105,600 a year against a $150,000 floor, so this band
    straddles it. Comparing the bare 60 would have called it below the floor
    by accident and got the right answer for the wrong reason; $120/hour is
    about $211,200 and genuinely clears it.
    """
    straddles = _job(60, 120, period="hour")
    score(straddles, _cfg())
    assert CLAIM not in straddles.reasons

    clears = _job(100, 130, period="hour")
    score(clears, _cfg())
    assert CLAIM in clears.reasons
