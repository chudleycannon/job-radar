"""Find an employer's job board from their careers page.

This is the piece that stops the source list being a hand-curated artifact
that rots. Given a company domain or careers URL it follows the redirect
chain, reads the landing page for an embedded ATS, extracts the token, builds
the API URL and proves it by counting live postings.

It matters most for Workday, where tenant and site names cannot be guessed at
all. 117 attempts at guessing them produced zero working tenants; following
the careers-page redirect works first time.

Identity is checked, not assumed. A token that merely responds is not proof it
is the right company: Ashby `primer` is a Florida schools operator, not the
London payments company, and Greenhouse `peak` is a Texas physiotherapy chain.
So `verify` compares the board's own apply links and company name against the
domain you asked for and reports a mismatch rather than banking it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import urlparse

import requests

from . import adapters
from .models import Source

UA = "job-radar/0.1 (+https://github.com/maccydee/job-radar) source discovery"

CAREERS_PATHS = [
    "/careers", "/jobs", "/careers/jobs", "/about/careers", "/company/careers",
    "/en/careers", "/work-with-us", "/join-us", "/vacancies", "/careers/open-roles",
]

# Signatures found in the redirect target or the page body.
SIGNATURES: list[tuple[str, str]] = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_.-]+)"),
    ("greenhouse", r"greenhouse\.io/v1/boards/([a-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)"),
    ("ashby", r"ashbyhq\.com/posting-api/job-board/([a-z0-9_.-]+)"),
    # Lever tokens are case-sensitive on the wire: api.eu.lever.co answers 200
    # for `Expana` and 404 for `expana`. The character class is spelled with
    # both cases so that stays true if the re.I in `_scan` is ever dropped, and
    # nothing here may lowercase the capture.
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9_.-]+)"),
    # Lever's EU deployment is a different host with different data, so it
    # needs its own signature. `jobs.lever.co/` cannot match `jobs.eu.lever.co/`
    # (the literal substring is not there), so the two never collide. Europe is
    # where the boards actually are: jobs.lever.co/robots.txt is Disallow:/ for
    # CCBot, ClaudeBot and GPTBot, jobs.eu.lever.co/robots.txt is Allow:/, so a
    # crawl-derived employer list finds EU boards and almost no US ones.
    ("lever_eu", r"jobs\.eu\.lever\.co/([A-Za-z0-9_.-]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9_.-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_.-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("breezy", r"([a-z0-9-]+)\.breezy\.hr"),
    ("teamtailor", r"([a-z0-9-]+)\.teamtailor\.com"),
    # Pinpoint also sells custom careers domains (careers.<employer>.com), and
    # a board on one of those is invisible to a hostname signature. Those have
    # to be added by hand; this finds the subdomain-hosted ones.
    ("pinpoint", r"([a-z0-9-]+)\.pinpointhq\.com"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("jazzhr", r"([a-z0-9-]+)\.applytojob\.com"),
    # Jobvite is the odd one out: the token is a path segment, not a
    # subdomain, because every customer sits on the one jobs.jobvite.com host.
    ("jobvite", r"jobs\.jobvite\.com/([A-Za-z0-9_.-]+)"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    # Oracle needs the whole host, not a short token, and the host bears no
    # relation to the company name.
    ("oracle", r"([a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com)"),
    # Avature and RMK are whole-host platforms with a path prefix on top, so
    # their token is composite: `host|prefix`, the same string
    # `adapters.build_avature` takes. They used to capture `host/prefix` in one
    # group, which nothing could build a URL from, so `_scan` dropped every hit
    # (it skips platforms with no `build`) and neither platform was ever found
    # by `discover` at all.
    ("avature", r"([a-z0-9-]+\.avature\.net)/([a-zA-Z0-9_-]+)"),
    # The customer-hosted form, which is the one that matters. Avature serves
    # as often from the employer's own domain as from its own, and Tesco is on
    # careers.tesco.com: a host signature cannot see it, so the signature has
    # to be the path. Tesco's prefix is two segments (`en_GB/careersmarketplace`),
    # hence the optional second one.
    ("avature",
     r"(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)"
     r"/([a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)?)/SearchJobs"),
    ("rmk", r"([a-z0-9-]+\.jobs2web\.com)/([a-zA-Z0-9_-]+)"),
    # Taleo is composite for a different reason to Avature and RMK: the second
    # group is not a vendor path prefix, it is which of the tenant's career
    # sections this is, and a tenant runs several with no default among them.
    # Hilton's is `us_hotel_ext`, Transport for London's is `external`,
    # D.R. Horton's and TTEC's are both `2`. Guessing the section does not
    # fail loudly either: a section that does not exist answers 200 with
    # "Career Section Unavailable", which reads as an empty board.
    ("taleo", r"([a-z0-9-]+)\.taleo\.net/careersection/([a-zA-Z0-9_-]+)"),
    ("icims", r"([a-z0-9-]+)\.icims\.com"),
]

# Workday needs two captures (tenant, site) and its own URL shape.
WORKDAY_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([a-zA-Z0-9_-]+)",
    re.I,
)

# "app", "help" and "support" are here for Breezy: a careers page that embeds a
# Breezy board also links app.breezy.hr and help.breezy.hr, and each of those
# would otherwise be offered as a separate employer board to go and validate.
# "career", "careers", "partner" and "dashboard" are the same problem on
# Teamtailor: support.teamtailor.com and partner.teamtailor.com are the
# vendor's own, and career.teamtailor.com is Teamtailor recruiting for itself,
# which is a real board but never the board of the employer whose page we just
# read. The cost of listing it is that Teamtailor-the-employer has to be added
# by hand; the cost of not listing it is offering their board as every
# customer's board.
_JUNK_TOKENS = {"embed", "job_board", "v1", "boards", "jobs", "api", "www",
                "search", "app", "help", "support", "blog",
                "career", "careers", "partner", "dashboard", "developers",
                # A live BambooHR board page links four of the vendor's own
                # hosts alongside the employer's: staticfe (assets), images4,
                # bhrpendo (analytics) and resources (marketing). Each would
                # otherwise be handed to a maintainer as an employer board.
                "staticfe", "images4", "bhrpendo", "resources", "documentation",
                "static", "images", "assets", "cdn"}

# Platforms we can recognise but cannot read yet. Naming them turns fifteen
# identical shrugs into a diagnosis, and tells the maintainer which adapter to
# write next from real runs rather than guesswork. UK charity and public-sector
# recruitment runs almost entirely on these.
UNSUPPORTED = [
    ("Eploy", r"\beploy\b"),
    ("Hireserve", r"hireserve|/jobs/home/?(?=\s|[?#]|$)"),
    ("Jobtrain", r"jobtrain\.co\.uk|/Home/Job(?=\s|[?/#]|$)"),
    ("Networx", r"networxrecruitment\.com"),
    ("Oracle EBS iRecruitment", r"/OA_HTML/"),
    ("Oleeo", r"oleeo\.com|\.tal\.net"),
    # 589 employer hosts and not one of them readable. The careers page is an
    # empty SPA shell and the job data comes from `services/x/career-site/v1/`,
    # which answers 401 "no Authorization header found" to everything. The
    # page mints that token at runtime, so the only way in is lifting it back
    # out, which is not a published API and is not something this tool does.
    # Named here so an employer on it gets a diagnosis rather than a shrug.
    ("Cornerstone OnDemand", r"\.csod\.com"),
    # Taleo used to sit here. It has an adapter now, so it is in SIGNATURES
    # instead. The older, pre-faceted career sections still cannot be read,
    # but they are indistinguishable from the readable ones by URL shape, so
    # naming them here would mean labelling every readable Taleo board
    # unsupported. `fetch_taleo` reports the difference instead, by name, once
    # it has actually looked at the page.
    ("iCIMS portal", r"icims\.com/jobs/search(?!.*in_iframe)"),
    ("Workday (site unknown)", r"myworkdayjobs\.com(?!.*/wday/cxs)"),
    ("Civil Service Jobs", r"civilservicejobs\.service\.gov\.uk"),
    ("CharityJob", r"charityjob\.co\.uk"),
]


def detect_unsupported(text: str, url: str) -> str:
    blob = f"{url}\n{text[:200_000]}"
    for name, pat in UNSUPPORTED:
        if re.search(pat, blob, re.I):
            return name
    return ""


@dataclass
class Found:
    company: str
    url: str
    platform: str
    token: str
    domain: str | None = None
    live_jobs: int = 0
    identity: str = "unchecked"   # ok | mismatch | unchecked
    note: str = ""

    def to_source(self) -> Source:
        return adapters.prepare(Source(
            company=self.company, url=self.url, platform=self.platform,
            domain=self.domain,
        ))


# Some large employers put their careers site behind bot protection. Tesco
# answers 403 from Akamai; Sainsbury's replies "You got banned permanently from
# this server". That is a clear no, and the honest thing is to report it as
# blocked rather than as "no job board found" and rather than working around
# it. Nothing in this tool tries to defeat bot protection.
_BLOCKED = re.compile(
    r"access denied|you got banned|permission to access|cloudflare|"
    r"captcha|are you a robot|bot detection|request blocked|akamai",
    re.I,
)


def _get(url: str, timeout: int = 12) -> requests.Response | None:
    try:
        return requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": UA,
                                     "Accept": "text/html,application/json"})
    except requests.RequestException:
        return None


def _is_blocked(r: requests.Response | None) -> bool:
    """Did this host actually refuse us?

    Substring-matching "cloudflare" or "captcha" over any response body marked
    three working charity sites as bot-protected: a Cloudflare-served 404 for
    a path that does not exist is not a block, and neither is a hidden captcha
    field on a job-alert signup form inside a perfectly good 200.
    """
    if r is None:
        return False
    if r.status_code in (401, 403, 429):
        return True
    # A page that served us content did not refuse us, whatever words are in it.
    if r.status_code < 400:
        return False
    return bool(_BLOCKED.search(r.text[:2000])) if r.text else False


def _candidates(target: str) -> list[str]:
    """Careers URLs to try, in rough order of likelihood.

    Large employers rarely put the ATS on `<domain>/careers`. They use a
    careers subdomain, or a separate careers domain entirely, so those are
    tried too.
    """
    t = target.strip().rstrip("/")
    if t.startswith("http"):
        return [t]

    if "." in t and " " not in t:
        host = t.lstrip("/")
        root = host.replace("www.", "")
        bare = root.split(".")[0]
        tld = ".".join(root.split(".")[1:]) or "com"
        bases = [
            f"https://{root}",
            # www is not redundant. Plenty of employers serve nothing on the
            # apex domain, so dropping it loses them outright: the Bank of
            # England answers on www and not without it.
            f"https://www.{root}",
            f"https://careers.{root}",
            f"https://jobs.{root}",
            f"https://{bare}.careers",
            f"https://careers.{bare}.{tld}",
        ]
    else:
        slug = re.sub(r"[^a-z0-9]", "", t.lower())
        bases = [f"https://{slug}.com", f"https://careers.{slug}.com",
                 f"https://jobs.{slug}.com"]

    out: list[str] = []
    for b in bases:
        out.append(b)
        out.extend(b + p for p in CAREERS_PATHS)

    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:40]


def _scan(text: str, final_url: str) -> list[tuple[str, str, str]]:
    """Returns [(platform, token, api_url)] found in a page or its redirect."""
    blob = f"{final_url}\n{text[:400_000]}"
    hits: list[tuple[str, str, str]] = []

    for m in WORKDAY_RE.finditer(blob):
        tenant, wd, site = m.group(1), m.group(2), m.group(3)
        if site.lower() in ("login", "home"):
            continue
        api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        hits.append(("workday", f"{tenant}/{site}", api))

    # Phenom does not expose a token at all; the careers host itself is the
    # source, and it is recognisable from the assets it loads. The URL is built
    # by the adapter rather than spelled out here, because it was spelled out
    # in three places and `enumerate_boards` had a fourth copy of the same idea
    # for Workday that had already drifted.
    if re.search(r"phenompeople|phApp\.ddo", blob, re.I):
        host = urlparse(final_url).netloc
        if host:
            ph = adapters.by_name("phenom")
            hits.append(("phenom", host, ph.build(host)))

    for platform, pat in SIGNATURES:
        for m in re.finditer(pat, blob, re.I):
            # A signature with more than one group is a composite token, joined
            # with "|" exactly as `adapters.build_avature` and the harvester's
            # columnar extractor spell it. The junk test stays on the FIRST
            # group only: the rest are path segments in the vendor's namespace,
            # and `_JUNK_TOKENS` contains "careers", which is what Tesco Bank's
            # Avature site is actually called.
            tok = "|".join(g or "" for g in m.groups())
            head = m.group(1) or ""
            if not head or head.lower() in _JUNK_TOKENS or len(head) < 2:
                continue
            p = adapters.by_name(platform)
            if not p or not p.build:
                continue
            hits.append((platform, tok, p.build(tok)))

    seen, out = set(), []
    for h in hits:
        if h[2] in seen:
            continue
        seen.add(h[2])
        out.append(h)
    return out


# Platforms that cannot answer at all without a credential. `validate` carries
# none, so a failure from one of these is a fact about `validate`, not about
# the source, and `validate --prune` deletes what looks dead.
KEYED_PLATFORMS = {"reed", "adzuna"}


def count_jobs(src: Source, timeout: int = 25) -> tuple[int, list, str | None]:
    """Fetch and parse. Job count is the only reliable liveness signal:
    several of these platforms answer 200 with an empty array for tokens that
    do not exist, so status codes prove nothing.

    The third value separates "this board has no postings" from "we could not
    read it". They used to be the same answer, and the difference matters
    every time: a 429 from a busy platform was being reported as a dead board,
    and `validate --prune` would then delete a real employer. Callers that
    only want the count can ignore it, but nothing may treat it as zero.
    """
    from .fetch import fetch_one, fetch_taleo
    if src.platform == "taleo":
        # Taleo's board URL is a JavaScript shell: a plain GET of it returns a
        # page with no job rows in it at all, on every live board checked. So
        # `fetch_one` would report every Taleo source as zero jobs, `validate`
        # would call that dead, and `validate --prune` deletes dead sources.
        # One page is enough to answer "is this board alive".
        res = fetch_taleo(src, [], timeout=timeout, retries=1, user_agent=UA,
                          max_pages=1)
    else:
        res = fetch_one(src, timeout=timeout, retries=1, user_agent=UA)
    if not res.ok:
        why = res.error or (f"HTTP {res.status}" if res.status else "no answer")
        if res.status in (429, 503) or res.throttled:
            why = f"rate limited ({why})"
        elif res.status in (400, 401, 403) and src.platform in KEYED_PLATFORMS:
            # `validate` does not carry credentials, so it cannot speak to
            # these at all. Reporting a bare "HTTP 401" here reads as a broken
            # source and would have anyone with a perfectly good key in their
            # config hunting a fault that is not there. Adzuna is the reason
            # 400 is in that list as well as 401: an unkeyed Adzuna request is
            # a 400 with an HTML error page, not a 401.
            why = (f"needs an API key, which `validate` does not send; "
                   f"this says nothing about whether {src.platform.title()} "
                   f"is working")
        return 0, [], why
    jobs = adapters.parse(res.payload, src)
    return len(jobs), jobs, None


def _norm(s: str) -> str:
    """Lowercase alphanumerics only, so 'Checkout.com' and 'checkout.com' and
    "Sotheby's" and 'sothebys' compare equal."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def verify_identity(jobs: list, domain: str | None, company: str,
                    platform: str = "") -> tuple[str, str]:
    """Does this board actually belong to who we think?

    Compares the board's apply links and its own company name against the
    domain asked for. Returns (verdict, note). Being unable to tell is
    reported as `unchecked`, never as `ok`, because a confident wrong answer
    here is how a Florida schools operator gets filed as a payments company.
    """
    # Aggregated search endpoints return many employers by design, so there is
    # no single identity to check against.
    if platform == "linkedin":
        return "unchecked", "aggregated search results, many employers"

    if not jobs:
        return "unchecked", "no postings to check against"
    apply_hosts = {urlparse(j.url).netloc.lower() for j in jobs if j.url}
    names = {(j.company or "").strip() for j in jobs if j.company}
    norm_names = {_norm(n) for n in names if n}

    if domain:
        d = domain.lower().replace("www.", "")
        root = _norm(d.split(".")[0])
        if root and any(root in _norm(h) for h in apply_hosts):
            return "ok", f"apply links point at {d}"
        if root and any(root in n or n in root for n in norm_names if n):
            return "ok", f"board names itself {sorted(names)[0]!r}"

    want = _norm(company)
    if want and any(want in n or n in want for n in norm_names if n):
        return "ok", f"board names itself {sorted(names)[0]!r}"
    if names:
        return "mismatch", f"board names itself {sorted(names)[0]!r}, expected {company!r}"
    return "unchecked", "board publishes no company name"


