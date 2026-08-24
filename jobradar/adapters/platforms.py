"""One parser per ATS. Each takes a raw payload and yields normalised `Job`s.

Adding a platform: write a `parse_<name>(payload, src)` generator, add it to
REGISTRY in __init__.py with a URL pattern, and add a builder in
`jobradar.discover` if the token can be found from a careers page.

Notes on the awkward ones are inline. They are all things that cost a
debugging session to find out.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import unquote, urljoin, urlparse

from ..models import Job, Salary, Source
from ..salary import (from_adzuna, from_ashby, from_greenhouse, from_pinpoint,
                       from_reed, parse_text)

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(v: Any) -> str:
    """Flatten whatever an API returned into a readable string.

    Some platforms wrap a field as {"rendered": "Data Engineer"}. Passing that
    to str() put a Python dict repr on screen as a job title.
    """
    if not v:
        return ""
    if isinstance(v, dict):
        for k in ("rendered", "name", "label", "text", "value"):
            if isinstance(v.get(k), str):
                v = v[k]
                break
        else:
            v = " ".join(str(x) for x in v.values() if isinstance(x, str))
    elif isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v if isinstance(x, str))
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
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y",
                # NHS Jobs writes the month in full ("18 August 2026"). Without
                # %B every NHS role had no date, so the recency points never
                # fired and 28 roles clumped onto three scores.
                "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
                # Taleo lets each career section pick its own date format, and
                # they really do differ: TTEC and D.R. Horton send
                # "Aug 24, 2026", Transport for London sends "13-Aug-26". A
                # Taleo posting is only ever found by shape, so a format this
                # cannot read is a posting with no date and no recency points.
                "%d-%b-%y", "%d-%b-%Y",
                # RFC 822, which is what every RSS <pubDate> is:
                # "Wed, 19 Aug 2026 16:47:00 +0100". Without it no feed-shaped
                # source had a date at all, so the recency points never fired
                # for any of them and every Teamtailor role scored as undated.
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
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
        # `ref` is the API URL. Swapping the host in it produced
        # jobs.smartrecruiters.com/<co>/postings/<id>, which 404s: the public
        # path has no /postings/ segment. Every link the tool offered for this
        # platform was dead, which is worse than not listing the role, because
        # a dead link is only discovered after someone decides to apply.
        ident = _text((j.get("company") or {}).get("identifier")) or src.company
        url = (f"https://jobs.smartrecruiters.com/{ident}/{cid}" if cid
               else (j.get("ref") or ""))
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
        if not loc:
            # Some tenants leave locationsText empty and put the city in the
            # path instead. Without this the role has no location at all, and
            # an unknown country passes a country filter it should fail.
            m2 = re.search(r"/job/([^/]+)/", path or "")
            if m2:
                loc = _text(m2.group(1).replace("-", " "))
            if not loc:
                loc = " ".join(str(b) for b in (j.get("bulletFields") or [])[:2])
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
# Breezy HR
# --------------------------------------------------------------------------
# Breezy writes countries as ISO 3166 alpha-2, so the United Kingdom arrives
# as "GB". Everything downstream of the adapters speaks screen.py's
# vocabulary, in which that country is "UK". Handing "GB" straight through
# filed every British posting under a code no country filter, dashboard facet
# or `--country` flag ever asks for, which loses a whole board from a UK-only
# search without reporting anything.
_BZ_COUNTRY = {"GB": "UK"}


def _breezy_place(loc: Any) -> str:
    """One Breezy location, written the way the rest of the tool reads them.

    Deliberately rebuilt from the parts rather than taken from Breezy's own
    `location.name`, which renders as "Lambeth, GB". The bare alpha-2 code
    reads badly on screen, and the country's full name is the stronger signal
    for the location filter, which looks for "United Kingdom" before it falls
    back to two-letter forms.
    """
    if not isinstance(loc, dict):
        return _text(loc)
    state = loc.get("state") or {}
    country = loc.get("country") or {}
    parts: list[str] = []
    seen: set[str] = set()
    for p in (_text(loc.get("city")),
              _text(state.get("id") or state.get("name")),
              _text(country.get("name"))):
        if p and p.lower() not in seen:
            seen.add(p.lower())
            parts.append(p)
    return ", ".join(parts) or _text(loc.get("name"))


def parse_breezy(payload: Any, src: Source) -> Iterator[Job]:
    """Breezy HR. The board is `https://<company>.breezy.hr/json`.

    Like Lever it answers with a bare top-level list, not an object with a
    `jobs` key. Like Ashby it answers 200 with an empty list for a token that
    does not exist, so liveness is a job count and never a status code.

    The list carries no description whatsoever, only metadata, which is why
    `enrich` grew a Breezy fetcher: the posting page embeds the full advert as
    schema.org JSON-LD. What the list does carry is a ready-formatted salary
    string ("£35,000 – £40,000 / year"), so a fair share of these state pay.
    """
    items = payload if isinstance(payload, list) else (payload or {}).get("positions") or []
    for j in items or []:
        if not isinstance(j, dict):
            continue
        primary = j.get("location") if isinstance(j.get("location"), dict) else {}
        places = [p for p in (j.get("locations") or []) if isinstance(p, dict)] \
            or ([primary] if primary else [])

        names: list[str] = []
        seen: set[str] = set()
        for p in places:
            txt = _breezy_place(p)
            # Breezy repeats the same place in `locations` when an employer
            # ticks two identical remote entries, which produced
            # "Remote / Remote" on a real Dozuki posting.
            if txt and txt.lower() not in seen:
                seen.add(txt.lower())
                names.append(txt)
        # Joined with " / " and not ", ": screen.py splits a multi-location
        # string on the slash but treats a comma as binding a place to its
        # qualifier, so a comma here fuses "Philadelphia, PA" and "Salt Lake
        # City, UT" into one string that resolves to neither.
        location = " / ".join(names)

        # Only set the country when the posting names exactly one. Where it
        # names several, leaving it unset lets screen.py mark it "multiple"
        # from the location string rather than picking a winner here.
        codes = {str((p.get("country") or {}).get("id") or "").upper() for p in places}
        codes.discard("")
        country = None
        if len(codes) == 1:
            code = codes.pop()
            country = _BZ_COUNTRY.get(code, code)

        remote_details = primary.get("remote_details") or {}
        detail = _text(remote_details.get("value")).lower()
        label = _text(remote_details.get("label"))
        if detail == "hybrid":
            # `is_remote` is true for hybrid postings as well as remote ones.
            # Taking it at face value marked a Bournemouth role that wants you
            # in the office part of the week as remote, which is the single
            # thing a remote filter must never do.
            remote: bool | None = False
        elif detail == "remote" or primary.get("is_remote") is True:
            remote = True
        else:
            remote = _remote(location, j.get("name"))

        pay = _text(j.get("salary"))
        sal = parse_text(pay)
        if sal.confirmed:
            sal.raw = pay[:120]

        url = _text(j.get("url"))
        if not url and j.get("friendly_id"):
            host = urlparse(src.url).netloc or \
                f"{_text((j.get('company') or {}).get('friendly_id'))}.breezy.hr"
            url = f"https://{host}/p/{j['friendly_id']}"

        # There is no advert text here, so the only thing worth screening is
        # the metadata. The remote label earns its place: "Hybrid (Some
        # remote, some in person)" is what makes screen.py file the role as
        # hybrid rather than reading the word "remote" off the location.
        meta = [x for x in (_text((j.get("type") or {}).get("name")),
                            _text(j.get("department")), label, pay) if x]

        yield Job(
            # The board publishes the employer's own name, and `discover`
            # checks a board's identity against it. Falling back to src.company
            # would make every board agree with whatever we already believed.
            company=_text((j.get("company") or {}).get("name")) or src.company,
            title=_text(j.get("name")),
            url=url,
            platform="breezy",
            location=location,
            remote=remote,
            department=_text(j.get("department")) or None,
            posted_at=_iso(j.get("published_date")),
            description=". ".join(meta),
            salary=sal,
            country=country,
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Personio (XML)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Jobvite
# --------------------------------------------------------------------------
# There is no public JSON here. `/<company>/jobs.json`, `/search/jobs` and
# `/jobs.rss` all return the same career-site HTML, and `api/v1/jobs` redirects
# away. The board is server-rendered though, so no browser is needed: the list
# is a plain table of links.
#
# The markup is employer-customisable and really does differ. NinjaOne ship
# `<td class="jv-job-list-name">` and LHH ship `<div class="jv-job-list-name">`
# for the same thing, so the class names are the anchor and the element name
# is not. The location cell is closed on `</td>` or `</div>` specifically,
# because NinjaOne put a `<span>,</span>` inside it and a lazier close would
# cut the location off after the first word.
_JV_ROW = re.compile(
    r'class="[^"]*jv-job-list-name[^"]*"[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>'
    r'\s*</(?:td|div)>\s*'
    r'<(?:td|div)[^>]*class="[^"]*jv-job-list-location[^"]*"[^>]*>(.*?)</(?:td|div)>',
    re.S | re.I,
)
_JV_HEAD = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S | re.I)

# The location cell carries the working arrangement in front of the place, and
# "Hybrid Remote" contains the word "remote". Reading it with the usual
# keyword check returns True, which would have marked all 31 hybrid roles on
# NinjaOne's board as remote. This is the same failure Breezy's `is_remote`
# caused on an office-based Bournemouth job, arriving by a different route.
_JV_HYBRID = re.compile(r"^\s*hybrid\s+remote\b\s*,?\s*", re.I)
_JV_REMOTE = re.compile(r"^\s*remote\b\s*,?\s*", re.I)


def parse_jobvite(payload: Any, src: Source) -> Iterator[Job]:
    """Jobvite. The board is `https://jobs.jobvite.com/<company>/jobs`.

    A company that does not exist answers 302 and lands somewhere with no job
    rows in it, so following redirects turns "no such board" into a perfectly
    ordinary 200. Liveness is the job count, as everywhere else here.

    The list carries no advert text, no date and no salary, so `enrich` reads
    the posting page's schema.org JSON-LD, which Jobvite publishes on every
    job for Google Jobs.
    """
    text = payload if isinstance(payload, str) else ""

    # Department comes from the nearest `<h3>` above the row, which is how
    # these boards group their tables. Checked against both live boards: it
    # yields real department names on each and never picks up the sidebar
    # headings, which sit above the first table rather than between tables.
    heads = [(m.start(), _text(m.group(1))) for m in _JV_HEAD.finditer(text)]

    for m in _JV_ROW.finditer(text):
        title = _text(m.group(2))
        if not title:
            continue

        place = _text(m.group(3))
        if _JV_HYBRID.match(place):
            remote: bool | None = False
            place = _JV_HYBRID.sub("", place)
        elif _JV_REMOTE.match(place):
            remote = True
            place = _JV_REMOTE.sub("", place)
        else:
            remote = _remote(place, title)
        # A role whose only stated location was the word "Remote" has to keep
        # saying so. An empty location is read as "no location given", which
        # is a different answer and a different filter branch.
        location = place or ("Remote" if remote else "")

        dept = next((t for pos, t in reversed(heads) if pos < m.start()), "")

        yield Job(
            # Jobvite's list markup never names the employer. LHH's own <h1>
            # is an image whose alt text is "LHH logo", so there is nothing
            # here to check identity against and `discover` will report these
            # boards as agreeing with whatever we already believed.
            company=src.company,
            title=title,
            url=urljoin(src.url, _text(m.group(1))),
            platform="jobvite",
            location=location,
            remote=remote,
            department=dept or None,
            # The JSON-LD on the posting page has `datePosted`, but `enrich`
            # only ever writes the description and the pay.
            posted_at=None,
            description="",
            salary=Salary(),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# JazzHR
# --------------------------------------------------------------------------
# 865 distinct employer hosts on applytojob.com in one Common Crawl index,
# more than any other platform this tool could not read. The board is
# `https://<company>.applytojob.com/apply`, server-rendered, so no browser is
# needed.
#
# Two things worth knowing before touching this.
#
# The RSS feed at `/apply/jobs.rss` answers 410 Gone, so the HTML list is the
# only route. And the whole board arrives on one page: there is no page
# parameter, no offset and no total anywhere in the markup, which is the one
# case where reading a single response is not a truncation bug.
#
# Unusually for this codebase, the page states the employer's own name, in a
# schema.org Organization block. Almost every other adapter fills `company`
# from the Source it was handed, which makes `discover`'s identity check
# circular. Here it can actually be checked.
_JZ_ROW = re.compile(
    r"<li class=[\"']list-group-item[\"']>(.*?)</ul>", re.S | re.I)
_JZ_LINK = re.compile(
    r"<h3[^>]*list-group-item-heading[^>]*>\s*<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.S | re.I)
_JZ_PLACE = re.compile(r"fa-map-marker[^>]*></i>\s*([^<]{1,80})", re.I)
_JZ_DEPT = re.compile(r"fa-sitemap[^>]*></i>\s*([^<]{1,60})", re.I)
_JZ_ORG = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _jazzhr_org(text: str) -> str:
    """The employer's own name, from the Organization block on the page."""
    for m in _JZ_ORG.finditer(text or ""):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for node in (d if isinstance(d, list) else [d]):
            if isinstance(node, dict) and node.get("@type") == "Organization":
                return _text(node.get("name") or "")
    return ""


