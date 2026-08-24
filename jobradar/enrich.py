"""Fetch the full posting for roles whose source only returned a headline.

LinkedIn's search endpoint returns a title, a company and a location. Nothing
else. That is a quarter to a half of a typical board, and for every one of
those roles the tool was silently doing nothing: dealbreakers had no text to
run against, the salary floor had no figure to compare, `rank` skipped them
because there was nothing to judge fit on, and `generate` refused outright.
They were leads pretending to be matches.

LinkedIn publishes each posting separately, one job id at a time, and that
response carries the whole description. So the missing text is one request per
role rather than something the design has to live without.

This is a read. It spends no tokens. It is also 125 requests to somebody
else's servers on a normal run, so it goes one at a time with a pause, skips
anything it already has, and gives up on a role quietly rather than retrying
it into the ground.

The robots.txt position is the same one the README already discloses for the
search endpoint: LinkedIn disallows it, this reads it anyway, and that is a
deliberate choice a user should know they are making.
"""

from __future__ import annotations

import html as _h
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import requests

from . import fetch as fetch_mod, salary as sal_mod, store

# One posting, by id. The same guest surface the search endpoint lives on.
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# The description sits in one block. Everything else on the page is chrome.
# Capture what is INSIDE the block. Matching from the class name onwards left
# `description__text description__text--rich">` sitting at the front of every
# description, which is noise in the token budget and in anything reading it.
_BLOCK = re.compile(r'description__text[^>]*>(.*?)</section>', re.S)
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>|</p>|</li>", re.I)

# Trailing digits of a LinkedIn job URL are its id.
_JOB_ID = re.compile(r"(\d{6,})(?:[/?#]|$)")


def job_id(url: str) -> str:
    m = _JOB_ID.search((url or "").split("?")[0])
    return m.group(1) if m else ""


def _text(page: str) -> str:
    m = _BLOCK.search(page)
    if not m:
        return ""
    # Keep the line breaks: a description that arrives as one wall of text
    # loses the bullet structure the dealbreaker patterns read best against.
    body = _BR.sub("\n", m.group(1))
    body = _h.unescape(_TAG.sub(" ", body))
    lines = [" ".join(x.split()) for x in body.split("\n")]
    return "\n".join(x for x in lines if x).strip()


def fetch(url: str, session=None, timeout: int = 20) -> str:
    jid = job_id(url)
    if not jid:
        return ""
    get = (session or requests).get
    try:
        r = get(JOB_URL.format(job_id=jid), headers={"User-Agent": UA},
                timeout=timeout)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    return _text(r.text)


# Workday and SmartRecruiters both omit the description from their list
# endpoints and both publish it per job. Same shape of problem as LinkedIn,
# same fix, so they share the machinery rather than each growing their own.
_WD_URL = re.compile(r"https://([^/]+)/([a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+)$")


def _workday_api(url: str) -> str:
    """Turn a human Workday URL into its CXS one.

    https://x.wd5.myworkdayjobs.com/en-US/site/job/City/Title_R123
      -> https://x.wd5.myworkdayjobs.com/wday/cxs/x/site/job/City/Title_R123
    """
    m = _WD_URL.match((url or "").split("?")[0])
    if not m:
        return ""
    host, _lang, site, path = m.groups()
    tenant = host.split(".")[0]
    return f"https://{host}/wday/cxs/{tenant}/{site}/job/{path}"


def _from_workday(url: str, session=None, timeout: int = 20) -> str:
    api = _workday_api(url)
    if not api:
        return ""
    get = (session or requests).get
    try:
        r = get(api, headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=timeout)
        if r.status_code != 200:
            return ""
        info = (r.json() or {}).get("jobPostingInfo") or {}
    except (requests.RequestException, ValueError):
        return ""
    return _strip(info.get("jobDescription") or "")


_SR_URL = re.compile(r"smartrecruiters\.com/([^/]+)/postings?/(\d+)")


def _from_smartrecruiters(url: str, session=None, timeout: int = 20) -> str:
    m = _SR_URL.search(url or "")
    if not m:
        return ""
    company, posting = m.groups()
    get = (session or requests).get
    try:
        r = get(f"https://api.smartrecruiters.com/v1/companies/{company}"
                f"/postings/{posting}", timeout=timeout)
        if r.status_code != 200:
            return ""
        secs = ((r.json() or {}).get("jobAd") or {}).get("sections") or {}
    except (requests.RequestException, ValueError):
        return ""
    # Keep the qualifications section: it is where the must-haves live, which
    # is what dealbreakers and fit are actually judged on.
    order = ("jobDescription", "qualifications", "additionalInformation",
             "companyDescription")
    return _strip("\n\n".join((secs.get(k) or {}).get("text") or ""
                               for k in order if secs.get(k)))


