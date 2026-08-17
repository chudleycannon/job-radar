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
from dataclasses import dataclass
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
    ("lever", r"jobs\.lever\.co/([a-z0-9_.-]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9_.-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_.-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
]

# Workday needs two captures (tenant, site) and its own URL shape.
WORKDAY_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([a-zA-Z0-9_-]+)",
    re.I,
)

_JUNK_TOKENS = {"embed", "job_board", "v1", "boards", "jobs", "api", "www", "search"}


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


def _get(url: str, timeout: int = 20) -> requests.Response | None:
    try:
        return requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": UA,
                                     "Accept": "text/html,application/json"})
    except requests.RequestException:
        return None


def _candidates(target: str) -> list[str]:
    """Careers URLs to try, in order of likelihood."""
    t = target.strip()
    if t.startswith("http"):
        return [t]
    if "." in t and " " not in t:
        base = f"https://{t.lstrip('/')}" if not t.startswith("http") else t
        return [base.rstrip("/") + p for p in CAREERS_PATHS] + [base]
    # A bare company name. Guessing a domain is weak, so try the obvious one
    # and let the caller supply a URL if it misses.
    slug = re.sub(r"[^a-z0-9]", "", t.lower())
    return [f"https://{slug}.com{p}" for p in CAREERS_PATHS[:4]]


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

    for platform, pat in SIGNATURES:
        for m in re.finditer(pat, blob, re.I):
            tok = m.group(1)
            if not tok or tok.lower() in _JUNK_TOKENS or len(tok) < 2:
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


def count_jobs(src: Source, timeout: int = 25) -> tuple[int, list]:
    """Fetch and parse. Job count is the only reliable liveness signal:
    several of these platforms answer 200 with an empty array for tokens that
    do not exist, so status codes prove nothing.
    """
    from .fetch import fetch_one
    res = fetch_one(src, timeout=timeout, retries=1, user_agent=UA)
    if not res.ok:
        return 0, []
    jobs = adapters.parse(res.payload, src)
    return len(jobs), jobs


def verify_identity(jobs: list, domain: str | None, company: str) -> tuple[str, str]:
    """Does this board actually belong to who we think?

    Checks the apply URLs and the board's own company name against the domain.
    Returns (verdict, note). Being unable to tell is reported as `unchecked`,
    never as `ok`.
    """
    if not jobs:
        return "unchecked", "no postings to check against"
    apply_hosts = {urlparse(j.url).netloc.lower() for j in jobs if j.url}
    names = {(j.company or "").lower() for j in jobs if j.company}

    if domain:
        d = domain.lower().replace("www.", "")
        root = d.split(".")[0]
        if any(d in h or root in h for h in apply_hosts):
            return "ok", f"apply links point at {d}"
        if any(root in n.replace(" ", "") for n in names):
            return "ok", f"board names itself {sorted(names)[0]!r}"

    root = re.sub(r"[^a-z0-9]", "", company.lower())
    if root and any(root in n.replace(" ", "") for n in names):
        return "ok", f"board names itself {sorted(names)[0]!r}"
    if names:
        return "mismatch", f"board names itself {sorted(names)[0]!r}, expected {company!r}"
    return "unchecked", "board publishes no company name"


def discover(target: str, company: str | None = None, *, validate: bool = True) -> list[Found]:
    """Resolve a company or careers URL to job boards."""
    found: list[Found] = []
    domain = None
    if target.startswith("http") or "." in target:
        domain = urlparse(target if target.startswith("http") else f"https://{target}").netloc
    name = company or (domain.split(".")[0] if domain else target).replace("-", " ").title()

    for cand in _candidates(target):
        r = _get(cand)
        if r is None or r.status_code >= 400:
            continue
        for platform, token, api in _scan(r.text, r.url):
            f = Found(company=name, url=api, platform=platform, token=token, domain=domain)
            if validate:
                src = f.to_source()
                n, jobs = count_jobs(src)
                f.live_jobs = n
                if n == 0:
                    f.note = "responded but published no postings"
                else:
                    f.identity, f.note = verify_identity(jobs, domain, name)
            found.append(f)
        if found:
            break

    found.sort(key=lambda f: (-f.live_jobs, f.identity != "ok"))
    return found


def validate_source(src: Source) -> dict:
    """Health check for one already-known source. Used by `validate` and by
    the weekly maintenance workflow.
    """
    n, jobs = count_jobs(src)
    verdict, note = ("dead", "no postings returned") if n == 0 else \
        verify_identity(jobs, src.domain, src.company)
    return {
        "company": src.company,
        "url": src.url,
        "platform": src.platform,
        "live_jobs": n,
        "verdict": verdict,
        "note": note,
    }