def _guess_tokens(domain: str | None, target: str) -> list[tuple[str, str, str]]:
    """Obvious token spellings, built into API URLs for the platforms that
    accept a plain token. Every result is still verified by the caller.
    """
    base = (domain or target).lower().replace("www.", "")
    root = base.split(".")[0]
    tld_form = base if "." in base else ""
    candidates = {root, root.replace("-", ""), root.replace("-", "")}
    if tld_form:
        candidates.add(tld_form)          # Primer's real Ashby token is "primer.io"
        candidates.add(tld_form.replace(".", ""))
    candidates = {c for c in candidates if c and len(c) > 1}

    out: list[tuple[str, str, str]] = []
    for name in ("greenhouse", "ashby", "lever"):
        p = adapters.by_name(name)
        if not p or not p.build:
            continue
        for tok in sorted(candidates):
            out.append((name, tok, p.build(tok)))
    return out


def discover(target: str, company: str | None = None, *, validate: bool = True) -> list[Found]:
    """Resolve a company or careers URL to job boards."""
    found: list[Found] = []
    domain = None
    if target.startswith("http") or "." in target:
        domain = urlparse(target if target.startswith("http") else f"https://{target}").netloc
    name = company or (domain.split(".")[0] if domain else target).replace("-", " ").title()

    # If the thing being asked about IS one of the platforms we cannot read,
    # say so and stop. Guessing tokens from the domain label answered
    # `civilservicejobs.service.gov.uk` with a real Greenhouse board named
    # "Civil Service Jobs" (it is one department's, holding a single posting),
    # marked it verified, and left a user believing the whole civil service
    # was covered. A platform domain is never an employer.
    platform_hit = detect_unsupported("", target)
    if platform_hit:
        return [Found(company=name, url=target if target.startswith("http")
                      else f"https://{target}", platform="", token="",
                      domain=domain, identity="unsupported",
                      note=f"{platform_hit} is a job platform, not an employer, "
                           f"and job-radar cannot read it yet. Searching it by "
                           f"hand is the only option for now.")]

    # Candidates are independent, so fetch them together rather than walking a
    # list of 30 URLs at 10 seconds each. First page that reveals an ATS wins.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cands = _candidates(target)
    hits: list[tuple[str, str, str]] = []
    blocked = 0
    unsupported = ""          # a platform we recognised but cannot read yet
    unsupported_url = ""
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_get, c, 10): c for c in cands}
        try:
            for fut in as_completed(futs, timeout=75):
                r = fut.result()
                if _is_blocked(r):
                    blocked += 1
                    continue
                if r is None or r.status_code >= 400:
                    continue
                got = _scan(r.text, r.url)
                if got:
                    hits.extend(got)
                    break
                if not unsupported:
                    plat = detect_unsupported(r.text, r.url)
                    if plat:
                        unsupported, unsupported_url = plat, r.url
        except TimeoutError:
            pass
        for f2 in futs:
            f2.cancel()

    # If the careers page could not be read, fall back to trying the obvious
    # tokens directly. Guessing used to be unsafe, because a token that
    # responds is not proof of the right company. It is safe here only because
    # every hit is identity-checked before it is offered, so the Florida
    # schools operator sitting on Ashby `primer` gets caught rather than filed.
    # It does not work for Workday at all: tenant and site names are not
    # derivable from a company name, and 117 attempts proved it.
    if not hits:
        hits.extend(_guess_tokens(domain, target))

    seen_api, checked = set(), []
    for platform, token, api in hits:
        if api in seen_api:
            continue
        seen_api.add(api)
        f = Found(company=name, url=api, platform=platform, token=token, domain=domain)
        if validate:
            n, jobs, err = count_jobs(f.to_source())
            f.live_jobs = n
            if err:
                f.note = f"could not be read: {err}"
            elif n == 0:
                f.note = "responded but published no postings"
            else:
                f.identity, f.note = verify_identity(jobs, domain, name, f.platform)
        checked.append(f)

    # Empty boards are noise when they came from guessing rather than from a
    # link the company actually published.
    found = [f for f in checked if f.live_jobs > 0 or not validate]

    if not found:
        if unsupported:
            # Naming the platform turns an identical shrug into a diagnosis,
            # and tells the maintainer which adapter to write next from real
            # runs rather than guesswork.
            return [Found(company=name, url=unsupported_url, platform="",
                          token="", domain=domain, identity="unsupported",
                          note=f"{unsupported}, which job-radar cannot read yet. "
                               f"The board is at {unsupported_url} and is worth "
                               f"a bookmark. Adapter requests welcome.")]
        if blocked:
            return [Found(company=name, url="", platform="", token="", domain=domain,
                          identity="blocked",
                          note=f"careers site refused automated requests "
                               f"({blocked} of {len(cands)} URLs blocked), and no "
                               f"job board was found by name. Apply through their "
                               f"site directly.")]
        return []

    found.sort(key=lambda f: (-f.live_jobs, f.identity != "ok"))
    return found


