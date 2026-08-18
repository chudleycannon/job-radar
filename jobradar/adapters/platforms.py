"""One parser per ATS. Each takes a raw payload and yields normalised `Job`s.

Adding a platform: write a `parse_<name>(payload, src)` generator, add it to
REGISTRY in __init__.py with a URL pattern, and add a builder in
`jobradar.discover` if the token can be found from a careers page.

Notes on the awkward ones are inline. They are all things that cost a
debugging session to find out.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

from ..models import Job, Salary, Source
from ..salary import from_ashby, from_greenhouse, parse_text

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(v: Any) -> str:
    if not v:
        return ""
    s = html.unescape(str(v))
    s = _TAGS.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _iso(v: Any) -> str | None:
    """Normalise the six date formats these APIs between them use."""
    if not v:
        return None
    if isinstance(v, (int, float)):
        # Milliseconds vs seconds: anything past ~2001 in ms is > 1e12.
        ts = float(v) / 1000.0 if float(v) > 1e11 else float(v)
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.replace("Z", "+0000") if fmt.endswith("%z") else s,
                                     fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else None


def _remote(*bits: Any) -> bool | None:
    blob = " ".join(str(b) for b in bits if b).lower()
    if not blob:
        return None
    if re.search(r"\bremote\b|\bwork from home\b|\bwfh\b|\bdistributed\b", blob):
        return not re.search(r"\bnon.?remote\b|\bno remote\b|\bhybrid only\b", blob)
    return None


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------
def parse_greenhouse(payload: Any, src: Source) -> Iterator[Job]:
    """`pay_input_ranges` appears ONLY with `?pay_transparency=true`.
    `content=true` is a separate parameter and does not trigger it.
    Also: never send a body with the GET, Greenhouse answers 403 if you do.
    """
    for j in (payload or {}).get("jobs", []) or []:
        loc = j.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else _text(loc)
        offices = ", ".join(
            o.get("name", "") for o in (j.get("offices") or []) if isinstance(o, dict)
        )
        desc = _text(j.get("content"))
        sal = from_greenhouse(j.get("pay_input_ranges"))
        if not sal.confirmed:
            sal = parse_text(desc[:1500])
        yield Job(
            company=j.get("company_name") or src.company,
            title=_text(j.get("title")),
            url=j.get("absolute_url") or "",
            platform="greenhouse",
            location=_text(location or offices),
            remote=_remote(location, offices, j.get("title")),
            department=", ".join(
                d.get("name", "") for d in (j.get("departments") or []) if isinstance(d, dict)
            ) or None,
            posted_at=_iso(j.get("first_published") or j.get("updated_at")),
            description=desc,
            salary=sal,
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Ashby
# --------------------------------------------------------------------------
def parse_ashby(payload: Any, src: Source) -> Iterator[Job]:
    """Returns HTTP 200 with an empty `jobs` array for a token that does not
    exist AND for one being rate-limited. Validate on job count, never status.
    Compensation needs `?includeCompensation=true`.
    """
    for j in (payload or {}).get("jobs", []) or []:
        if j.get("isListed") is False:
            continue
        secondary = ", ".join(
            s.get("location", "") if isinstance(s, dict) else str(s)
            for s in (j.get("secondaryLocations") or [])
        )
        loc = _text(j.get("location"))
        yield Job(
            company=src.company,
            title=_text(j.get("title")),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            platform="ashby",
            location=", ".join(x for x in (loc, secondary) if x),
            remote=j.get("isRemote") if isinstance(j.get("isRemote"), bool)
            else _remote(loc, secondary),
            department=_text(j.get("department") or j.get("team")) or None,
            posted_at=_iso(j.get("publishedAt")),
            description=_text(j.get("descriptionPlain") or j.get("descriptionHtml")),
            salary=from_ashby(j.get("compensation")),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------
def parse_lever(payload: Any, src: Source) -> Iterator[Job]:
    """Lever returns a bare top-level list, not an object with a `jobs` key."""
    items = payload if isinstance(payload, list) else (payload or {}).get("data", [])
    for j in items or []:
        cats = j.get("categories") or {}
        loc = _text(cats.get("location"))
        desc = _text(j.get("descriptionPlain") or j.get("description"))
        extra = " ".join(
            _text(l.get("content")) for l in (j.get("lists") or []) if isinstance(l, dict)
        )
        yield Job(
            company=src.company,
            title=_text(j.get("text")),
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            platform="lever",
            location=loc,
            remote=_remote(loc, cats.get("commitment"), j.get("workplaceType")),
            department=_text(cats.get("team") or cats.get("department")) or None,
            posted_at=_iso(j.get("createdAt")),
            description=(desc + " " + extra).strip(),
            salary=parse_text(f"{desc[:1500]} {extra[:500]}"),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Workable
# --------------------------------------------------------------------------
def parse_workable(payload: Any, src: Source) -> Iterator[Job]:
    for j in (payload or {}).get("jobs", []) or []:
        loc = j.get("location") or {}
        if isinstance(loc, dict):
            location = ", ".join(
                str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)
            )
            remote = loc.get("workplace") == "remote" or bool(loc.get("telecommuting"))
        else:
            location, remote = _text(loc), None
        desc = _text(j.get("description"))
        yield Job(
            company=src.company,
            title=_text(j.get("title")),
            url=j.get("url") or j.get("application_url") or j.get("shortlink") or "",
            platform="workable",
            location=location,
            remote=remote if isinstance(remote, bool) else _remote(location),
            department=_text(j.get("department")) or None,
            posted_at=_iso(j.get("published_on") or j.get("created_at")),
            description=desc,
            salary=parse_text(desc[:1500]),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# SmartRecruiters
# --------------------------------------------------------------------------
def parse_smartrecruiters(payload: Any, src: Source) -> Iterator[Job]:
    """Like Ashby: 200 + empty `content` for a company id that does not exist."""
    for j in (payload or {}).get("content", []) or []:
        loc = j.get("location") or {}
        location = ", ".join(
            str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)
        ) if isinstance(loc, dict) else _text(loc)
        cid = j.get("id") or ""
        url = (j.get("ref") or "").replace("api.smartrecruiters.com/v1/companies",
                                           "jobs.smartrecruiters.com") or \
            f"https://jobs.smartrecruiters.com/{src.company}/{cid}"
        yield Job(
            company=_text((j.get("company") or {}).get("name")) or src.company,
            title=_text(j.get("name")),
            url=url,
            platform="smartrecruiters",
            location=location,
            remote=bool(loc.get("remote")) if isinstance(loc, dict) else _remote(location),
            department=_text((j.get("department") or {}).get("label")) or None,
            posted_at=_iso(j.get("releasedDate") or j.get("createdOn")),
            description=_text(j.get("jobAd")),
            salary=Salary(),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Workday (CXS)
# --------------------------------------------------------------------------
def parse_workday(payload: Any, src: Source) -> Iterator[Job]:
    """POST, not GET. Body: {"appliedFacets":{},"limit":20,"offset":0,"searchText":""}

    A tenant that does not exist answers 406, not 404, because of wildcard DNS
    on *.myworkdayjobs.com. So a non-404 response proves nothing about whether
    the tenant is real; only `jobPostings` having entries does.
    """
    base = re.sub(r"/wday/cxs/.*$", "", src.url)
    m = re.search(r"/wday/cxs/([^/]+)/([^/]+)/jobs", src.url)
    site = m.group(2) if m else ""
    for j in (payload or {}).get("jobPostings", []) or []:
        path = j.get("externalPath") or ""
        url = urljoin(f"{base}/en-US/{site}/", path.lstrip("/")) if path else base
        bullets = " ".join(str(b) for b in (j.get("bulletFields") or []))
        loc = _text(j.get("locationsText"))
        yield Job(
            company=src.company,
            title=_text(j.get("title")),
            url=url,
            platform="workday",
            location=loc,
            remote=_remote(loc, j.get("title")),
            department=None,
            posted_at=_iso(j.get("startDate") or j.get("postedOn")),
            description=bullets,
            salary=parse_text(bullets),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# LinkedIn (public guest endpoint)
# --------------------------------------------------------------------------
_LI_CARD = re.compile(r"<li>(.*?)</li>", re.S)
_LI_TITLE = re.compile(r'base-search-card__title"[^>]*>(.*?)<', re.S)
_LI_CO = re.compile(r'base-search-card__subtitle"[^>]*>\s*(?:<a[^>]*>)?(.*?)<', re.S)
_LI_LOC = re.compile(r'job-search-card__location"[^>]*>(.*?)<', re.S)
_LI_URL = re.compile(r'href="(https://[^"]*?/jobs/view/[^"?]+)')
_LI_DATE = re.compile(r'datetime="([\d-]+)"')


def parse_linkedin(payload: Any, src: Source) -> Iterator[Job]:
    """The guest `seeMoreJobPostings/search` endpoint returns server-rendered
    HTML cards to a plain GET, no login and no JS. It gives title, company,
    location and a canonical /jobs/view/ URL, but no description or salary,
    so these are lead-generation rather than screenable postings.
    """
    text = payload if isinstance(payload, str) else ""
    for card in _LI_CARD.findall(text):
        t = _LI_TITLE.search(card)
        c = _LI_CO.search(card)
        u = _LI_URL.search(card)
        if not (t and u):
            continue
        loc = _LI_LOC.search(card)
        d = _LI_DATE.search(card)
        yield Job(
            company=_text(c.group(1)) if c else src.company,
            title=_text(t.group(1)),
            url=u.group(1),
            platform="linkedin",
            location=_text(loc.group(1)) if loc else "",
            remote=_remote(loc.group(1) if loc else "", t.group(1)),
            posted_at=_iso(d.group(1)) if d else None,
            description="",
            salary=Salary(),
            source_id=src.key,
            flags=["listing-only: no description available from this source"],
        )


# --------------------------------------------------------------------------
# Recruitee
# --------------------------------------------------------------------------
def parse_recruitee(payload: Any, src: Source) -> Iterator[Job]:
    for j in (payload or {}).get("offers", []) or []:
        loc = ", ".join(
            str(j.get(k)) for k in ("city", "country") if j.get(k)
        ) or _text(j.get("location"))
        desc = _text(j.get("description")) + " " + _text(j.get("requirements"))
        yield Job(
            company=src.company,
            title=_text(j.get("title")),
            url=j.get("careers_url") or j.get("careers_apply_url") or "",
            platform="recruitee",
            location=loc,
            remote=_remote(loc, j.get("remote")),
            department=_text(j.get("department")) or None,
            posted_at=_iso(j.get("published_at") or j.get("created_at")),
            description=desc.strip(),
            salary=parse_text(desc[:1500]),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Personio (XML)
# --------------------------------------------------------------------------
def parse_personio(payload: Any, src: Source) -> Iterator[Job]:
    text = payload if isinstance(payload, str) else ""
    for block in re.findall(r"<position>(.*?)</position>", text, re.S):
        def g(tag):
            m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
            return _text(m.group(1)) if m else ""

        pid = g("id")
        title = g("name")
        if not title:
            continue
        desc = _text(block)
        yield Job(
            company=src.company,
            title=title,
            url=g("url") or f"https://{src.company}.jobs.personio.de/job/{pid}",
            platform="personio",
            location=g("office") or g("location"),
            remote=_remote(g("office"), title),
            department=g("department") or None,
            posted_at=_iso(g("createdAt")),
            description=desc,
            salary=parse_text(desc[:1500]),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Oracle Recruiting Cloud
# --------------------------------------------------------------------------
_ORC_SITE = re.compile(r"siteNumber=(CX_\d+)", re.I)


def parse_oracle(payload: Any, src: Source) -> Iterator[Job]:
    """Oracle Recruiting Cloud, the system behind a lot of large employers.

    The response nests one level deeper than most: `items[0].requisitionList`
    holds the postings, and `items[0].TotalJobsCount` is the real total rather
    than the page length.

    Two things to know. The host is not derivable from the company name
    (Marks and Spencer sit on `fa-eqid-saasfaprod1.fa.ocs.oraclecloud.com`),
    so these come from reading the careers page. And the list view carries no
    salary at all, only a short description, so roles from here are almost
    always "unconfirmed salary" and that is the platform, not a parse failure.
    """
    items = (payload or {}).get("items") or []
    reqs = []
    for it in items:
        reqs.extend(it.get("requisitionList") or [])

    host = urlparse(src.url).netloc
    m = _ORC_SITE.search(src.url)
    site = m.group(1) if m else "CX_1"

    for j in reqs:
        rid = j.get("Id")
        if not rid:
            continue
        loc = _text(j.get("PrimaryLocation"))
        secondary = ", ".join(
            _text(s.get("Name") or s.get("PrimaryLocation"))
            for s in (j.get("secondaryLocations") or []) if isinstance(s, dict)
        )
        desc = _text(j.get("ShortDescriptionStr") or j.get("ExternalResponsibilitiesStr"))
        yield Job(
            company=src.company,
            title=_text(j.get("Title")),
            url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
            platform="oracle",
            location=", ".join(x for x in (loc, secondary) if x),
            remote=_remote(loc, j.get("WorkplaceTypeCode"), j.get("Title")),
            department=_text(j.get("JobFamily") or j.get("JobFunction")) or None,
            posted_at=_iso(j.get("PostedDate")),
            description=desc,
            salary=parse_text(desc[:1500]),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# RSS / Atom (SuccessFactors, Avature, many public-sector boards)
# --------------------------------------------------------------------------
def parse_rss(payload: Any, src: Source) -> Iterator[Job]:
    text = payload if isinstance(payload, str) else ""
    for item in re.findall(r"<(?:item|entry)>(.*?)</(?:item|entry)>", text, re.S):
        def g(tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", item, re.S)
            return _text(m.group(1)) if m else ""

        title = g("title")
        if not title:
            continue
        link = g("link")
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', item)
            link = m.group(1) if m else ""
        desc = g("description") or g("summary") or g("content")
        yield Job(
            company=src.company,
            title=title,
            url=link,
            platform="rss",
            location=g("location") or "",
            remote=_remote(desc[:300], title),
            posted_at=_iso(g("pubDate") or g("published") or g("updated")),
            description=desc,
            salary=parse_text(desc[:1500]),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Generic JSON fallback
# --------------------------------------------------------------------------
_TITLE_KEYS = ("title", "name", "jobTitle", "positionTitle", "text")
_URL_KEYS = ("url", "absolute_url", "jobUrl", "applyUrl", "hostedUrl", "link", "applyLink")


def parse_generic(payload: Any, src: Source) -> Iterator[Job]:
    """Last resort for bespoke boards (Amazon, Netflix, Atlassian and friends).

    Walks the payload for the first list of dicts that look like postings.
    Deliberately conservative: if it cannot find a title and a URL it yields
    nothing rather than inventing a job.
    """
    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list):
            if node and isinstance(node[0], dict):
                keys = set(node[0])
                if keys & set(_TITLE_KEYS):
                    yield node
            for x in node[:50]:
                yield from walk(x, depth + 1)
        elif isinstance(node, dict):
            for v in node.values():
                yield from walk(v, depth + 1)

    for candidate in walk(payload):
        for j in candidate:
            if not isinstance(j, dict):
                continue
            title = next((_text(j[k]) for k in _TITLE_KEYS if j.get(k)), "")
            url = next((str(j[k]) for k in _URL_KEYS if j.get(k)), "")
            if not title or not url or not url.startswith("http"):
                continue
            loc = j.get("location") or j.get("locations") or j.get("city") or ""
            if isinstance(loc, dict):
                loc = ", ".join(str(v) for v in loc.values() if isinstance(v, str))
            elif isinstance(loc, list):
                loc = ", ".join(str(x) for x in loc if isinstance(x, str))
            desc = _text(j.get("description") or j.get("content") or "")
            yield Job(
                company=src.company,
                title=title,
                url=url,
                platform=src.platform or "custom",
                location=_text(loc),
                remote=_remote(loc, title),
                posted_at=_iso(j.get("postedDate") or j.get("posted_at")
                               or j.get("updated_at") or j.get("publishedAt")),
                description=desc,
                salary=parse_text(desc[:1500]),
                source_id=src.key,
            )
        return  # only the first plausible list


# --------------------------------------------------------------------------
# NHS Jobs
# --------------------------------------------------------------------------
_NHS_PANEL = re.compile(r'<li class="nhsuk-list-panel search-result.*?(?=<li class="nhsuk-list-panel|</ul>)', re.S)
_NHS_TITLE = re.compile(r'data-test="search-result-job-title"[^>]*>\s*(.*?)\s*</a>', re.S)
_NHS_HREF = re.compile(r'href="(/candidate/jobadvert/[^"?]+)')
_NHS_EMPLOYER = re.compile(
    r'data-test="search-result-location">.*?<h3[^>]*>\s*(.*?)\s*<div class="location-font-size">\s*(.*?)\s*</div>',
    re.S)
_NHS_FIELD = re.compile(
    r'data-test="search-result-{}"[^>]*>.*?<strong[^>]*>\s*(.*?)\s*</strong>', re.S)


def _nhs_field(block: str, name: str) -> str:
    m = re.compile(
        rf'data-test="search-result-{name}"[^>]*>.*?<strong[^>]*>\s*(.*?)\s*</strong>',
        re.S).search(block)
    return _text(m.group(1)) if m else ""


def parse_nhs(payload: Any, src: Source) -> Iterator[Job]:
    """NHS Jobs search results.

    NHS Jobs is the reason a whole sector was invisible: trusts do not use any
    of the commercial applicant tracking systems, so no amount of adding
    employer names reached them. There is a JSON API at /api/v1/search_json but
    it sits behind an auth token, and the .rss path returns HTML rather than a
    feed, so the search page is the route.

    It is worth the parsing. Postings carry Agenda for Change bands, so unlike
    most of the market these roles nearly always state a salary, which means
    the pay filter actually bites here rather than falling through to
    "unconfirmed".
    """
    text = payload if isinstance(payload, str) else ""
    base = "https://www.jobs.nhs.uk"

    for block in _NHS_PANEL.findall(text):
        t = _NHS_TITLE.search(block)
        h = _NHS_HREF.search(block)
        if not (t and h):
            continue

        emp = _NHS_EMPLOYER.search(block)
        employer = _text(emp.group(1)) if emp else "NHS"
        location = _text(emp.group(2)) if emp else ""

        pay = _nhs_field(block, "salary")
        posted = _nhs_field(block, "publicationDate")
        closing = _nhs_field(block, "closingDate")
        jobtype = _nhs_field(block, "jobType")
        pattern = _nhs_field(block, "workingPattern")

        desc = " ".join(x for x in (jobtype, pattern, pay) if x)
        job = Job(
            company=employer,
            title=_text(t.group(1)),
            url=base + h.group(1),
            platform="nhs",
            location=location,
            remote=_remote(location, _text(t.group(1)), pattern),
            department=None,
            posted_at=_iso(posted),
            description=desc,
            salary=parse_text(pay),
            source_id=src.key,
        )
        if closing:
            job.flags.append(f"closes {closing}")
        # The search page carries no duties text, so a dealbreaker scan here
        # would be scanning three metadata fields and calling it clean.
        job.flags.append("not screened: search listing only, open the advert")
        yield job
