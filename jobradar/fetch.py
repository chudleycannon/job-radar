"""Polite HTTP with throttle detection.

The reason this file exists rather than a bare `requests.get` loop: several of
these APIs fail in ways that look exactly like success.

  * Ashby and SmartRecruiters return HTTP 200 with an empty array for a board
    token that does not exist, and for one that is being rate-limited. Status
    code tells you nothing; job count does.
  * Greenhouse returns 403 if you attach a body to a GET, which is easy to do
    accidentally when one code path handles both GET and POST platforms.
  * Workday returns 406 rather than 404 for a tenant that does not exist,
    because of wildcard DNS. A non-404 is not evidence a tenant is real.

So a source that used to return jobs and now returns none is reported as a
suspected throttle rather than "no jobs", because the difference matters and
the API will not tell you.
"""

from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import requests

from .models import Source


@dataclass
class Result:
    source: Source
    payload: Any = None
    error: str | None = None
    status: int | None = None
    elapsed: float = 0.0
    throttled: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 30.0))
            return
        except ValueError:
            pass
    time.sleep(min(2 ** attempt, 20) + random.uniform(0, 0.75))


def fetch_one(
    src: Source,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    session: requests.Session | None = None,
) -> Result:
    s = session or requests.Session()
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    t0 = time.time()
    last = "unknown error"
    status = None

    for attempt in range(retries + 1):
        try:
            if src.method.upper() == "POST":
                r = s.post(src.url, json=src.body or {}, headers=headers, timeout=timeout)
            else:
                # Never attach a body to a GET. Greenhouse 403s on it.
                r = s.get(src.url, headers=headers, timeout=timeout)
            status = r.status_code

            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = f"HTTP {r.status_code}"
                if attempt < retries:
                    _sleep_backoff(attempt, r.headers.get("Retry-After"))
                    continue
                return Result(src, error=last, status=status,
                              elapsed=time.time() - t0, throttled=r.status_code == 429)

            if r.status_code >= 400:
                return Result(src, error=f"HTTP {r.status_code}", status=status,
                              elapsed=time.time() - t0)

            ctype = (r.headers.get("Content-Type") or "").lower()
            if "json" in ctype or r.text.lstrip()[:1] in "[{":
                return Result(src, payload=r.json(), status=status, elapsed=time.time() - t0)
            return Result(src, payload=r.text, status=status, elapsed=time.time() - t0)

        except requests.RequestException as e:
            last = type(e).__name__
            if attempt < retries:
                _sleep_backoff(attempt, None)
                continue

    return Result(src, error=last, status=status, elapsed=time.time() - t0)


def fetch_workday(
    src: Source,
    terms: list[str],
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 3,
) -> Result:
    """Workday needs its own path, for two reasons.

    Its page size is hard-capped at 20 (asking for 100 returns a 400), and its
    boards are enormous: Barclays reports 1,055 open roles. Paging through that
    for every enterprise tenant would be dozens of requests each, per scan, for
    results almost all of which get discarded by the title filter anyway.

    Workday is also the only platform here with server-side search, so the
    filtering happens at their end instead: one query per wanted title,
    shallowly paged. That turns a thousand postings into the handful that
    matter, in two or three requests rather than fifty.
    """
    session = requests.Session()
    merged: dict[str, dict] = {}
    total = 0
    errors = []

    for term in (terms or [""])[:3]:
        for page in range(max_pages):
            probe = Source(
                company=src.company, url=src.url, platform="workday",
                sector=src.sector, country=src.country, domain=src.domain,
                method="POST",
                body={"appliedFacets": {}, "limit": 20,
                      "offset": page * 20, "searchText": term},
            )
            res = fetch_one(probe, timeout=timeout, retries=retries,
                            user_agent=user_agent, session=session)
            if not res.ok or not isinstance(res.payload, dict):
                errors.append(res.error or "bad payload")
                break
            posts = res.payload.get("jobPostings") or []
            total = max(total, int(res.payload.get("total") or 0))
            for p in posts:
                key = p.get("externalPath") or p.get("title")
                if key:
                    merged.setdefault(key, p)
            if len(posts) < 20:
                break

    if not merged and errors:
        return Result(src, error=errors[0])
    return Result(src, payload={"jobPostings": list(merged.values()), "total": total})


