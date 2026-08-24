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
        note="returns a bare top-level list; tokens are case-sensitive",
    ),
    # Lever runs two separate deployments and they do not share data. Every
    # European board answers 404 on api.lever.co and 200 on api.eu.lever.co,
    # with byte-identical JSON, which is why this shares parse_lever and only
    # differs in the host. Checked live: seb 98 postings, jacquemus 46,
    # innogames 3, all three 404 on the US host. Before this entry existed the
    # single hardcoded US builder made every EU board look dead, and
    # `validate --prune` deletes boards that look dead.
    #
    # A second registry entry rather than a token convention ("eu:seb") because
    # the registry keys on URL shape, and a different API host IS a different
    # URL shape: it needs its own url_re for `detect` and its own `build`. A
    # convention would have had to smuggle the region through the token, which
    # is the one field `discover` reads verbatim off a careers page.
    # `parse_lever` still stamps these jobs `platform="lever"`, because for
    # everything downstream of the fetch they are ordinary Lever postings.
    Platform(
        "lever_eu",
        r"api\.eu\.lever\.co/v0/postings",
        platforms.parse_lever,
        build=lambda t: f"https://api.eu.lever.co/v0/postings/{t}?mode=json",
        verified=True,
        note="Lever's EU deployment; identical shape, separate data. "
             "Tokens are case-sensitive: `Expana` is 200 and `expana` is 404",
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
        note="public guest endpoint, HTML cards, no description or salary. "
             "NOTE: LinkedIn's robots.txt is Disallow:/ for all agents, so this "
             "source is fetched in spite of it. See the README before using it.",
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
    # The only aggregator here reached through a documented, keyed API rather
    # than a public page. It is keyword-driven like NHS Jobs, so it ships as a
    # `keyword_template` source and `sources.expand_templates` turns it into
    # one search per title in `titles.include`.
    #
    # `postedByDirectEmployer=true` is in the builder on purpose. Reed carries
    # the same vacancy once per agency that has been given it, and the API
    # gives no per-result flag saying which kind of listing you are looking
    # at: it is a request filter or nothing. Filtering at the query is
    # therefore the only place duplicates can be cut before they reach the
    # pipeline. Drop the parameter if you do want agency listings.
    Platform(
        "reed",
        r"reed\.co\.uk/api/",
        platforms.parse_reed,
        build=lambda kw: (
            "https://www.reed.co.uk/api/1.0/search"
            f"?keywords={kw.replace(' ', '%20')}"
            "&postedByDirectEmployer=true"
        ),
        verified=False,
        note="needs a free API key as the HTTP Basic username, empty password. "
             "401 without one, and 200 with an empty `results` list both for a "
             "search that matched nothing and for a nonsense keyword, so "
             "liveness is the result count. Search results carry no "
             "`salaryType`, so an unlabelled figure below 2,000 is a rate and "
             "is left unconfirmed rather than read as an annual salary. "
             "UNVERIFIED against a live call: no key was obtainable here",
    ),
    Platform(
        "recruitee",
        r"\.recruitee\.com/api/offers",
        platforms.parse_recruitee,
        build=lambda t: f"https://{t}.recruitee.com/api/offers/",
    ),
    Platform(
        "breezy",
        r"\.breezy\.hr/json",
        platforms.parse_breezy,
        build=lambda t: f"https://{t}.breezy.hr/json",
        verified=True,
        note="bare top-level list at /json, like Lever; 200 + empty list for an "
             "unknown token, like Ashby; countries are ISO alpha-2 so the UK "
             "arrives as GB; the list has no description, `enrich` reads the "
             "posting page's schema.org JSON-LD for that",
    ),
    Platform(
        "jobvite",
        r"jobs\.jobvite\.com/[^/]+/jobs",
        platforms.parse_jobvite,
        build=lambda t: f"https://jobs.jobvite.com/{t}/jobs",
        verified=True,
        note="no public JSON at all: jobs.json, /search/jobs and jobs.rss all "
             "return the same career-site HTML. The list is server-rendered so "
             "no browser is needed. An unknown company 302s to a page with no "
             "rows, and the location cell says 'Hybrid Remote' for hybrid roles",
    ),
    Platform(
        "bamboohr",
        r"\.bamboohr\.com/careers/list",
        platforms.parse_bamboohr,
        build=lambda t: f"https://{t}.bamboohr.com/careers/list",
        verified=True,
        note="summary index only: no description, no date, no salary, and no "
             "country for office or hybrid roles. `enrich` reads the advert "
             "from /careers/<id>/detail. An unknown subdomain answers 200 with "
             "BambooHR's marketing homepage, so status code proves nothing",
    ),
    Platform(
        "pinpoint",
        r"\.pinpointhq\.com/(?:[a-z]{2}/)?postings\.json",
        platforms.parse_pinpoint,
        build=lambda t: f"https://{t}.pinpointhq.com/postings.json",
        verified=True,
        note="/postings.json is the documented free endpoint; /jobs.json is "
             "deprecated and /api/v1/jobs is 401 without an X-API-KEY. "
             "Structured pay behind `compensation_visible`, and no posting "
             "date anywhere in the payload",
    ),
    Platform(
        "teamtailor",
        r"\.teamtailor\.com/jobs\.rss",
        platforms.parse_teamtailor,
        # per_page is honoured; the feed's own default is the first 100 only.
        build=lambda t: f"https://{t}.teamtailor.com/jobs.rss?per_page=200",
        verified=True,
        # This entry has to sit above the generic `rss` one, whose pattern
        # `\.(?:rss|xml)(?:$|\?)` matches this URL too. `detect` returns the
        # first match, so the order is what stops every Teamtailor board being
        # read by the generic feed parser, which knows nothing about
        # remoteStatus, tt:country or tt:department.
        note="/jobs.rss, not the /jobs.json feed: only the RSS carries "
             "remoteStatus, department and the country spelled out in full. "
             "404s honestly for an unknown subdomain, unlike Ashby and Breezy, "
             "but a live board with nothing open is still a 200 with no items",
    ),
    Platform(
        "personio",
        r"jobs\.personio\.(?:de|com)/xml",
        platforms.parse_personio,
        build=lambda t: f"https://{t}.jobs.personio.de/xml",
    ),
    Platform(
        "oracle",
        r"oraclecloud\.com/hcmRestApi/.*recruitingCEJobRequisitions",
        platforms.parse_oracle,
        # The token here is "<host>|<siteNumber>", because neither is
        # derivable from the company name: Marks and Spencer are on
        # fa-eqid-saasfaprod1.fa.ocs.oraclecloud.com.
        build=lambda tok: (
            lambda host, site: (
                f"https://{host}/hcmRestApi/resources/latest/"
                f"recruitingCEJobRequisitions?onlyData=true"
                f"&expand=requisitionList.secondaryLocations"
                f"&finder=findReqs;siteNumber={site},limit=200,"
                f"sortBy=POSTING_DATES_DESC"
            )
        )(*(tok.split("|") + ["CX_1"])[:2]),
        verified=True,
        note="postings nest at items[0].requisitionList; list view carries no salary",
    ),
    Platform(
        "phenom",
        r"/search-results|phenompeople",
        platforms.parse_phenom,
        verified=True,
        note="renders in the browser but embeds the full result set as JSON in phApp.ddo",
    ),
    Platform(
        "rmk",
        r"jobs2web\.com/.*/search|/search/\?q=",
        platforms.parse_rmk,
        verified=True,
        note="SuccessFactors Recruiting Marketing; hrefs carry a tenant prefix",
    ),
    Platform(
        "avature",
        r"avature\.net/.*SearchJobs",
        platforms.parse_avature,
        verified=True,
        note="absolute hrefs to /JobDetail/; location lives in the slug",
    ),
    Platform(
        "icims",
        r"icims\.com/jobs/search",
        platforms.parse_icims,
        build=lambda t: f"https://{t}.icims.com/jobs/search?ss=1&in_iframe=1",
        verified=True,
        note="the plain search page is an empty shell; in_iframe=1 returns the real list",
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
        # Remember that these were worked out from the URL, so writing the
        # list back out does not bake in something this function re-derives on
        # every load.
        src.derived_request = src.method != "POST" or not src.body
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
