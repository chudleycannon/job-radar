"""Regressions introduced by this morning's fixes, found by re-running users.

Four of them. Each is a fix that worked on the case it was written for and
broke, or failed to reach, something beside it.

- `regions_in` was wired into `match` and nowhere else, so a role in "Remote
  - Europe" passed the filter and then scored twenty points BELOW a bare
  "Remote", which is scored as naming nowhere.
- The citizenship pattern let any three words sit between the verb and the
  noun, so "a good corporate citizen" read as a bar on working here.
- European decimals became readable without the month vocabulary that has to
  go with them, so "€8.000 im Monat" was confirmed as an annual 8,000 and
  deleted by the floor with a reason stating the opposite of the advert.
- The written dashboard read the board BEFORE the scan's own cleanup, so it
  showed roles the same run had closed and a merged duplicate twice.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config          # noqa: E402
from jobradar.models import Job             # noqa: E402
from jobradar.salary import parse_text      # noqa: E402
from jobradar.screen import enrich, score, work_rights  # noqa: E402

BODY = "We are hiring a marketing manager to run our campaigns. " * 8


def _scored(location, countries=("GR",)):
    cfg = Config()
    cfg.titles_include = ["marketing manager"]
    cfg.countries = list(countries)
    job = Job(company="Acme", title="Marketing Manager", url="https://x/1",
              platform="ashby", location=location, description=BODY)
    return score(job, cfg), job


def test_a_region_that_contains_your_country_scores_like_your_country():
    europe, _ = _scored("Remote - Europe")
    athens, _ = _scored("Athens, Greece")
    nowhere, _ = _scored("Remote")
    assert europe == athens, (europe, athens)
    assert europe > nowhere, "the qualifier that helps still costs points"


def test_a_region_that_does_not_contain_your_country_does_not():
    namer, _ = _scored("Remote - NAMER")
    nowhere, _ = _scored("Remote")
    assert namer == nowhere, (namer, nowhere)


def test_the_country_facet_can_place_a_region():
    _, job = _scored("Remote - Europe")
    enrich(job)
    assert job.country == "multiple", job.country


def _rights(text):
    return work_rights(Job(company="Acme", title="Engineer", url="https://x/1",
                           platform="ashby", description=text))


def test_a_good_corporate_citizen_is_not_a_bar_on_working_here():
    for text in ("You must be a good corporate citizen and a team player.",
                 "You must be a self starter and a strong communicator.",
                 "We are comfortable working with nationals of many countries."):
        assert _rights(text) != "no sponsorship", text


def test_a_real_citizenship_requirement_still_is_one():
    for text in ("Applicants must be Singapore Citizens or Permanent Residents.",
                 "Applicants must be US citizens.",
                 "You must be a British citizen.",
                 "Applicants must be permanent residents."):
        assert _rights(text) == "no sponsorship", text


MONTHLY = (
    "salary: €8.000 im Monat", "salary: €8.000/maand",
    "€5.000 μηνιαίως",
    "€8.000 par mois", "€8.000 al mes", "€8.000 mensile",
    "€8.000/mo", "€8.000 p.m.", "$6k-$10k/mo OTE",
    "€8.000 per maand", "€8.000 per month",
)


def test_a_monthly_figure_is_never_confirmed_as_an_annual_one():
    """The floor deletes on a confirmed figure. A monthly salary read as
    annual is a 96,000 job hidden with the reason "pay below floor", which
    states the opposite of the advert."""
    wrong = [t for t in MONTHLY if parse_text(t).confirmed]
    assert not wrong, f"read as an annual figure: {wrong}"


def test_an_annual_figure_still_confirms():
    for text in ("€90.000 per jaar", "£140,000 per annum",
                 "€90.000 - €110.000", "$150,000 a year"):
        assert parse_text(text).confirmed, text


def test_the_page_is_written_after_the_scans_own_cleanup():
    """It was written from a snapshot taken before `merge_duplicates` and the
    enrichment pass, so it showed roles that run had just closed and a merged
    duplicate twice, at a uid no longer in the database."""
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_scan)
    snapshot = src.index("board = kept if args.dry_run")
    for later in ("merge_duplicates", "_enrich_step"):
        assert src.index(later) < snapshot, \
            f"the board is read before {later} again"
