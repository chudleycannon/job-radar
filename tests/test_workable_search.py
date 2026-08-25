"""jobs.workable.com, the aggregator, as opposed to the 2,094 employer boards.

The boards are 2,094 requests to one host every scan, which is fifty minutes
at the pace that host tolerates, and which earned a sixteen hour 429 at any
pace faster. The search answers the same question in six requests, carries the
full description so those roles need no enrichment pass, and reaches every
Workable employer rather than the ones a crawl happened to find.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters
from jobradar.models import Source
from jobradar.screen import country_name, dedupe, directness
from jobradar.sources import expand_templates

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workable_search.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _src() -> Source:
    return Source(company="Workable search: engineering manager",
                  platform="workable_search",
                  url="https://jobs.workable.com/api/v1/jobs?query=x")


def test_the_employer_comes_from_the_posting_not_the_source():
    """Every other parser here takes src.company, because for a board the
    source IS the employer. Here the source is a search, so taking it would
    file 110 unrelated employers under the literal string "Workable search:
    engineering manager", and dedupe groups on employer, so they would all
    have collapsed into each other."""
    jobs = adapters.parse(_payload(), _src())
    assert len(jobs) == 3
    assert {j.company for j in jobs} == {"Zego", "WalletConnect", "Runware"}
    assert not any("Workable search" in j.company for j in jobs)


def test_every_role_arrives_with_its_description():
    """The point of the search over the boards. A role with no description
    cannot be ranked or screened, so these would otherwise each need their own
    enrichment fetch."""
    jobs = adapters.parse(_payload(), _src())
    assert all(len(j.description or "") > 200 for j in jobs)
    assert all(j.location for j in jobs)
    assert all(j.url.startswith("https://jobs.workable.com/view/") for j in jobs)


def test_workable_says_whether_a_role_is_remote_so_we_do_not_guess():
    """`workplace` is one of remote, hybrid or on-site. Reading the location
    text instead, which is the fallback everywhere else, gets a hybrid role in
    London wrong in both directions: hide it, or offer one that cannot be done
    from where the user lives."""
    jobs = {j.company: j for j in adapters.parse(_payload(), _src())}
    assert jobs["WalletConnect"].remote is True      # workplace: remote
    assert jobs["Zego"].remote is False              # workplace: hybrid


def test_the_employers_own_board_beats_the_aggregator():
    """directness's own docstring: an aggregator not listed there is treated
    as an employer's own board, will not fold, and its repost shows as a
    second row beside the real vacancy. This one carries the full advert, so
    left at the default it would win on description length and hand the reader
    Workable's view page instead of the employer's apply link."""
    from jobradar.models import Job
    assert directness("workable_search") < directness("workable")

    board = Job(company="Zego", title="Engineering Manager", platform="workable",
                url="https://apply.workable.com/zego/j/ABC/", description="short")
    agg = Job(company="Zego", title="Engineering Manager",
              platform="workable_search",
              url="https://jobs.workable.com/view/xyz/", description="x" * 5000)
    kept = dedupe([agg, board])
    assert len(kept) == 1
    assert kept[0].url.startswith("https://apply.workable.com/"), (
        "the longer aggregator advert beat the employer's own link")


def test_a_search_is_narrowed_to_the_countries_the_user_named():
    """"software engineer" worldwide is 4,220 postings, 211 pages behind an
    opaque cursor. The same search in the United Kingdom is 322."""
    tmpl = Source(company="Workable search", platform="workable_search",
                  keyword_template=True,
                  url="https://jobs.workable.com/api/v1/jobs"
                      "?query={keyword}&location={country}")
    out = expand_templates([tmpl], ["engineering manager"], ["UK", "IE"])
    assert len(out) == 2
    assert "location=United+Kingdom" in out[0].url
    assert "location=Ireland" in out[1].url
    assert "in United Kingdom" in out[0].company, "the label has to say which"


def test_an_unknown_country_code_does_not_become_a_literal_search_term():
    """A "ZZ" in the query returns nothing, which reads as "no jobs there"
    rather than "that is not a country". One unfiltered search returns too
    much, which the title filter then cuts, and that is the honest failure."""
    assert country_name("ZZ") == ""
    tmpl = Source(company="Workable search", platform="workable_search",
                  keyword_template=True,
                  url="https://jobs.workable.com/api/v1/jobs"
                      "?query={keyword}&location={country}")
    out = expand_templates([tmpl], ["engineering manager"], ["ZZ"])
    assert len(out) == 1
    assert out[0].url.endswith("location=")


def test_a_source_with_no_country_placeholder_is_untouched():
    """NHS Jobs and LinkedIn expand by title alone and must keep doing so."""
    tmpl = Source(company="NHS Jobs", platform="nhs", keyword_template=True,
                  url="https://www.jobs.nhs.uk/x?keyword={keyword}")
    out = expand_templates([tmpl], ["nurse", "matron"], ["UK", "IE"])
    assert len(out) == 2, "two titles, not two titles times two countries"


def test_the_search_is_registered_and_reachable_from_its_url():
    p = adapters.detect("https://jobs.workable.com/api/v1/jobs?query=x")
    assert p.name == "workable_search"
    # The employer boards must not be caught by the same rule.
    assert adapters.detect(
        "https://apply.workable.com/api/v1/widget/accounts/zego").name == "workable"
    assert "location=" not in (adapters.by_name("workable_search").build("x") or "")