def fetch_nhs(
    src: Source,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 5,
) -> Result:
    """NHS Jobs returns ten results a page and has no page-size parameter.

    The search is already narrowed by keyword in the source URL, so a handful
    of pages is plenty; walking all 10,000 results would be both slow and
    pointless when the title filter discards almost all of them.
    """
    session = requests.Session()
    parts: list[str] = []
    sep = "&" if "?" in src.url else "?"

    for page in range(1, max_pages + 1):
        probe = Source(company=src.company, url=f"{src.url}{sep}page={page}",
                       platform="nhs", sector=src.sector, country=src.country)
        res = fetch_one(probe, timeout=timeout, retries=retries,
                        user_agent=user_agent, session=session)
        if not res.ok or not isinstance(res.payload, str):
            break
        if "search-result" not in res.payload:
            break
        parts.append(res.payload)
        # A short page means the results ran out.
        if res.payload.count('data-test="search-result"') < 10:
            break

    if not parts:
        return Result(src, error="no pages returned")
    return Result(src, payload="".join(parts))


# Reed hard-limits a page to 100 and documents it. Three pages per keyword is
# 300 postings for one job title, which is far past the point the title filter
# has stopped discarding things, and `expand_templates` already makes one of
# these per title in `titles.include`.
REED_PAGE = 100


def fetch_reed(
    src: Source,
    api_key: str,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 3,
) -> Result:
    """Reed's jobseeker API. Keyed, and paged with resultsToSkip.

    The key goes in as the HTTP Basic username with an empty password, which
    is Reed's own documented scheme. It is set on the session rather than
    built into the URL, so it never lands in a log line, a saved source list
    or an error message.

    With no key this returns a stated error rather than fetching. Reed answers
    401 to an unkeyed request, and a 401 arriving through the ordinary path
    would be reported next to genuinely broken boards as "could not be read",
    which tells the reader nothing about the one thing they need to do.
    """
    if not api_key:
        return Result(src, error="no Reed API key: set sources.reed_api_key in "
                                 "your config, or the REED_API_KEY environment "
                                 "variable. Free key: "
                                 "https://www.reed.co.uk/developers/jobseeker")

    session = requests.Session()
    # (key, "") is Basic auth with an empty password, which is what Reed asks
    # for. requests base64-encodes it into the Authorization header.
    session.auth = (api_key, "")
    sep = "&" if "?" in src.url else "?"

    merged: dict[Any, dict] = {}
    total = 0
    first_error: Result | None = None

    for page in range(max_pages):
        probe = Source(
            company=src.company,
            url=f"{src.url}{sep}resultsToTake={REED_PAGE}"
                f"&resultsToSkip={page * REED_PAGE}",
            platform="reed", sector=src.sector, country=src.country,
        )
        res = fetch_one(probe, timeout=timeout, retries=retries,
                        user_agent=user_agent, session=session)
        if not res.ok or not isinstance(res.payload, dict):
            # Carry the failure on the ORIGINAL source, not the paged probe:
            # `detect_throttling` and the state file key on `source.key`, and
            # a URL with resultsToSkip in it is a different key every page.
            first_error = Result(src, error=res.error or "bad payload",
                                 status=res.status, throttled=res.throttled)
            break
        rows = res.payload.get("results") or []
        total = max(total, int(res.payload.get("totalResults") or 0))
        for r in rows:
            if isinstance(r, dict) and r.get("jobId") is not None:
                merged.setdefault(r["jobId"], r)
        if len(rows) < REED_PAGE:
            break

    if not merged and first_error is not None:
        return first_error
    return Result(src, payload={"results": list(merged.values()),
                                "totalResults": total})


