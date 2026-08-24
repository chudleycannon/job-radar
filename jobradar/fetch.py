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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


# Adzuna's documented ceiling is 50 a page. Three pages of one job title is
# 150 postings, which is well past the point the title filter has stopped
# discarding anything, and `expand_templates` already makes one of these per
# entry in `titles.include`. It also has to be counted against the free tier:
# six titles at three pages is eighteen calls a scan, and the free limits are
# 250 a day and 2,500 a month.
ADZUNA_PAGE = 50

# The page number lives in the PATH (/search/1), not in a query parameter, so
# paging means rewriting the URL rather than appending to it.
_ADZUNA_PAGE_PATH = re.compile(r"(/v1/api/jobs/[a-z]{2}/search/)\d+", re.I)


def fetch_adzuna(
    src: Source,
    app_id: str,
    app_key: str,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 3,
) -> Result:
    """Adzuna's search API. Two credentials, both in the query string.

    Adzuna offers no header authentication, so unlike Reed the credential
    cannot be kept out of the URL. What it can be kept out of is everything
    that outlives the request: the paged, credentialled URL is built onto a
    throwaway probe, and every Result returned from here carries the ORIGINAL
    source. The state file and `detect_throttling` key on `source.key`, so
    returning the probe would write an app_key into state.json and into the
    source list this repo publishes.

    With no credentials this returns a stated error rather than fetching.
    Adzuna answers an unkeyed request with 400 and an HTML error page, which
    through the ordinary path is reported as "could not be read" next to
    genuinely broken boards, and tells the reader nothing about the one thing
    they need to do.
    """
    if not (app_id and app_key):
        return Result(src, error="no Adzuna credentials: set sources.adzuna_app_id "
                                 "and sources.adzuna_app_key in your config, or the "
                                 "ADZUNA_APP_ID and ADZUNA_APP_KEY environment "
                                 "variables. Free: "
                                 "https://developer.adzuna.com/signup")

    session = requests.Session()
    # The shipped URL already asks for a page size, so drop any that is there
    # before adding ours. Sending the same parameter twice leaves Adzuna to
    # choose between two values and makes the paging arithmetic below a guess.
    # Rebuilt through urlencode rather than cut out with a regex: stripping
    # "?results_per_page=50" out of a URL where it happens to come first takes
    # the "?" with it and turns every remaining parameter into part of the path.
    _u = urlsplit(src.url)
    _q = [(k, v) for k, v in parse_qsl(_u.query, keep_blank_values=True)
          if k != "results_per_page"]
    base = urlunsplit((_u.scheme, _u.netloc, _u.path, urlencode(_q), ""))
    sep = "&" if "?" in base else "?"
    merged: dict[Any, dict] = {}
    total = 0
    first_error: Result | None = None

    for page in range(1, max_pages + 1):
        paged = _ADZUNA_PAGE_PATH.sub(rf"\g<1>{page}", base)
        probe = Source(
            company=src.company,
            url=f"{paged}{sep}app_id={app_id}&app_key={app_key}"
                f"&results_per_page={ADZUNA_PAGE}",
            platform="adzuna", sector=src.sector, country=src.country,
        )
        res = fetch_one(probe, timeout=timeout, retries=retries,
                        user_agent=user_agent, session=session)
        if not res.ok or not isinstance(res.payload, dict):
            first_error = Result(src, error=res.error or "bad payload",
                                 status=res.status, throttled=res.throttled)
            break
        rows = res.payload.get("results") or []
        total = max(total, int(res.payload.get("count") or 0))
        for r in rows:
            if isinstance(r, dict) and r.get("id") is not None:
                merged.setdefault(r["id"], r)
        # Stop on an empty page or once we hold everything Adzuna says exists,
        # never on a short one. `results_per_page` is a request, not a promise:
        # if Adzuna quietly caps a page below what we asked for, "shorter than
        # we asked" is true on every page and stopping there would throw away
        # everything past the first.
        if not rows or len(merged) >= total > 0:
            break

    if not merged and first_error is not None:
        return first_error
    return Result(src, payload={"results": list(merged.values()), "count": total})