def parse_jazzhr(payload: Any, src: Source) -> Iterator[Job]:
    """JazzHR, from the server-rendered board at `/apply`."""
    text = payload if isinstance(payload, str) else ""
    org = _jazzhr_org(text)

    for m in _JZ_ROW.finditer(text):
        blk = m.group(1)
        link = _JZ_LINK.search(blk)
        if not link:
            continue
        title = _text(link.group(2))
        if not title:
            continue
        place = _text((_JZ_PLACE.search(blk) or [None, ""])[1]
                      if _JZ_PLACE.search(blk) else "")
        dept = _text((_JZ_DEPT.search(blk).group(1)
                      if _JZ_DEPT.search(blk) else ""))
        remote = _remote(place, title)
        yield Job(
            # The board names itself, so this is the one platform here where
            # the company field is evidence rather than an echo of our label.
            company=org or src.company,
            title=title,
            url=urljoin(src.url, _text(link.group(1))),
            platform="jazzhr",
            location=place or ("Remote" if remote else ""),
            remote=remote,
            department=dept or None,
            # No date, advert text or pay in the list. `enrich` reads the
            # posting page, which carries a JobPosting JSON-LD block.
            posted_at=None,
            description="",
            salary=Salary(),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Oracle Taleo
# --------------------------------------------------------------------------
# 255 distinct employer hosts on taleo.net in one Common Crawl index, the
# largest readable gap left after JazzHR. The board is
# `https://<tenant>.taleo.net/careersection/<section>/jobsearch.ftl`, and the
# token is composite (`tenant|section`) because a Taleo tenant runs several
# career sections and none of them is the default: Hilton's is
# `us_hotel_ext`, Transport for London's is `external`, TTEC's is `2`.
#
# Four things cost a session each here.
#
# The page is a JavaScript shell. A plain GET of `jobsearch.ftl` returns no
# job rows at all, so `fetch_taleo` reads the JSON endpoint the page itself
# calls. See the comment there for why the `tz` header is not optional.
#
# The columns are configured per career section and there is no header row in
# the JSON, so nothing may be read by position. Live proof: BAE Systems ship
# ONE column (title only, no location anywhere), Transport for London ship two
# (title, date), TTEC and D.R. Horton ship three (title, locations, date).
# Reading `column[1]` as the location gives BAE nothing and TfL a date. Taleo
# does hand out pointers, `linkedColumn` and `locationsColumns`, and those are
# what this trusts; the date is found by trying to parse the leftovers.
#
# The location is a JSON array serialised INTO the cell, so the raw value is
# the eight characters `["Bath"]` plus the place. It has to be decoded, or
# every location on every Taleo board arrives with brackets and quotes in it.
#
# And Taleo writes a location as a hierarchy joined by hyphens, biggest first:
# `PH-National Capital-Quezon City, Metro Manila`. screen.py's country matcher
# reads comma-separated locations, smallest first, and its US-state rules
# require the comma (`,\s*nebraska`). Handed Taleo's own spelling it resolved
# almost nothing. `_taleo_place` reverses and re-commas, which is the entire
# fix: "Omaha, Nebraska" resolves to US, "Quezon City, Metro Manila, National
# Capital, PH" resolves to PH on the city.
_TL_CELL_SPLIT = 2   # country / region / everything-else, see _taleo_place

# Two-letter codes that may be expanded into a country name, and the list is
# short on purpose. It is exactly the codes screen.py's `_COUNTRY_MARKERS`
# already knows, MINUS every code that is also a US state abbreviation.
# Excluded for that reason and no other: CA (California, not Canada), DE
# (Delaware, not Germany), IN (Indiana, not India), IL (Illinois, not Israel),
# ID (Idaho, not Indonesia), AR (Arkansas, not Argentina). D.R. Horton's board
# is the live proof this matters: it publishes `IN-Indianapolis`,
# `AL-Spanish Fort` and `KY-Louisville` next to `Nebraska-Omaha`, all American,
# and expanding those codes would file them in India, Albania and the Cayman
# Islands. Codes screen.py has never heard of are left alone too, because
# expanding one gains nothing and only invents a place name.
_TL_COUNTRY = {
    "GB": "United Kingdom", "US": "United States", "IE": "Ireland",
    "FR": "France", "ES": "Spain", "NL": "Netherlands", "AU": "Australia",
    "NZ": "New Zealand", "AE": "United Arab Emirates", "SG": "Singapore",
    "HK": "Hong Kong", "JP": "Japan", "CN": "China", "PL": "Poland",
    "PT": "Portugal", "SE": "Sweden", "CH": "Switzerland", "BR": "Brazil",
    "MX": "Mexico", "ZA": "South Africa", "TH": "Thailand", "MY": "Malaysia",
    "PH": "Philippines", "IT": "Italy", "BE": "Belgium", "AT": "Austria",
    "DK": "Denmark", "NO": "Norway", "FI": "Finland", "CZ": "Czechia",
    "RO": "Romania", "TR": "Turkey", "VN": "Vietnam", "KR": "South Korea",
}


def _taleo_place(cell: Any) -> list[str]:
    """One Taleo location cell, rewritten so screen.py can read it.

    Three deliberate rules, each of which cost real roles when it was not
    there.

    It splits at most twice, because Taleo's hierarchy is country, region,
    place and the place itself may be hyphenated. Splitting on every hyphen
    turns `GB-England-Stoke-on-Trent` into five fragments; splitting twice
    keeps the town whole.

    It reverses, so the string reads smallest-first and comma-separated, which
    is the shape screen.py's country matcher was built for. Its US-state rules
    require the comma (`,\\s*nebraska`), so Taleo's own `Nebraska-Omaha`
    matched nothing at all and every D.R. Horton role reached the country
    filter unresolved. Reversed it is "Omaha, Nebraska", which resolves.

    It expands a leading two-letter country code only from `_TL_COUNTRY`,
    which deliberately excludes every code that is also a US state. A bare
    `PH` resolves to nothing (screen.py looks for the word "philippines"), so
    leaving it alone loses TTEC's whole Philippine operation from the country
    facet; expanding `IN` would move D.R. Horton's Indianapolis jobs to India.
    Both of those are on live boards, which is why the answer is a list rather
    than a rule.
    """
    raw = cell if isinstance(cell, str) else _text(cell)
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        # Not every tenant serialises the cell as an array. A bare string is
        # still a location and must not be thrown away.
        entries = [raw]
    if not isinstance(entries, list):
        entries = [entries]

    out: list[str] = []
    for e in entries:
        s = _text(e)
        if not s:
            continue
        parts = [p.strip() for p in s.split("-", _TL_CELL_SPLIT) if p.strip()]
        # Only the leading segment, which is the one Taleo puts the country
        # in. A two-letter code further down is a region, and "TX" is not
        # Texas-the-country.
        if len(parts) > 1 and parts[0] in _TL_COUNTRY:
            parts[0] = _TL_COUNTRY[parts[0]]
        out.append(", ".join(reversed(parts)))
    return out


def _taleo_date(cells: list[str], used: set[int]) -> str | None:
    """The posting date, found by shape rather than by position.

    There is no header row in the JSON and the columns differ per career
    section, so the only honest way to find the date is to try to parse the
    cells nothing else claimed. Live formats seen: "Aug 24, 2026" (TTEC,
    D.R. Horton) and "13-Aug-26" (Transport for London), which is why `_iso`
    grew the second one.
    """
    for i, c in enumerate(cells):
        if i in used or not isinstance(c, str):
            continue
        got = _iso(c)
        if got:
            return got
    return None


# Taleo publishes no working-arrangement field of any kind: not in the row,
# not in the facets. Remote is stated in the job title when it is stated at
# all ("Data Engineer (Remote)" on TTEC). That means the keyword check is the
# only signal, and it walks straight into the Jobvite trap, where "Hybrid
# Remote" contains the word "remote" and reads as true. A title or location
# that says hybrid is answered False, which is what it is: a hybrid role is
# not open to someone who cannot reach the office.
_TL_HYBRID = re.compile(r"\bhybrid\b", re.I)


def parse_taleo(payload: Any, src: Source) -> Iterator[Job]:
    """Oracle Taleo, from the JSON search endpoint `fetch_taleo` collects.

    The payload is what `fetch_taleo` assembles: every page's rows merged
    under `requisitionList`, plus `employerName` read from the RSS channel
    title, which is the only place on the whole platform where Taleo states
    who the employer is. See `fetch_taleo` for why that is worth a request.

    A career section that does not exist answers **HTTP 200** with
    `careerSectionUnAvailable: true` and every field null, so liveness here is
    the parsed job count and never the status code.
    """
    rows = (payload or {}).get("requisitionList") if isinstance(payload, dict) else None
    employer = _text((payload or {}).get("employerName")) if isinstance(payload, dict) else ""

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cells = [c if isinstance(c, str) else _text(c)
                 for c in (row.get("column") or [])]
        if not cells:
            continue

        ti = row.get("linkedColumn")
        ti = ti if isinstance(ti, int) and 0 <= ti < len(cells) else 0
        title = _text(cells[ti])
        if not title:
            continue

        loc_idx = [i for i in (row.get("locationsColumns") or [])
                   if isinstance(i, int) and 0 <= i < len(cells)]
        places: list[str] = []
        for i in loc_idx:
            places.extend(_taleo_place(cells[i]))
        # A pipe, because screen.py splits genuinely distinct locations on
        # `[;|/]` and deliberately does not split on a comma: a comma binds a
        # place to the qualifier that identifies its country.
        place = " | ".join(dict.fromkeys(p for p in places if p))

        posted = _taleo_date(cells, {ti, *loc_idx})

        blob = f"{title} {place}"
        remote = False if _TL_HYBRID.search(blob) else _remote(place, title)

        contest = _text(row.get("contestNo") or row.get("jobId"))
        if not contest:
            continue

        yield Job(
            # `employerName` comes from the feed, not from the label we were
            # handed, so identity here is evidence. It is also the ONLY place
            # it is available: both <title> tags on an unbranded Taleo board
            # read "Job Search", which is the shape that collapsed 252 Jobvite
            # employers into one row.
            company=employer or src.company,
            title=title,
            # jobdetail.ftl lives beside jobsearch.ftl in the same career
            # section, and `job=` takes the contest number rather than the
            # internal requisition id.
            url=urljoin(src.url, f"jobdetail.ftl?lang=en&job={contest}"),
            platform="taleo",
            location=place or ("Remote" if remote else ""),
            remote=remote,
            # The row carries no department. Taleo has a JOB_FIELD facet, but
            # it is a summary of the whole board rather than a value per
            # posting, so there is nothing honest to put here.
            department=None,
            posted_at=posted,
            description="",
            # No pay in any of the seven live career sections checked. The
            # advert sometimes states one, and `enrich` re-parses it from
            # there, where the period is written down: a bare figure with no
            # period is the Reed trap, where 650 a day read as 650 a year.
            salary=Salary(),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# BambooHR
# --------------------------------------------------------------------------
# `/careers/list` is a summary index, not a board. It carries no description,
# no apply URL, no date and no salary, so `enrich` grew a BambooHR fetcher
# that reads `/careers/<id>/detail` for the advert. Without it every one of
# these roles would arrive as a bare title that no dealbreaker and no salary
# floor can be run against.
#
# `locationType` is the field that says how the job is worked, and the field
# actually called `isRemote` is a decoy: it is null on all 155 postings across
# the five live boards checked. The enum was pinned by comparing the JSON
# against the labels BambooHR's own `/jobs/embed2.php` widget renders for the
# same posting ids:
#   "0" -> plain office location   ("Farnborough")        = in-office
#   "1" -> "Remote"                                        = remote
#   "2" -> "(Hybrid)" suffix       ("Farnborough (Hybrid)")= hybrid
# Type 1 is also the only one with no company location at all, which matches
# BambooHR's documented behaviour: picking Remote requires no location.
_BB_OFFICE, _BB_REMOTE, _BB_HYBRID = "0", "1", "2"


def parse_bamboohr(payload: Any, src: Source) -> Iterator[Job]:
    """BambooHR. The board is `https://<company>.bamboohr.com/careers/list`.

    A subdomain that does not exist does NOT 404 here and does not return an
    empty list either. It answers **200 with BambooHR's own marketing
    homepage** as HTML, so both the status code and the content type prove
    nothing and liveness has to be the job count. That is why this tolerates a
    payload that is not a dict at all rather than assuming JSON.

    The list gives no country for office and hybrid roles, only a city and a
    region. See the README: those roles reach the country filter unresolved.
    """
    rows = (payload or {}).get("result") if isinstance(payload, dict) else None
    host = urlparse(src.url).netloc
    for j in rows or []:
        if not isinstance(j, dict):
            continue

        loc_type = str(j.get("locationType") or "")
        office = j.get("location") if isinstance(j.get("location"), dict) else {}
        ats = j.get("atsLocation") if isinstance(j.get("atsLocation"), dict) else {}

        # Remote postings carry no company address, so their only location is
        # the free-text one, which is also the only place a country ever
        # appears in this payload.
        parts = ([_text(ats.get("city")),
                  _text(ats.get("state") or ats.get("province")),
                  _text(ats.get("country"))]
                 if loc_type == _BB_REMOTE or not _text(office.get("city"))
                 else [_text(office.get("city")), _text(office.get("state"))])
        seen: set[str] = set()
        keep: list[str] = []
        for part in parts:
            # "OMAN, OMAN" is a real value on a live board.
            if part and part.lower() not in seen:
                seen.add(part.lower())
                keep.append(part)
        location = ", ".join(keep)

        if loc_type == _BB_REMOTE:
            remote: bool | None = True
        elif loc_type in (_BB_OFFICE, _BB_HYBRID):
            # Never read this off the words. Breezy's own flag was true for
            # hybrid roles and marked an office-based Bournemouth job as
            # remote, which is the one thing a remote filter must never do.
            remote = False
        else:
            remote = _remote(location, j.get("jobOpeningName"))

        jid = _text(j.get("id"))
        if not jid:
            continue

        yield Job(
            # The payload never names the employer, so identity has to come
            # from the source entry and `discover` will report these boards as
            # unchecked rather than falsely ok.
            company=src.company,
            title=_text(j.get("jobOpeningName")),
            # Matches the `jobOpeningShareUrl` the detail endpoint returns,
            # and `enrich` turns it back into the detail URL by appending
            # /detail, so the two have to stay in this shape.
            url=f"https://{host}/careers/{jid}" if host else "",
            platform="bamboohr",
            location=location or ("Remote" if remote else ""),
            remote=remote,
            department=_text(j.get("departmentLabel")) or None,
            # Not in the list payload. `/careers/<id>/detail` has `datePosted`,
            # but `enrich` only ever writes the description and the pay.
            posted_at=None,
            # No advert text here at all. Everything worth screening on is
            # metadata until `enrich` has run.
            description=". ".join(
                x for x in (_text(j.get("employmentStatusLabel")),
                            _text(j.get("departmentLabel"))) if x),
            salary=Salary(),
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Pinpoint
# --------------------------------------------------------------------------
# The documented public endpoint is `/postings.json`. `/jobs.json` answers too
# but is the deprecated one, and `/api/v1/jobs` is 401 without an X-API-KEY, so
# the free surface is the first of the three and only the first.
#
# What it does not carry is a posting date. There is none in the payload and
# none in the documented schema; the RSS feed at `/jobs.rss` has a <pubDate>
# but carries nothing else useful, so this trades the date for the structured
# pay, location and workplace fields rather than fetching both. Pinpoint roles
# therefore score flat on recency, which is a stated limitation and not a
# parse failure.
_PP_SECTIONS = (
    ("key_responsibilities_header", "key_responsibilities"),
    ("skills_knowledge_expertise_header", "skills_knowledge_expertise"),
    ("benefits_header", "benefits"),
)


def _pinpoint_place(loc: Any) -> str:
    """One Pinpoint location.

    Built from `city` and `province`, deliberately not from `name`, which is
    whatever the employer typed: real values include "Minneapolis, MN" and
    "Anna, IL". A bare two-letter code is the worst possible thing to put in a
    location string, because twenty US state codes are also ISO country codes.
    `province` is spelled out ("California", "New York"), which resolves
    unambiguously.

    Pinpoint publishes no country anywhere in this payload, so the country is
    left for screen.py to infer from the city and state. Inventing one here
    would be guessing.
    """
    if not isinstance(loc, dict):
        return _text(loc)
    city = _text(loc.get("city"))
    province = _text(loc.get("province"))
    parts = [p for p in (city, province) if p]
    # Cities that are their own region give "London, London", which reads as
    # a bug to anyone looking at the dashboard.
    if len(parts) == 2 and parts[0].lower() == parts[1].lower():
        parts.pop()
    return ", ".join(parts) or _text(loc.get("name"))


def parse_pinpoint(payload: Any, src: Source) -> Iterator[Job]:
    """Pinpoint. The board is `https://<company>.pinpointhq.com/postings.json`.

    Like Teamtailor it 404s honestly for a subdomain that does not exist, and
    like every other board here a live one with nothing open answers 200 with
    an empty list, so liveness is the job count.

    `workplace_type` is the field that separates remote from hybrid. Its
    values are `remote`, `hybrid` and `onsite`.

    The advert arrives in four separate fields rather than one, so a parser
    that reads only `description` throws away the responsibilities and the
    must-haves, which is precisely the half the dealbreakers are written
    against.
    """
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    for j in rows or []:
        if not isinstance(j, dict):
            continue

        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        location = _pinpoint_place(loc)

        mode = _text(j.get("workplace_type")).lower()
        if mode == "remote":
            remote: bool | None = True
        elif mode in ("hybrid", "onsite"):
            # Never read this off the advert text. Breezy's own remote flag
            # was true for hybrid roles and marked an office-based Bournemouth
            # job as remote, which is the one thing a remote filter must never
            # do. Pinpoint states it outright, so use the statement.
            remote = False
        else:
            remote = _remote(location, j.get("title"))

        parts = [_text(j.get("description"))]
        for head, body in _PP_SECTIONS:
            txt = _text(j.get(body))
            if txt:
                parts.append(f"{_text(j.get(head))}\n{txt}".strip())
        desc = "\n\n".join(x for x in parts if x)

        sal = from_pinpoint(j)
        if not sal.confirmed:
            # An employer with `compensation_visible` off has not published a
            # figure, but plenty state one in the advert body anyway.
            sal = parse_text(desc[:1500])

        job = j.get("job") if isinstance(j.get("job"), dict) else {}
        dept = job.get("department") if isinstance(job.get("department"), dict) else {}

        yield Job(
            # Pinpoint never names the employer in this payload, not even the
            # hiring organisation, so identity has to come from the source
            # entry. `discover` will report these boards as `unchecked`
            # against a domain rather than falsely `ok`.
            company=src.company,
            title=_text(j.get("title")),
            url=_text(j.get("url")),
            platform="pinpoint",
            location=location or ("Remote" if remote else ""),
            remote=remote,
            department=_text(dept.get("name")) or None,
            # No date exists in this payload. See the note above the parser.
            posted_at=None,
            description=desc,
            salary=sal,
            source_id=src.key,
        )


# --------------------------------------------------------------------------
# Teamtailor
# --------------------------------------------------------------------------
# Two public feeds exist on every career site and they are not equivalent.
# `/jobs.json` is a JSON Feed carrying a schema.org JobPosting per item, but it
# states the country as ISO alpha-2 ("GB") and says nothing at all about
# remote working or department. `/jobs.rss` carries the same descriptions plus
# `<remoteStatus>`, `<tt:department>` and, decisively, `<tt:country>` spelled
# out in full ("United Kingdom"). Reading the RSS is what keeps this adapter
# clear of the country-code trap Breezy walked into, rather than aliasing
# around it afterwards.
#
# The feed defaults to the first 100 jobs and honours `per_page` (verified:
# per_page=2 on a 33-job board returns 2), so the builder asks for 200. A
# board with more than that would silently lose the tail, which is why the
# number is stated here and not left implicit.
_TT_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TT_LOCATION = re.compile(r"<tt:location>(.*?)</tt:location>", re.S)


def _tt_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                  block, re.S)
    return _text(m.group(1)) if m else ""


def _tt_place(block: str) -> str:
    """One `<tt:location>`, written the way screen.py reads locations.

    City then country in words, never the alpha-2 code. Two-letter codes are
    actively dangerous here: twenty US state codes are also ISO country codes,
    so "Berlin, DE" resolves to Delaware and "Toronto, CA" to California. The
    full name is unambiguous and is what the country matcher checks first.
    """
    city = _tt_tag(block, "tt:city")
    country = _tt_tag(block, "tt:country")
    name = _tt_tag(block, "tt:name")
    parts = [p for p in (city or name, country) if p]
    # An employer who named the office after the country produces "Latin
    # America, Latin America", which reads as a parse failure to anyone
    # looking at it.
    if len(parts) == 2 and parts[0].lower() == parts[1].lower():
        parts.pop()
    return ", ".join(parts)


def parse_teamtailor(payload: Any, src: Source) -> Iterator[Job]:
    """Teamtailor. The board is `https://<company>.teamtailor.com/jobs.rss`.

    Unlike Ashby, Breezy and SmartRecruiters this one does answer 404 for a
    subdomain that does not exist, so a status code is meaningful. It is still
    not sufficient: a live board with nothing open answers 200 with no items
    (mathem and normative both do), so liveness stays a job count.

    `<remoteStatus>` is the field that separates remote from hybrid. Its
    values are `fully`, `hybrid`, `temporary` and `none`.
    """
    text = payload if isinstance(payload, str) else ""

    # The channel names the employer. `discover` checks a board's identity
    # against its own claim about itself, and falling back to src.company
    # would make every board agree with whatever we already believed.
    head = text.split("<item>", 1)[0]
    board_company = _tt_tag(head, "title")

    for item in _TT_ITEM.findall(text):
        title = _tt_tag(item, "title")
        if not title:
            continue

        names: list[str] = []
        seen: set[str] = set()
        for loc in _TT_LOCATION.findall(item):
            txt = _tt_place(loc)
            if txt and txt.lower() not in seen:
                seen.add(txt.lower())
                names.append(txt)
        # " / " and not ", ": screen.py splits a multi-location string on the
        # slash but reads a comma as binding a place to its qualifier, so a
        # comma fuses "Cambridge, United States" and "Stockholm, Sweden" into
        # one string that resolves to neither.
        location = " / ".join(names)

        status = _tt_tag(item, "remoteStatus").lower()
        if status == "fully":
            remote: bool | None = True
        elif status in ("hybrid", "temporary"):
            # Hybrid is an office job with some days at home, and "temporary"
            # is an office job that is remote for now. Breezy's `is_remote`
            # was true for hybrid roles and marked an office-based Bournemouth
            # job as remote, which is the one thing a remote filter must never
            # do. 14 of 16 roles on Teamtailor's own board are hybrid, so this
            # is the common case here, not an edge case.
            remote = False
        else:
            # `none` is a default as much as a statement, so fall back to
            # reading the words rather than asserting the role is on-site.
            remote = _remote(location, title)

        desc = _tt_tag(item, "description")

        yield Job(
            company=board_company or src.company,
            title=title,
            url=_tt_tag(item, "link"),
            platform="teamtailor",
            location=location or ("Remote" if remote else ""),
            remote=remote,
            department=_tt_tag(item, "tt:department") or None,
            posted_at=_iso(_tt_tag(item, "pubDate")),
            description=desc,
            # No salary field anywhere in the feed, so pay only ever comes
            # from the employer stating it in the advert body.
            salary=parse_text(desc[:1500]),
            # Deliberately not set. Teamtailor names the country in words and
            # screen.py resolves those at its highest tier; a name-to-code
            # table in here would be a second copy of that mapping, and it
            # would have to invent an answer for "Latin America", which
            # Teamtailor really does return as a country.
            source_id=src.key,
        )


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


# --------------------------------------------------------------------------
# Phenom People
# --------------------------------------------------------------------------
_PHENOM_DDO = re.compile(r"phApp\.ddo\s*=\s*(\{.*?\});\s*(?:phApp|</script>|window\.)", re.S)


def parse_phenom(payload: Any, src: Source) -> Iterator[Job]:
    """Phenom renders in the browser, but it also embeds the whole result set
    as JSON in the page under `phApp.ddo`, so there is no need to render
    anything: the jobs are already there in the HTML we fetched.

    Used by large employers who otherwise look unreachable. Serco and Thales
    both sit here. `descriptionTeaser` often carries a salary line even though
    there is no salary field.
    """
    blocks = []
    if isinstance(payload, dict):
        # The /widgets POST API, which pages properly.
        er = payload.get("refineSearch") or payload.get("eagerLoadRefineSearch") or {}
        blocks.append((er.get("data") or {}).get("jobs") or er.get("jobs") or [])
    else:
        text = payload if isinstance(payload, str) else ""
        for m in _PHENOM_DDO.finditer(text):
            try:
                ddo = json.loads(m.group(1))
            except (ValueError, TypeError):
                continue
            er = ddo.get("eagerLoadRefineSearch") or {}
            blocks.append((er.get("data") or {}).get("jobs") or er.get("jobs") or [])
            break

    for jobs in blocks:
        for j in jobs:
            title = _text(j.get("title"))
            url = j.get("applyUrl") or j.get("imApplyUrl") or ""
            if not (title and url):
                continue
            loc = _text(j.get("cityStateCountry") or j.get("location") or j.get("country"))
            teaser = _text(j.get("descriptionTeaser"))
            yield Job(
                company=src.company,
                title=title,
                url=url,
                platform="phenom",
                location=loc,
                remote=_remote(loc, title, j.get("jobType")),
                department=_text(j.get("category")) or None,
                posted_at=_iso(j.get("postedDate") or j.get("dateCreated")),
                description=teaser,
                salary=parse_text(teaser),
                source_id=src.key,
            )


# --------------------------------------------------------------------------
# SuccessFactors RMK (jobs2web)
# --------------------------------------------------------------------------
_RMK_LINK = re.compile(
    r'<a[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*href="([^"?]+)"[^>]*>\s*(.*?)\s*</a>', re.S)
_RMK_ANY = re.compile(r'href="((?:/[a-z0-9_-]+)?/job/[^"?]+)"[^>]*>\s*(.*?)\s*</a>', re.S | re.I)


def parse_rmk(payload: Any, src: Source) -> Iterator[Job]:
    """SAP SuccessFactors Recruiting Marketing, still served from jobs2web
    hostnames. Server-rendered, so it parses without a browser.

    Transport for London sit here, and so do many public bodies that look like
    they have no machine-readable board at all. The href carries a tenant
    prefix (`/tfl/job/...`) rather than a bare `/job/`, and the location is in
    the slug ahead of the title rather than in its own field.
    """
    text = payload if isinstance(payload, str) else ""
    base = f"https://{urlparse(src.url).netloc}"

    pairs = _RMK_LINK.findall(text) or _RMK_ANY.findall(text)
    seen = set()
    for path, title in pairs:
        title = _text(title)
        if not title or path in seen:
            continue
        seen.add(path)
        # "/tfl/job/Palestra-House,-Southwark,-SE1-Assistant-Safety-Manager/1349"
        slug = path.rsplit("/job/", 1)[-1]
        slug = re.sub(r"/\d+/?$", "", slug)
        slug = _text(unquote(slug).replace("-", " "))
        # The title repeats at the end of the slug; what precedes it is where.
        loc = slug
        low, tl = slug.lower(), title.lower()
        if tl and tl in low:
            loc = slug[: low.rindex(tl)].strip(" ,-")
        yield Job(
            company=src.company,
            title=title,
            url=path if path.startswith("http") else base + path,
            platform="rmk",
            location=loc,
            remote=_remote(loc, title),
            description="",
            salary=Salary(),
            source_id=src.key,
            flags=["not screened: search listing only, open the advert"],
        )


# --------------------------------------------------------------------------
# Avature
# --------------------------------------------------------------------------
# Avature serves absolute hrefs, not paths.
#
# `[^"?]` and not `[^"]` before /JobDetail/: every card also carries Twitter
# and Facebook share links whose QUERY STRING contains the job's own URL
# (`?text=<title> https://.../JobDetail/...`). Metro Bank's six roles come with
# twelve such links. They only fail to parse today because the anchor wraps an
# icon rather than text and the empty title is dropped, which is luck rather
# than a rule: the moment one carries a label the board reports three rows per
# job.
_AV_LINK = re.compile(r'href="(https?://[^"?]*?/JobDetail/[^"?]+)"[^>]*>\s*(.*?)\s*</a>', re.S)


def parse_avature(payload: Any, src: Source) -> Iterator[Job]:
    """Avature's hosted careers site. Server-rendered links to /JobDetail/,
    with the location usually in the slug rather than a separate field.
    """
    text = payload if isinstance(payload, str) else ""
    seen = set()
    for url, title in _AV_LINK.findall(text):
        title = _text(title)
        if not title or url in seen:
            continue
        seen.add(url)
        # The slug carries the location when the markup does not.
        slug = url.rsplit("/JobDetail/", 1)[-1].replace("-", " ")
        yield Job(
            company=src.company,
            title=title,
            url=url,
            platform="avature",
            location="",
            remote=_remote(slug, title),
            description=_text(slug),
            salary=Salary(),
            source_id=src.key,
            flags=["not screened: search listing only, open the advert"],
        )


# --------------------------------------------------------------------------
# iCIMS
# --------------------------------------------------------------------------
_ICIMS_ITEM = re.compile(r'<div class="row">(.*?)(?=<div class="row">|</body>)', re.S)
_ICIMS_LINK = re.compile(
    r'<a[^>]+href="(https?://[^"]*?/jobs/\d+/[^"?]+[^"]*)"[^>]*class="iCIMS_Anchor"[^>]*>(.*?)</a>',
    re.S)
_ICIMS_LOC = re.compile(
    r'field-label">Job Locations?</span>\s*<span[^>]*>\s*(.*?)\s*</span>', re.S)


def parse_icims(payload: Any, src: Source) -> Iterator[Job]:
    """iCIMS renders its results into an iframe, so the plain search page comes
    back as a shell with no jobs in it. Adding `in_iframe=1` returns the
    server-rendered list instead, which is the whole trick.

    Locations arrive pipe-separated in a single span ("UK-London |
    UK-Wolverhampton"), and the leading country code makes them read oddly, so
    they are tidied here rather than left to confuse the location filter.
    """
    text = payload if isinstance(payload, str) else ""
    seen = set()

    for block in _ICIMS_ITEM.findall(text):
        lm = _ICIMS_LINK.search(block)
        if not lm:
            continue
        # The anchor wraps a screen-reader label ("Title") before the heading,
        # which _text would otherwise fold into the job title.
        inner = re.sub(r'<span[^>]*class="[^"]*sr-only[^"]*"[^>]*>.*?</span>', " ",
                       lm.group(2), flags=re.S)
        url, title = lm.group(1), _text(inner)
        if not title or url in seen:
            continue
        seen.add(url)

        loc = ""
        lo = _ICIMS_LOC.search(block)
        if lo:
            parts = [p.strip() for p in _text(lo.group(1)).split("|") if p.strip()]
            # "UK-Kent-Chatham" reads better, and filters better, as "Chatham, UK"
            tidy = []
            for p in parts:
                bits = [b for b in p.split("-") if b]
                tidy.append(f"{', '.join(bits[1:])}, {bits[0]}" if len(bits) > 1 else p)
            loc = " / ".join(tidy)

        yield Job(
            company=src.company,
            title=title,
            url=url,
            platform="icims",
            location=loc,
            remote=_remote(loc, title),
            description="",
            salary=Salary(),
            source_id=src.key,
            flags=["not screened: search listing only, open the advert"],
        )


# --------------------------------------------------------------------------
# Reed
# --------------------------------------------------------------------------
def _screen():
    """screen.py, imported on first use rather than at module import.

    The adapter layer sits below the filter chain, and importing screen.py at
    the top of this file would make `import jobradar.adapters` drag in
    config.py behind it. config.py already defers its own screen.py import for
    the same reason, and this is the other half of that arrangement.
    """
    from .. import screen
    return screen


# Reed employers put the working arrangement in the location field instead of
# a place. screen.py knows "Remote" is not a city and knows to look in the
# body for the country; it does not know these spellings, so "Work From Home"
# came out as the city on the dashboard and as a facet you could filter by.
_REED_NOT_A_PLACE = re.compile(
    r"^\s*(?:work[\s-]?from[\s-]?home|homeworking|home[\s-]?based|home[\s-]?working|"
    r"wfh|remote(?:\s*working)?)\s*$", re.I)


def _reed_location(name: str) -> str:
    """Reed states a town and nothing else, so the country has to be added.

    reed.co.uk is a UK site and `locationName` is free text the employer
    typed: "Stoke-on-Trent", "Cambridgeshire", "City of London". screen.py
    resolves a country from a location string against a city list, and that
    list cannot hold every town and county in Britain: "Stoke-on-Trent" and
    "Cambridgeshire" both resolve to no country at all, and `match` drops a
    posting whose location it cannot place whenever the user has set
    `locations.countries`. Which is every UK user, on the majority of the
    listings, silently.

    So the country is named outright. Only where the location does not already
    name one, because Reed does carry a handful of overseas roles and
    "Dublin, United Kingdom" would file an Irish job as British: the UK marker
    is tested first, and screen.py does not split a location on the comma.
    """
    name = (name or "").strip()
    if not name:
        return "United Kingdom"
    if _REED_NOT_A_PLACE.match(name):
        # Keep the country. A Reed listing is a UK listing, and "Remote" on
        # its own is read downstream as "the employer named no country",
        # which sends the role past the country filter untested.
        return "Remote, United Kingdom"
    if _screen()._countries_in(name):
        return name
    return f"{name}, United Kingdom"


def parse_reed(payload: Any, src: Source) -> Iterator[Job]:
    """Reed's jobseeker API: https://www.reed.co.uk/api/1.0/search

    The first aggregator here that is neither an employer's own board nor an
    HTML page, and the reason it earns a place is coverage. Every other source
    is one employer's applicant tracking system, which reaches an employer only
    once somebody has added them; Reed is keyword-driven and reaches the whole
    of its UK market at once, including the mid-size employers who never
    appear on an enumerable board.

    What that costs, and what is done about it:

      * The same role is listed many times, usually once per agency. Reed
        answers that at the query, not here: `postedByDirectEmployer=true`
        asks for employers only, which is what the shipped source uses. Where
        two copies do reach the pipeline, `screen.dedupe` collapses them on
        company plus title and keeps the more direct platform.
      * `employerName` is whoever posted it. On an agency listing that is the
        agency, not the employer, so these roles cannot be trusted to name the
        company they are actually for.
      * The apply link is a reed.co.uk page rather than the employer's own
        form. Only the per-job details endpoint carries `externalUrl`, and
        that is one request per role. Each posting is flagged so the reader
        knows which kind of link they are following.

    Two failure modes worth stating. An empty `results` list means a search
    that matched nothing, which is also what a search for something misspelled
    returns, so liveness here is a result count and never a status code. And a
    missing or wrong API key is a 401, not an empty list, which is the one
    piece of good news: it cannot be mistaken for "no jobs today".
    """
    items = payload.get("results") if isinstance(payload, dict) else payload
    for j in items or []:
        if not isinstance(j, dict):
            continue

        title = _text(j.get("jobTitle"))
        url = _text(j.get("jobUrl"))
        if not url and j.get("jobId") is not None:
            url = f"https://www.reed.co.uk/jobs/{j['jobId']}"
        if not (title and url):
            continue

        desc = _text(j.get("jobDescription") or j.get("description"))
        location = _reed_location(_text(j.get("locationName")))

        sal = from_reed(j)
        if not sal.confirmed:
            # Second go at an unlabelled rate. from_reed will not guess
            # whether a bare 650 is a day rate or an hourly one, but the
            # advert almost always spells it out, and parse_text reads
            # "per day" and "per hour".
            from_advert = parse_text(desc[:1500], default_currency=sal.currency)
            if from_advert.confirmed:
                sal = from_advert

        # Reed has no remote field of any kind, so the working arrangement can
        # only come from the words. Ask screen.py rather than re-deriving it:
        # it checks hybrid BEFORE remote, which is what stops a "hybrid, 2 days
        # in the London office" advert being handed to a remote filter as a
        # remote job on the strength of containing the word.
        probe = Job(company="", title=title, url=url, platform="reed",
                    location=location, description=desc)
        mode = _screen().work_mode(probe)
        if mode == "remote":
            remote: bool | None = True
        elif mode in ("hybrid", "office"):
            remote = False
        else:
            remote = _remote(location, title)

        job = Job(
            company=_text(j.get("employerName")) or "Unknown employer",
            title=title,
            url=url,
            platform="reed",
            location=location,
            remote=remote,
            department=None,
            # Reed writes dates as dd/MM/yyyy, which `_iso` already handles.
            # `date` is the search field and `datePosted` the details one.
            posted_at=_iso(j.get("date") or j.get("datePosted")),
            description=desc,
            salary=sal,
            source_id=src.key,
        )
        job.flags.append("listed on Reed; the apply link goes via reed.co.uk")
        exp = _text(j.get("expirationDate"))
        if exp:
            job.flags.append(f"closes {exp}")
        yield job


# --------------------------------------------------------------------------
# Adzuna
# --------------------------------------------------------------------------
# Adzuna runs one index per country and the country is in the URL path, not in
# the payload: /v1/api/jobs/gb/search/1 is the British index and every figure
# in it is in pounds. Nothing in a result names the country, so an adapter that
# reads only the payload produces "Reading, Berkshire" with no country, and
# `match` drops a posting it cannot place the moment `locations.countries` is
# set. That is the same failure Reed had, arriving by a different route.
_ADZUNA_COUNTRIES = {
    "gb": ("United Kingdom", "GBP"), "us": ("United States", "USD"),
    "at": ("Austria", "EUR"), "au": ("Australia", "AUD"),
    "be": ("Belgium", "EUR"), "br": ("Brazil", "BRL"),
    "ca": ("Canada", "CAD"), "ch": ("Switzerland", "CHF"),
    "de": ("Germany", "EUR"), "es": ("Spain", "EUR"),
    "fr": ("France", "EUR"), "in": ("India", "INR"),
    "it": ("Italy", "EUR"), "mx": ("Mexico", "MXN"),
    "nl": ("Netherlands", "EUR"), "nz": ("New Zealand", "NZD"),
    "pl": ("Poland", "PLN"), "sg": ("Singapore", "SGD"),
    "za": ("South Africa", "ZAR"),
}

_ADZUNA_PATH = re.compile(r"/v1/api/jobs/([a-z]{2})/search/", re.I)


def adzuna_country(url: str) -> tuple[str, str]:
    """The country name and currency behind an Adzuna search URL.

    Falls back to the British index because that is what the shipped builder
    produces, but the code is read from the URL first so pointing the source at
    /jobs/ca/ or /jobs/au/ works without touching this file. An unknown code
    yields no country name at all rather than a wrong one: naming the wrong
    country is worse than naming none, because a wrong name passes the filter.
    """
    m = _ADZUNA_PATH.search(url or "")
    code = (m.group(1) if m else "gb").lower()
    return _ADZUNA_COUNTRIES.get(code, ("", ""))


def _adzuna_location(display: str, country: str) -> str:
    """Adzuna's `display_name` is a town and a county, never a country.

    Same treatment as `_reed_location`, and for the same reason: screen.py
    resolves a country from a city list that cannot hold every town in
    Britain, and an unplaceable location is a dropped posting. The country is
    only added where the string does not already name one, so a listing on the
    British index that says "Dublin, Ireland" is not relabelled as British.
    """
    display = (display or "").strip()
    if not country:
        return display
    if not display:
        return country
    if _screen()._countries_in(display):
        return display
    return f"{display}, {country}"


def parse_adzuna(payload: Any, src: Source) -> Iterator[Job]:
    """Adzuna's search API: https://api.adzuna.com/v1/api/jobs/{country}/search/{page}

    A keyword-driven aggregator like Reed, and it earns its place for the same
    reason: it reaches employers nobody has added to the source list. It is
    broader than Reed in one way that matters here, which is that it runs
    nineteen national indexes, so the same config that watches the UK can watch
    the United States, Canada and Australia by changing two letters in a URL.

    Four things about the payload that cost a role each if missed:

      * **The salary may be a guess.** `salary_is_predicted` is "1" when the
        number came from Adzuna's Jobsworth model rather than the advertiser.
        `from_adzuna` refuses to confirm those, because only a confirmed figure
        can disqualify a posting and a modelled one would do it silently.
      * **The country is in the URL, not the payload.** See `adzuna_country`.
      * **There is no remote field**, so the arrangement comes from the words,
        via `screen.work_mode`, which tests hybrid before remote.
      * **The description is truncated to 500 characters** by Adzuna's own
        documentation, so it is a preview and not the advert. `enrich` cannot
        expand it either: `redirect_url` is a redirector rather than a page.

    Adzuna has no direct-employer filter of any kind, unlike Reed's
    `postedByDirectEmployer`, so agency listings arrive mixed in with employer
    ones and `company.display_name` is whoever placed the advert.
    """
    items = payload.get("results") if isinstance(payload, dict) else payload
    country, currency = adzuna_country(src.url)

    for j in items or []:
        if not isinstance(j, dict):
            continue

        title = _text(j.get("title"))
        # `redirect_url` is the link Adzuna's terms require you to send people
        # to, and it is also the only one that reaches the advertiser.
        url = _text(j.get("redirect_url"))
        if not (title and url):
            continue

        desc = _text(j.get("description"))
        loc = j.get("location")
        display = _text(loc.get("display_name")) if isinstance(loc, dict) else _text(loc)
        location = _adzuna_location(display, country)

        sal = from_adzuna(j, currency)
        if not sal.confirmed:
            # The truncated advert gets a go at it, exactly as with Reed. An
            # employer who wrote "£150,000 - £170,000" into the first line of
            # the advert beats both silence and a Jobsworth estimate.
            from_advert = parse_text(desc, default_currency=currency)
            if from_advert.confirmed:
                sal = from_advert

        probe = Job(company="", title=title, url=url, platform="adzuna",
                    location=location, description=desc)
        mode = _screen().work_mode(probe)
        if mode == "remote":
            remote: bool | None = True
        elif mode in ("hybrid", "office"):
            remote = False
        else:
            remote = _remote(location, title)

        company = j.get("company")
        category = j.get("category")
        job = Job(
            company=(_text(company.get("display_name")) if isinstance(company, dict)
                     else _text(company)) or "Unknown employer",
            title=title,
            url=url,
            platform="adzuna",
            location=location,
            remote=remote,
            department=(_text(category.get("label"))
                        if isinstance(category, dict) else "") or None,
            posted_at=_iso(j.get("created")),
            description=desc,
            salary=sal,
            source_id=src.key,
        )
        job.flags.append("listed on Adzuna; the apply link redirects to the "
                         "advertiser")
        # A contract advertised at a day rate is annualised by Adzuna before we
        # ever see it, which is how a six month contract clears a permanent
        # salary floor. Say which kind of job it is on the row rather than
        # trying to undo the arithmetic.
        if str(j.get("contract_type") or "").lower() == "contract":
            job.flags.append("contract, not permanent")
        if str(j.get("contract_time") or "").lower() == "part_time":
            job.flags.append("part time")
        if not sal.confirmed and str(j.get("salary_is_predicted") or "") == "1":
            job.flags.append("pay figure is an Adzuna estimate, not the "
                             "employer's")
        yield job