def _strip(markup: str) -> str:
    body = _BR.sub("\n", markup or "")
    body = _h.unescape(_TAG.sub(" ", body))
    lines = [" ".join(x.split()) for x in body.split("\n")]
    return "\n".join(x for x in lines if x).strip()


# Breezy publishes schema.org JSON-LD on every posting page so that Google
# Jobs can index it. That block carries the whole advert, which the `/json`
# board endpoint does not carry at all, so this is reading a documented
# structure rather than scraping their markup.
_LD_BLOCK = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _json_ld_text(page: str) -> str:
    """The advert out of a page's schema.org JobPosting block.

    Split out from `_from_json_ld` because iCIMS needs the same reader against
    a URL it will not serve unmodified: see `_from_icims`.
    """
    for m in _LD_BLOCK.finditer(page or ""):
        try:
            node = json.loads(m.group(1))
        except ValueError:
            continue
        # There are two blocks on a Breezy page and the first one is a WebSite,
        # so taking the first match returned an empty description every time.
        for d in (node if isinstance(node, list) else [node]):
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                return _strip(d.get("description") or "")
    return ""


def _page(url: str, session=None, timeout: int = 20) -> str:
    """A posting page's HTML, or "" for anything that is not a clean 200.

    The board links carry `?source=...` on some postings; the page is the same
    without it and the shorter URL is what the seen-set is keyed on.
    """
    get = (session or requests).get
    try:
        r = get((url or "").split("?")[0], headers={"User-Agent": UA},
                timeout=timeout)
    except requests.RequestException:
        return ""
    return r.text if r.status_code == 200 else ""


def _from_json_ld(url: str, session=None, timeout: int = 20) -> str:
    """The advert out of a posting page's schema.org JobPosting block.

    Breezy, Jobvite and Avature all publish one so that Google Jobs can index
    them, and none of them puts the advert in its list endpoint. Same problem,
    same fix, so they share this rather than each growing their own copy.
    """
    return _json_ld_text(_page(url, session, timeout))


_from_breezy = _from_json_ld
_from_jobvite = _from_json_ld
# JazzHR publishes an Organization block on the posting page but no
# JobPosting one, so the shared JSON-LD reader comes back empty. The advert
# itself sits in a div with a stable id, which is what this reads instead.
_JZ_DESC = re.compile(
    r'<div[^>]+id="job-description"[^>]*>(.*?)</div>\s*(?:<div|<section|<footer)',
    re.S | re.I)


def _from_jazzhr(url: str, session=None, timeout: int = 20) -> str:
    get = (session or requests).get
    try:
        r = get((url or "").split("?")[0], headers={"User-Agent": UA},
                timeout=timeout)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    m = _JZ_DESC.search(r.text)
    return _strip(m.group(1)) if m else ""


# Taleo's posting page renders itself from JavaScript too, so there is no
# JobPosting JSON-LD and no description element to read. The advert is in a
# `api.fillList(... 'descRequisition', [...])` array, URL-encoded, and the
# INDEX of it moves: it is element 10 on TTEC and element 11 on BAE Systems,
# because BAE's career section adds a job-field pair that TTEC's does not.
# Every other element in the array is a requisition id, a boolean, a location
# or a one-line label, so the advert is simply the longest one. Reading it by
# position instead would return the string "false" for half the platform.
_TL_DESC_LIST = re.compile(
    r"api\.fillList\(\s*'requisitionDescriptionInterface'\s*,\s*"
    r"'descRequisition'\s*,\s*\[(.*?)\]\s*\)\s*;", re.S)
_TL_DESC_ITEM = re.compile(r"'((?:[^'\\]|\\.)*)'", re.S)


def _from_taleo(url: str, session=None, timeout: int = 20) -> str:
    get = (session or requests).get
    try:
        r = get(url or "", headers={"User-Agent": UA}, timeout=timeout)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    m = _TL_DESC_LIST.search(r.text)
    if not m:
        return ""
    best = ""
    for item in _TL_DESC_ITEM.findall(m.group(1)):
        # `!*!` is Taleo's own marker for "this element is rich text", not part
        # of the advert, and it lands at the front of the description.
        text = _strip(unquote(item.replace("!*!", "")))
        if len(text) > len(best):
            best = text
    return best