def fetch_phenom(
    src: Source,
    terms: list[str] | None = None,
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
    # One narrow search per wanted title, the same shape as Workday, Avature
    # and RMK. Serco publish 359 roles and four unfiltered pages of fifty stop
    # at 200 of them, so an unfiltered walk was quietly deciding which 200 of
    # an employer's roles this tool would ever see. `keywords` is server-side,
    # so narrowing first is both more complete and fewer requests.
    for term in (terms or [""])[:3]:
        # Counted per term, not against `merged`: two titles overlap, and a
        # shared counter would call the second search complete on the first
        # one's rows.
        got = 0
        for page in range(max_pages):
            probe = Source(
                company=src.company, url=f"https://{host}/widgets", platform="phenom",
                sector=src.sector, country=src.country, method="POST",
                body={"lang": f"en_{country}", "deviceType": "desktop", "country": country,
                      "pageName": "search-results", "ddoKey": "refineSearch",
                      "from": page * 50, "size": 50, "jobs": True, "counts": True,
                      "all_fields": [], "keywords": term, "global": True,
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
            got += len(jobs)
            # Stop on an empty page, or once this term's whole result set is
            # held. Never on a short one: `size` is a request, not a promise,
            # and a site that caps a page below fifty makes every page short,
            # so "shorter than we asked" would throw away everything past the
            # first page of every board.
            if not jobs or got >= int(er.get("totalHits") or 0) > 0:
                break

    if not merged:
        # Fall back to the ten embedded in the page rather than returning none.
        return fetch_one(src, timeout=timeout, retries=retries, user_agent=user_agent)
    return Result(src, payload={"refineSearch": {"data": {"jobs": list(merged.values())},
                                                 "totalHits": total}})


def _with_query(url: str, **params: str) -> str:
    """Replace query parameters, rather than appending a second copy.

    Appending is what a naive `url + "&startrow=25"` does, and the shipped
    Avature and RMK URLs already carry `q=` and `jobRecordsPerPage=`. Two
    values for one parameter leaves the server to pick, and the paging
    arithmetic here becomes a guess. Same reasoning as `fetch_adzuna`, which
    learned it the expensive way.
    """
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if k not in params]
    q += [(k, v) for k, v in params.items()]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


# The link Avature renders for "Next >>". Following it beats computing the
# next offset ourselves, because the page size is the tenant's choice and not
# ours: Tesco answers ten rows however many `jobRecordsPerPage` asks for, and
# advertises `jobRecordsPerPage=10&jobOffset=10` in this very link. Stepping
# by the size we requested would have skipped forty rows out of every fifty.
_AV_NEXT = re.compile(
    r'class="[^"]*paginationNextLink[^"]*"\s+href="([^"]+)"', re.I)
# `[^"?]` and not `[^"]`: every Avature card also carries Twitter and
# Facebook share links whose query string contains the job URL. Those are
# not rows, and counting them as "fresh" would keep the pager walking a
# board that had already run out.
_AV_JOB = re.compile(r'href="(https?://[^"?]*?/JobDetail/[^"?]+)"', re.I)


def fetch_avature(
    src: Source,
    terms: list[str],
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 4,
) -> Result:
    """Avature's search page, paged, and narrowed at the server where possible.

    One request returns whatever the tenant configured, which for Tesco is ten
    rows out of "999+". A source that quietly returns ten of three thousand is
    worse than one that fails: `validate` calls it live, the scan reports it
    healthy, and nobody finds out. So this pages.

    Paging alone is not enough either. Ten at a time through a 999+ board is a
    hundred requests per scan for an employer whose roles are almost all
    discarded by the title filter. `semanticSearch=` is a real server-side
    keyword filter (47 results for "engineering manager" against 999+ with no
    filter), so this does what `fetch_workday` does: one narrow search per
    wanted title, paged shallowly, instead of one deep unfiltered walk.

    The page cap is hard and applies per term. There is no total anywhere in
    the markup to stop on, so the stop condition is "no next link, or no rows
    we have not already seen", and a cap is the only thing standing between a
    broken stop condition and a loop that never ends.
    """
    session = requests.Session()
    pages: list[str] = []
    seen: set[str] = set()
    first_error: Result | None = None

    # An empty term means the unfiltered board, which is right for the small
    # tenants: Metro Bank publish six roles and a keyword search would only
    # hide four of them.
    for term in (terms or [""])[:3]:
        url = _with_query(src.url, semanticSearch=term) if term else src.url
        for _ in range(max_pages):
            probe = Source(company=src.company, url=url, platform="avature",
                           sector=src.sector, country=src.country)
            res = fetch_one(probe, timeout=timeout, retries=retries,
                            user_agent=user_agent, session=session)
            if not res.ok or not isinstance(res.payload, str):
                # Carry the failure on the ORIGINAL source: `detect_throttling`
                # and the state file key on `source.key`, and a URL with an
                # offset in it is a different key on every page.
                first_error = first_error or Result(
                    src, error=res.error or "bad payload", status=res.status,
                    throttled=res.throttled)
                break
            fresh = {u for u in _AV_JOB.findall(res.payload)} - seen
            if not fresh:
                break
            seen |= fresh
            pages.append(res.payload)
            nxt = _AV_NEXT.search(res.payload)
            if not nxt:
                break
            url = nxt.group(1).replace("&amp;", "&")

    if not pages:
        return first_error or Result(src, error="no pages returned")
    # `parse_avature` already drops a repeated /JobDetail/ link, so joining the
    # pages is safe and keeps the parser a pure function of one HTML string.
    return Result(src, payload="".join(pages))


# SuccessFactors RMK pages on `startrow` and serves twenty-five rows, and no
# part of the markup states a total: "Showing {0} to {1}" is a client-side
# template with the numbers filled in by JavaScript we never run. Verified
# against Transport for London, where startrow=0 returns 24 and startrow=10
# returns 14, so the offset is honoured and TfL really does have 24 rather
# than being truncated.
RMK_PAGE = 25


def fetch_rmk(
    src: Source,
    terms: list[str],
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    max_pages: int = 4,
) -> Result:
    """SuccessFactors RMK, paged on startrow and narrowed with `q`.

    Without this the adapter reads exactly the first twenty-five rows of every
    tenant and reports that as the board. SAP's own careers site sits on this
    platform and publishes thousands.

    `q` is server-side (TfL: 24 rows unfiltered, 10 for "engineer"), so the
    same shape as Workday and Avature applies: search per wanted title rather
    than walk the whole board.
    """
    session = requests.Session()
    pages: list[str] = []
    seen: set[str] = set()
    first_error: Result | None = None

    for term in (terms or [""])[:3]:
        for page in range(max_pages):
            url = _with_query(src.url, q=term, startrow=str(page * RMK_PAGE))
            probe = Source(company=src.company, url=url, platform="rmk",
                           sector=src.sector, country=src.country)
            res = fetch_one(probe, timeout=timeout, retries=retries,
                            user_agent=user_agent, session=session)
            if not res.ok or not isinstance(res.payload, str):
                first_error = first_error or Result(
                    src, error=res.error or "bad payload", status=res.status,
                    throttled=res.throttled)
                break
            fresh = set(re.findall(r'href="([^"]*?/job/[^"?]+)"',
                                   res.payload)) - seen
            # Stop on nothing new, never on a short page. A tenant that serves
            # fewer than RMK_PAGE rows per page would otherwise be truncated
            # to its first page on every scan, which is the exact fault this
            # function exists to fix.
            if not fresh:
                break
            seen |= fresh
            pages.append(res.payload)

    if not pages:
        return first_error or Result(src, error="no pages returned")
    return Result(src, payload="".join(pages))


# What one page looks like on the platforms that cap one. A source whose whole
# result is exactly one of these numbers is the signature of a paging bug: the
# board answered, the parser worked, and everything past row N was silently
# dropped. Tesco returning exactly 10 of "999+" looked healthy for as long as
# nobody counted.
PAGE_SIZES = {
    "avature": 10, "rmk": RMK_PAGE, "phenom": 50, "workday": 20,
    "nhs": 10, "reed": REED_PAGE, "adzuna": ADZUNA_PAGE,
    # The Teamtailor builder asks for per_page=200; the feed's own default is
    # the first 100, so a board sitting on either number is worth a look.
    "teamtailor": 200,
}


def pinned_to_one_page(counts: dict[str, int], sources: Iterable[Source]) -> list[str]:
    """Sources whose entire result is exactly one page of their platform.

    Not proof of a fault: a board can genuinely have twenty roles on a
    platform that pages in twenty-fives. It is the only cheap signal there is,
    though, and the alternative is what happened here, where a source returned
    ten of three thousand for months and read as healthy the whole time.
    """
    out = []
    for src in sources:
        size = PAGE_SIZES.get(src.platform)
        if size and counts.get(src.key, 0) == size:
            out.append(src.company)
    return sorted(set(out))


def fetch_all(
    sources: Iterable[Source],
    *,
    concurrency: int = 4,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    search_terms: list[str] | None = None,
    # Keyed by credential name, not by platform: Adzuna needs two. Passed down rather
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
    if src.platform == "adzuna":
        return fetch_adzuna(src, (keys or {}).get("adzuna_app_id", ""),
                            (keys or {}).get("adzuna_app_key", ""),
                            timeout=timeout, retries=retries, user_agent=ua)
    if src.platform == "workday":
        return fetch_workday(src, terms, timeout=timeout, retries=retries,
                             user_agent=ua)
    if src.platform == "nhs":
        return fetch_nhs(src, timeout=timeout, retries=retries, user_agent=ua)
    if src.platform == "phenom":
        return fetch_phenom(src, terms, timeout=timeout, retries=retries,
                            user_agent=ua)
    if src.platform == "avature":
        return fetch_avature(src, terms, timeout=timeout, retries=retries,
                             user_agent=ua)
    if src.platform == "rmk":
        return fetch_rmk(src, terms, timeout=timeout, retries=retries,
                         user_agent=ua)
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