# A neutral word to probe a keyword search with. It has to return something
# on any real job board without being specific to one field.
PROBE_KEYWORD = "manager"


def validate_source(src: Source) -> dict:
    """Health check for one already-known source. Used by `validate` and by
    the weekly maintenance workflow.

    Keyword searches (NHS Jobs, LinkedIn) ship as templates and are expanded
    per title at scan time. Fetching the unexpanded URL asks the board for
    postings matching the literal string "{keyword}", which returns nothing,
    which read as dead: the weekly prune was scheduled to delete NHS Jobs, a
    live source and the only direct one that works outside technology. So
    probe them with a real word instead, and skip the identity check, which
    asks "is this board really this employer's" and is meaningless for an
    aggregator that returns every employer.
    """
    if src.keyword_template or "{keyword}" in src.url:
        from urllib.parse import quote_plus
        probe = replace(src, url=src.url.format(keyword=quote_plus(PROBE_KEYWORD)),
                        keyword_template=False)
        n, _, err = count_jobs(probe)
        return {
            "company": src.company,
            "url": src.url,
            "platform": src.platform,
            "live_jobs": n,
            "verdict": "unreachable" if err else ("dead" if n == 0 else "live"),
            "note": f"could not be read: {err}" if err else
                    ("keyword search, probed with "
                     f"'{PROBE_KEYWORD}'; identity not checked"),
        }

    n, jobs, err = count_jobs(src)
    if err:
        # Not a verdict on the board. Something between here and it failed,
        # and calling that dead is how a live employer gets pruned.
        verdict, note = "unreachable", f"could not be read: {err}"
    elif n == 0:
        verdict, note = "dead", "no postings returned"
    else:
        verdict, note = verify_identity(jobs, src.domain, src.company,
                                        src.platform)
    return {
        "company": src.company,
        "url": src.url,
        "platform": src.platform,
        "live_jobs": n,
        "verdict": verdict,
        "note": note,
    }
