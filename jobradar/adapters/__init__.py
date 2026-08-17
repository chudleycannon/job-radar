"""Platform registry.

`REGISTRY` maps a platform name to how its URLs look, how to build one from a
token, and how to parse the response. Adding an ATS means adding one entry
here and one parser in `platforms.py`.

`verified` records whether the parser has been checked against live data from
that platform. Unverified ones are best-effort and are marked as such in the
README rather than quietly presented as equal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

from ..models import Job, Source
from . import platforms


@dataclass
class Platform:
    name: str
    url_re: str
    parse: Callable[[object, Source], Iterator[Job]]
    build: Callable[[str], str] | None = None
    method: str = "GET"
    verified: bool = False
    note: str = ""

    def matches(self, url: str) -> bool:
        return bool(re.search(self.url_re, url, re.I))


def _wd_body() -> dict:
    return {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


REGISTRY: list[Platform] = [
    Platform(
        "greenhouse",
        r"boards-api\.greenhouse\.io|job-boards(?:\.eu)?\.greenhouse\.io",
        platforms.parse_greenhouse,
        build=lambda t: (
            f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs"
            f"?content=true&pay_transparency=true"
        ),
        verified=True,
        note="pay_transparency=true is required for salary; content=true does not imply it",
    ),
    Platform(
        "ashby",
        r"api\.ashbyhq\.com/posting-api",
        platforms.parse_ashby,
        build=lambda t: (
            f"https://api.ashbyhq.com/posting-api/job-board/{t}"
            f"?includeCompensation=true"
        ),
        verified=True,
        note="200 + empty array for unknown tokens; validate on job count",
    ),
    Platform(
        "lever",
        r"api\.lever\.co/v0/postings",
        platforms.parse_lever,
        build=lambda t: f"https://api.lever.co/v0/postings/{t}?mode=json",
        verified=True,
        note="returns a bare top-level list",
    ),
    Platform(
        "workday",
        r"myworkdayjobs\.com/wday/cxs",
        platforms.parse_workday,
        method="POST",
        verified=True,
        note="POST only; 406 (not 404) for unknown tenants due to wildcard DNS",
    ),
    Platform(
        "workable",
        r"apply\.workable\.com/api",
        platforms.parse_workable,
        build=lambda t: f"https://apply.workable.com/api/v1/widget/accounts/{t}",
        verified=True,
    ),
    Platform(
        "smartrecruiters",
        r"api\.smartrecruiters\.com/v1/companies",
        platforms.parse_smartrecruiters,
        build=lambda t: f"https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100",
        verified=True,
        note="200 + empty content for unknown company ids",
    ),
    Platform(
        "linkedin",
        r"linkedin\.com/jobs-guest",
        platforms.parse_linkedin,
        verified=True,
        note="public guest endpoint, HTML cards, no description or salary",
    ),
    Platform(
        "nhs",
        r"jobs\.nhs\.uk/candidate/search",
        platforms.parse_nhs,
        build=lambda kw: (
            "https://www.jobs.nhs.uk/candidate/search/results?keyword="
            + kw.replace(" ", "+")
        ),
        verified=True,
        note="search page, not a board; JSON API is auth-gated and .rss returns HTML. "
             "Salary is usually stated because trusts publish Agenda for Change bands",
    ),
    Platform(
        "recruitee",
        r"\.recruitee\.com/api/offers",
        platforms.parse_recruitee,
        build=lambda t: f"https://{t}.recruitee.com/api/offers/",
    ),
    Platform(
        "personio",
        r"jobs\.personio\.(?:de|com)/xml",
        platforms.parse_personio,
        build=lambda t: f"https://{t}.jobs.personio.de/xml",
    ),
    Platform(
        "oracle",
        r"/hcmRestApi/resources/.*recruitingCEJobRequisitions",
        platforms.parse_oracle,
    ),
    Platform(
        "rss",
        r"\.(?:rss|xml)(?:$|\?)|/rss/|successfactors|avature",
        platforms.parse_rss,
    ),
]

_FALLBACK = Platform("custom", r".*", platforms.parse_generic)


def detect(url: str) -> Platform:
    for p in REGISTRY:
        if p.matches(url):
            return p
    return _FALLBACK


def by_name(name: str) -> Platform | None:
    return next((p for p in REGISTRY if p.name == name), None)


def prepare(src: Source) -> Source:
    """Fill in platform and POST body from the URL shape."""
    p = detect(src.url)
    if not src.platform:
        src.platform = p.name
    if p.method == "POST":
        src.method = "POST"
        if not src.body:
            src.body = _wd_body()
    return src


def parse(payload, src: Source) -> list[Job]:
    p = by_name(src.platform) or detect(src.url)
    try:
        return [j for j in p.parse(payload, src) if j.title and j.url]
    except Exception as e:  # a malformed board must not kill the whole run
        return [] if not isinstance(e, KeyboardInterrupt) else []


def platform_names() -> list[str]:
    return [p.name for p in REGISTRY]