# BambooHR's `/careers/list` is a summary index: no advert text, no salary, no
# date. The advert lives one request away at `/careers/<id>/detail`, which is
# the same JSON API the board itself is built on rather than a page scrape.
def _from_bamboohr(url: str, session=None, timeout: int = 20) -> str:
    base = (url or "").split("?")[0].rstrip("/")
    if not re.search(r"bamboohr\.com/careers/\d+$", base):
        return ""
    get = (session or requests).get
    try:
        r = get(f"{base}/detail", headers={"User-Agent": UA,
                                           "Accept": "application/json"},
                timeout=timeout)
        if r.status_code != 200:
            return ""
        job = ((r.json() or {}).get("result") or {}).get("jobOpening") or {}
    except (requests.RequestException, ValueError):
        return ""
    text = _strip(job.get("description") or "")
    # The detail record has a `compensation` string the list endpoint does not.
    # Putting it in front of the advert is what lets `run()` re-parse pay for
    # these roles, which otherwise carry no figure from anywhere.
    pay = (job.get("compensation") or "").strip()
    return f"Compensation: {pay}\n\n{text}".strip() if pay else text


# Oracle and SuccessFactors both write their adverts as a chain of <div>s with
# no <p> or <li> in them at all, so `_strip`'s line breaks never fire and the
# whole advert arrives as one unbroken line. The dealbreaker patterns read
# best against bullet structure, and a heading welded to the sentence after it
# ("We are Reckitt Home to the world's best loved brands") also reads as one
# phrase to anything scanning for a job title or a seniority word.
_BLOCK_END = re.compile(r"</(?:div|h[1-6]|tr|table|ul|ol)>", re.I)


def _strip_blocks(markup: str) -> str:
    return _strip(_BLOCK_END.sub("\n", markup or ""))


def _inner_blocks(page: str, opener, tag: str):
    """The inside of every element `opener` matches, closing tags counted.

    A lazy `(.*?)</span>` is what this replaces and it is not a small
    difference: both Avature's and SuccessFactors' advert containers nest
    further elements of the same tag inside themselves (EA's Avature advert
    holds 12 more divs, PSEG's SuccessFactors advert enough spans that a lazy
    match returns 121 characters of its 15,758), so the lazy form stops at the
    first inner close and yields a fragment. A fragment over 200 characters
    gets stored and treated as the whole advert, which is worse than returning
    nothing, because nothing at least leaves the role visibly unscreened.
    """
    pair = re.compile(rf"<{tag}\b|</{tag}>", re.I)
    for m in opener.finditer(page or ""):
        rest = page[m.end():]
        depth = 1
        for t in pair.finditer(rest):
            depth += -1 if t.group(0).lower() == f"</{tag}>" else 1
            if depth == 0:
                yield rest[:t.start()]
                break
        else:
            # Unbalanced markup: take what is there rather than dropping the
            # advert entirely.
            yield rest


# iCIMS renders its posting into an iframe exactly the way it renders its
# search results into one. The bare posting URL answers 200 with a ~3.8KB
# shell that contains no advert and no JSON-LD, so the shared reader returns
# "" on a page that looks perfectly healthy. `in_iframe=1` returns the
# server-rendered posting, JobPosting block and all. This is the same trick
# `parse_icims` already uses on the search endpoint.
#
# The query string is rebuilt rather than kept, because a stored URL that lost
# its `in_iframe=1` somewhere (a redirect, a hand-edited source) would enrich
# to nothing with no error to show for it.
_ICIMS_JOB = re.compile(r"/jobs/\d+/", re.I)


def _from_icims(url: str, session=None, timeout: int = 20) -> str:
    base = (url or "").split("?")[0]
    if not _ICIMS_JOB.search(base):
        return ""
    get = (session or requests).get
    try:
        r = get(f"{base}?in_iframe=1", headers={"User-Agent": UA},
                timeout=timeout)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    return _json_ld_text(r.text)


# Oracle Recruiting Cloud's posting page is a JavaScript shell: 4.4KB, no
# JSON-LD, no advert. The text is in the same REST API `parse_oracle` already
# reads the list from, one requisition at a time.
#
# The resource is `recruitingCEJobRequisitionDetails`, PLURAL. The singular
# spelling answers 404 with an empty body, which is indistinguishable from a
# dead board, so this is not a name to guess at.
#
# The site number has to come from the URL. It is CX_1 on most tenants but not
# all, and `parse_oracle` already carries whatever the source said through
# into the job URL, so it is read back out here rather than assumed.
_ORACLE_JOB = re.compile(
    r"^https?://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/([^/?#]+)",
    re.I)
_ORACLE_API = ("https://{host}/hcmRestApi/resources/latest/"
               "recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
               "&finder=ById;Id=%22{rid}%22,siteNumber={site}")