def fetch_phenom(
    src: Source,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 4,
) -> Result:
    """Phenom's search page embeds only the first ten results, but the site is
    driven by a `/widgets` POST endpoint that returns fifty at a time and
    reports the true total. Serco publish 359 roles; ten of them is not a
    useful view of an employer.
    """
    from urllib.parse import urlparse

    session = requests.Session()
    host = urlparse(src.url).netloc
    # Prefer the country in the URL, then the one the source is tagged with.
    # "gb" is only the last resort: a UK default baked in below the config
    # layer is invisible to anyone who is not in the UK.
    country = (src.country or "gb").lower()
    if country == "uk":
        country = "gb"
    m = re.search(r"//[^/]+/([a-z]{2})/", src.url)
    if m:
        country = m.group(1)

    merged: dict[str, dict] = {}
    total = 0
    for page in range(max_pages):
        probe = Source(
            company=src.company, url=f"https://{host}/widgets", platform="phenom",
            sector=src.sector, country=src.country, method="POST",
            body={"lang": f"en_{country}", "deviceType": "desktop", "country": country,
                  "pageName": "search-results", "ddoKey": "refineSearch",
                  "from": page * 50, "size": 50, "jobs": True, "counts": True,
                  "all_fields": [], "keywords": "", "global": True,
                  "siteType": "external", "clearAll": False},
        )
        res = fetch_one(probe, timeout=timeout, retries=retries,
                        user_agent=user_agent, session=session)
        if not res.ok or not isinstance(res.payload, dict):
            break
        er = res.payload.get("refineSearch") or {}
        jobs = (er.get("data") or {}).get("jobs") or []
        total = max(total, int(er.get("totalHits") or 0))
        for j in jobs:
            key = j.get("jobSeqNo") or j.get("jobId") or j.get("applyUrl")
            if key:
                merged.setdefault(key, j)
        if len(jobs) < 50:
            break

    if not merged:
        # Fall back to the ten embedded in the page rather than returning none.
        return fetch_one(src, timeout=timeout, retries=retries, user_agent=user_agent)
    return Result(src, payload={"refineSearch": {"data": {"jobs": list(merged.values())},
                                                 "totalHits": total}})


def fetch_all(
    sources: Iterable[Source],
    *,
    concurrency: int = 4,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    search_terms: list[str] | None = None,
    # Keyed by platform name. Only Reed needs one so far. Passed down rather
    # than read from the environment inside the fetcher, so a caller can run
    # two configs in one process without them sharing a key.
    api_keys: dict[str, str] | None = None,
    on_result: Callable[[Result], None] | None = None,
) -> list[Result]:
    sources = list(sources)
    out: list[Result] = []
    # One session per worker; requests.Session is not thread-safe to share.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {}
        for i, src in enumerate(sources):
            # Stagger the opening burst so we do not hit 200 hosts in the
            # same 50ms and look like something worth blocking.
            delay = (i % max(1, concurrency)) * 0.05
            futs[ex.submit(_delayed_fetch, src, delay, timeout, retries,
                           user_agent, search_terms or [],
                           api_keys or {})] = src
        for f in as_completed(futs):
            res = f.result()
            out.append(res)
            if on_result:
                on_result(res)
    return out


def _delayed_fetch(src, delay, timeout, retries, ua, terms, keys=None) -> Result:
    if delay:
        time.sleep(delay)
    if src.platform == "reed":
        return fetch_reed(src, (keys or {}).get("reed", ""), timeout=timeout,
                          retries=retries, user_agent=ua)
    if src.platform == "workday":
        return fetch_workday(src, terms, timeout=timeout, retries=retries,
                             user_agent=ua)
    if src.platform == "nhs":
        return fetch_nhs(src, timeout=timeout, retries=retries, user_agent=ua)
    if src.platform == "phenom":
        return fetch_phenom(src, timeout=timeout, retries=retries, user_agent=ua)
    return fetch_one(src, timeout=timeout, retries=retries, user_agent=ua,
                     session=requests.Session())


def detect_throttling(
    results: list[Result],
    counts: dict[str, int],
    history: dict[str, int],
) -> list[str]:
    """Sources that previously returned jobs and now return none.

    Silent throttling is the failure mode that makes this whole tool lie: an
    empty array reads as "nothing matched" when it actually means "you were
    blocked". Anything here should be treated as unknown, not as zero.
    """
    suspects = []
    for res in results:
        key = res.source.key
        was = history.get(key, 0)
        now = counts.get(key, 0)
        if was >= 3 and now == 0:
            suspects.append(res.source.company)
        elif res.throttled:
            suspects.append(res.source.company)
    return sorted(set(suspects))