def _from_oracle(url: str, session=None, timeout: int = 20) -> str:
    m = _ORACLE_JOB.match(url or "")
    if not m:
        return ""
    host, site, rid = m.groups()
    get = (session or requests).get
    try:
        r = get(_ORACLE_API.format(host=host, rid=rid, site=site),
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=timeout)
        if r.status_code != 200:
            return ""
        items = (r.json() or {}).get("items") or []
    except (requests.RequestException, ValueError):
        return ""
    if not items or not isinstance(items[0], dict):
        return ""
    job = items[0]
    # The advert is split across fields and which ones are filled varies by
    # tenant: Marks and Spencer put all 8,762 characters in
    # ExternalDescriptionStr and leave responsibilities and qualifications
    # empty, while Mashreq's ExternalDescriptionStr is a 349 character stub
    # with the real advert in the other two. Reading one field alone loses
    # whole adverts either way.
    #
    # ShortDescriptionStr is deliberately excluded: it is a teaser cut from
    # the description, so including it scores the same paragraph twice.
    order = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
             "ExternalQualificationsStr", "CorporateDescriptionStr",
             "OrganizationDescriptionStr")
    parts = []
    for k in order:
        text = _strip_blocks(job.get(k) or "")
        # Some tenants fill every field with the same advert. One measured
        # board had responsibilities and qualifications byte-identical to each
        # other and both a re-encoding of the description, so a plain join
        # stored the advert three times: three times the tokens for `rank` to
        # read and three times the chance of tripping the 20,000 character cap
        # before the end of the advert is reached.
        if not text or any(text in p for p in parts):
            continue
        parts = [p for p in parts if p not in text]
        parts.append(text)
    return "\n\n".join(parts)


# Avature serves a JobPosting block on some tenants and none at all on others:
# Tesco's careers site has one, EA's sandbox board has zero, and both are
# ordinary Avature installs. So the shared reader is tried first and a second
# reader stands behind it rather than the platform being called done on the
# strength of one board that happened to work.
#
# The fallback reads Avature's own template class rather than an employer
# theme's markup, which is why it holds across tenants: the same
# `article__content__view__field__value` divs are on the Tesco page that did
# not need them. Every field block is taken, not just the advert one, because
# the header of the advert block is localised ("Descripción del puesto") and
# keying on it would fail on exactly the non-English boards this is for. The
# other blocks are the location, the worker type and the req id, which are
# short and are worth screening against anyway.
_AV_FIELD = re.compile(
    r'<div[^>]*\bclass="[^"]*\barticle__content__view__field__value\b[^"]*"[^>]*>',
    re.I)


def _from_avature(url: str, session=None, timeout: int = 20) -> str:
    page = _page(url, session, timeout)
    if not page:
        return ""
    text = _json_ld_text(page)
    if text:
        return text
    parts = [_strip_blocks(b) for b in _inner_blocks(page, _AV_FIELD, "div")]
    return "\n\n".join(p for p in parts if p).strip()


# SuccessFactors RMK publishes no JSON-LD at all: zero blocks on Reckitt and
# zero on Burberry, so the shared reader comes back empty on a 200. The advert
# sits in <span class="jobdescription">, which is stable across tenants
# because it is SuccessFactors' own markup rather than the employer's theme.
#
# The close is found by counting, not by a lazy `(.*?)</span>`, because most
# adverts nest further spans inside that one. Measured on live boards: PSEG's
# advert is 15,758 characters and a lazy match returns 121 of them, Cintas
# 5,124 against 133, Medibank 8,354 against 153. Every one of those would be
# under the 200-character floor and so would look like a failed fetch, but
# Hikma's returns 1,053 of 2,375, which would be stored as the whole advert.
#
# The opening tag varies: some tenants serve `<span class="jobdescription">`
# and others `<span itemprop="description" class="jobdescription">`, so the
# class is matched as a word anywhere in the tag rather than as the whole
# attribute.
_RMK_DESC_OPEN = re.compile(
    r'<span[^>]*\bclass="[^"]*\bjobdescription\b[^"]*"[^>]*>', re.I)


def _from_rmk(url: str, session=None, timeout: int = 20) -> str:
    page = _page(url, session, timeout)
    if not page:
        return ""
    for body in _inner_blocks(page, _RMK_DESC_OPEN, "span"):
        return _strip_blocks(body)
    return ""


# Which fetcher handles which platform. A platform absent from here is one
# whose list endpoint already carries the description.
FETCHERS = {
    "linkedin": lambda u, s: fetch(u, session=s),
    "workday": _from_workday,
    "smartrecruiters": _from_smartrecruiters,
    "breezy": _from_breezy,
    "bamboohr": _from_bamboohr,
    "jobvite": _from_jobvite,
    "jazzhr": _from_jazzhr,
    "taleo": _from_taleo,
    "icims": _from_icims,
    "oracle": _from_oracle,
    "avature": _from_avature,
    "rmk": _from_rmk,
}


def candidates(con, limit: int = 0) -> list:
    """Roles on the board that have a URL we can expand and no description."""
    store._ensure_columns(con)
    q = ("SELECT r.uid, r.url, r.platform, r.salary_confirmed FROM roles r "
         "LEFT JOIN role_state s ON s.uid = r.uid "
         "WHERE COALESCE(s.status,'new') NOT IN "
         "('rejected','withdrawn','skipped','closed') "
         f"AND {store.LIVE_SQL} "
         # Was a second, hand-maintained copy of FETCHERS' keys. Adding Breezy
         # to one and not the other writes a fetcher that never runs, and the
         # symptom is silence rather than an error.
         f"AND r.platform IN ({','.join('?' for _ in FETCHERS)}) "
         "AND LENGTH(TRIM(COALESCE(r.description,''))) < 200")
    rows = con.execute(q, tuple(FETCHERS)).fetchall()
    return rows[:limit] if limit else rows


def run(con, cfg=None, rows=None, pause: float = 1.0, on_each=None,
        concurrency: int = fetch_mod.DEFAULT_CONCURRENCY) -> tuple[int, int]:
    """Fill in descriptions. Returns (fetched, attempted).

    Re-parses pay while it is there: a posting that states a salary in its body
    was being carried as "unconfirmed" purely because the body had never been
    read, which meant the floor could not act on it either.
    """
    rows = candidates(con) if rows is None else rows
    got = 0
    for i, r, text in _texts(rows, pause, concurrency):
        if text and len(text) >= 200:
            got += 1
            fields = {"description": text[:20000]}
            s = sal_mod.parse_text(text, cfg.salary_currency if cfg else None)
            if s.confirmed and not r["salary_confirmed"]:
                fields.update({
                    "salary_min": s.min, "salary_max": s.max,
                    "salary_currency": s.currency, "salary_period": s.period,
                    "salary_confirmed": 1, "salary_label": s.label(),
                })
            con.execute(
                "UPDATE roles SET " + ",".join(f"{k}=?" for k in fields)
                + " WHERE uid=?", (*fields.values(), r["uid"]))
        if on_each:
            on_each(i, len(rows), got)
    return got, len(rows)


def _texts(rows, pause: float, concurrency: int):
    """Yield (position, row, advert text) with the fetching done in parallel.

    This pass used to be strictly serial with a fixed one second sleep between
    every row, which is the same mistake the scan itself made: a single global
    delay standing in for politeness towards each individual host. It costs
    whole minutes for nothing. These are one posting page per role, spread
    across employer domains and ATS hosts, so almost every consecutive pair is
    a different server and there was never a reason for them to queue.

    The database writes stay on the caller's thread. A sqlite connection may
    not be used from the thread that did not open it, and a scan that fetched
    faster but corrupted the roles table would not be an improvement.

    `pause` still works and still means what it said, for anyone who set it,
    and passing concurrency=1 restores the old behaviour exactly.
    """
    fetchers = [(i, r, FETCHERS.get(r["platform"]))
                for i, r in enumerate(rows, 1)]
    if concurrency <= 1:
        session = requests.Session()
        for i, r, fetcher in fetchers:
            yield i, r, (fetcher(r["url"], session) if fetcher else "")
            if pause and i < len(rows):
                time.sleep(pause)
        return

    limiter = fetch_mod.HostLimiter()
    local = threading.local()

    def one(item):
        i, r, fetcher = item
        if not fetcher:
            return i, r, ""
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
        # Same pacing object the scan uses, so a posting page on
        # boards-api.greenhouse.io is queued behind the board listings rather
        # than racing them, and a host that has blocked us is not asked again.
        if limiter.blocked_for(r["url"]) > 0:
            return i, r, ""
        limiter.wait(r["url"])
        try:
            return i, r, fetcher(r["url"], session)
        except Exception:
            # One unreadable posting must not end the pass. Before this ran in
            # a pool the loop had the same exposure, and a role with no text
            # passes every dealbreaker by default, so losing the rest of the
            # batch to one bad page is the expensive failure here.
            return i, r, ""

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for i, r, text in ex.map(one, fetchers):
            yield i, r, text
