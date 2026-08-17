"""Filtering and scoring.

Two stages, deliberately separate:

  * `match` decides whether a posting is even the kind of job you want, on
    title and location. Cheap, runs on everything.
  * `screen` reads the description for your dealbreakers. This is the part
    that catches the roles that look right in a search result and are wrong
    in the detail.

Nothing here is a black box. Every kept role carries the reasons it scored
what it did, and every dropped one carries why.
"""

from __future__ import annotations

import re

from .config import Config
from .models import Job
from .salary import clears_floor

# Locations that read as "somewhere else" unless the user says otherwise.
_COUNTRY_HINTS = {
    # The postcode pattern matters more than the city list. Employers who
    # advertise nationally list towns, not cities: NHS Jobs gives locations
    # like "Dorchester DT1 2JY", and a city-only regex reads every one of them
    # as an unrecognised country and drops the role.
    "UK": r"united kingdom|\buk\b|england|scotland|wales|northern ireland|"
          r"london|manchester|bristol|birmingham|leeds|edinburgh|glasgow|"
          r"cambridge|oxford|reading|milton keynes|cardiff|belfast|liverpool|"
          r"newcastle|sheffield|nottingham|southampton|brighton|york|bath|"
          # Lowercase on purpose: _country_of lowercases before matching.
          r"\b[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}\b",
    "US": r"united states|\busa?\b|new york|san francisco|seattle|austin|boston|chicago|"
          r"los angeles|denver|atlanta",
    "IE": r"\bireland\b|dublin",
    "DE": r"\bgermany\b|berlin|munich|hamburg",
    "FR": r"\bfrance\b|paris",
    "ES": r"\bspain\b|madrid|barcelona",
    "NL": r"netherlands|amsterdam",
    "CA": r"\bcanada\b|toronto|vancouver|montreal",
    "AU": r"\baustralia\b|sydney|melbourne",
    "AE": r"\buae\b|dubai|abu dhabi",
    "NZ": r"new zealand|auckland|wellington",
    "SG": r"\bsingapore\b",
    "HK": r"hong kong",
    "IN": r"\bindia\b|bangalore|bengaluru|hyderabad|mumbai|pune|gurgaon|noida",
    "JP": r"\bjapan\b|tokyo",
    "CN": r"\bchina\b|beijing|shanghai|shenzhen",
    "PL": r"\bpoland\b|warsaw|krakow|wroclaw",
    "PT": r"\bportugal\b|lisbon|porto",
    "SE": r"\bsweden\b|stockholm",
    "CH": r"switzerland|zurich|geneva",
    "IL": r"\bisrael\b|tel aviv",
    "BR": r"\bbrazil\b|sao paulo",
    "MX": r"\bmexico\b",
    "ZA": r"south africa|cape town|johannesburg",
    "ID": r"\bindonesia\b|jakarta",
    "TH": r"\bthailand\b|bangkok",
    "MY": r"\bmalaysia\b|kuala lumpur",
    "PH": r"philippines|manila",
    "IT": r"\bitaly\b|milan|rome",
    "BE": r"\bbelgium\b|brussels",
    "AT": r"\baustria\b|vienna",
    "DK": r"\bdenmark\b|copenhagen",
    "NO": r"\bnorway\b|oslo",
    "FI": r"\bfinland\b|helsinki",
    "CZ": r"czech|prague",
    "RO": r"\bromania\b|bucharest",
    "TR": r"\bturkey\b|istanbul",
    "AR": r"\bargentina\b|buenos aires",
}

# "Remote" with nothing else attached. Anything more specific than this names
# a place, and a place has to clear the country filter even when it is remote:
# a US-remote role is not open to someone in the UK.
_GENERIC_REMOTE = re.compile(
    r"^\s*(?:fully\s+|100%\s+)?(?:remote|anywhere|global(?:ly)?|worldwide|distributed)"
    r"[\s,\-/]*$",
    re.I,
)

_SPLIT = re.compile(r"[;,/|]| or | and |\bor\b")


def _country_of(location: str) -> str | None:
    l = (location or "").lower()
    for code, pat in _COUNTRY_HINTS.items():
        if re.search(pat, l):
            return code
    return None


def _countries_in(location: str) -> set[str]:
    """Every country a posting names. Postings routinely list several."""
    found = set()
    for part in _SPLIT.split(location or ""):
        c = _country_of(part)
        if c:
            found.add(c)
    if not found:
        c = _country_of(location)
        if c:
            found.add(c)
    return found


def match(job: Job, cfg: Config) -> tuple[bool, str]:
    """Title and location gate. Returns (keep, reason_if_dropped)."""
    inc, exc = cfg.title_include_re(), cfg.title_exclude_re()
    title = job.title or ""

    if exc and exc.search(title):
        return False, "title excluded"
    if inc and not inc.search(title):
        return False, "title does not match"

    loc = (job.location or "").strip()
    allowed = set(cfg.countries) | set(cfg.relocate_to)

    loc_exc = cfg.location_exclude_re()
    if loc_exc and loc and loc_exc.search(loc):
        # A posting listing several locations survives if one of them is wanted.
        if not (_countries_in(loc) & allowed):
            return False, f"location excluded ({loc})"

    if allowed:
        # "Remote" on its own means the employer has not named a country, so
        # take them at their word. "Remote - US" has named one, and being
        # remote does not make a US role open to someone outside the US.
        generic = not loc or bool(_GENERIC_REMOTE.match(loc))
        if generic:
            if not cfg.remote_ok and not loc:
                return False, "no location given and remote is off"
            return True, ""

        found = _countries_in(loc)
        if not found:
            return False, f"location not recognised ({loc})"
        if not (found & allowed):
            return False, f"{loc} outside target countries"

    return True, ""


def screen(job: Job, cfg: Config) -> tuple[bool, list[str]]:
    """Dealbreaker scan over the description. Returns (keep, hits)."""
    if not job.description:
        if job.platform == "linkedin":
            job.flags.append("not screened: no description from this source")
        return True, []

    hits = []
    for db in cfg.dealbreakers:
        if db.compiled().search(job.description):
            hits.append(db.name)
            if not db.hard:
                job.flags.append(f"soft flag: {db.name}")

    hard = [
        db.name for db in cfg.dealbreakers
        if db.hard and db.compiled().search(job.description)
    ]
    return (not hard), hits


def apply_salary(job: Job, cfg: Config) -> tuple[bool, str]:
    keep, why = clears_floor(job.salary, cfg.salary_floor, cfg.salary_currency)
    if keep and why:
        job.flags.append(why)
    return keep, why


def score(job: Job, cfg: Config) -> float:
    """A transparent 0-100 score. Explanations land in `job.reasons`."""
    s, why = 0.0, []

    inc = cfg.title_include_re()
    if inc and inc.search(job.title):
        s += 35
        why.append("title matches your targets")

    found = _countries_in(job.location)
    home = found & set(cfg.countries)
    if home:
        s += 20
        why.append("remote in " + ", ".join(sorted(home)) if job.remote
                   else "in " + ", ".join(sorted(home)))
    elif job.remote and not found:
        s += 20
        why.append("remote, no country named")
    elif found & set(cfg.relocate_to):
        s += 8
        why.append("in " + ", ".join(sorted(found & set(cfg.relocate_to))) + ", relocation")

    if job.salary.confirmed:
        s += 10
        why.append(f"pay stated ({job.salary.raw})")
        top = job.salary.annualised()
        if top and cfg.salary_floor and top >= cfg.salary_floor * 1.15:
            s += 10
            why.append("comfortably above your floor")
    else:
        why.append("unconfirmed salary")

    if job.posted_at:
        from datetime import date
        try:
            age = (date.today() - date.fromisoformat(job.posted_at)).days
            if age <= 7:
                s += 15
                why.append("posted this week")
            elif age <= 21:
                s += 8
                why.append(f"posted {age} days ago")
        except ValueError:
            pass

    if not job.flags:
        s += 10

    job.score = round(min(s, 100.0), 1)
    job.reasons = why
    return job.score


def dedupe(jobs: list[Job], cfg: Config | None = None) -> list[Job]:
    """Collapse the same role posted once per location.

    Several ATSs publish one posting per office, so a single job appears six
    times with six URLs. Merging them on company+title and joining the
    locations turns six rows back into the one job it actually is.
    """
    groups: dict[tuple[str, str], list[Job]] = {}
    for j in jobs:
        groups.setdefault((j.company.strip().lower(), j.title.strip().lower()), []).append(j)

    out: list[Job] = []
    for members in groups.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        best = max(members, key=lambda x: (x.salary.confirmed, len(x.description or "")))
        locs, seen_loc = [], set()
        for m in members:
            l = (m.location or "").strip()
            if l and l.lower() not in seen_loc:
                seen_loc.add(l.lower())
                locs.append(l)
        # Show the locations the reader can actually take first. A role open in
        # twenty countries should not lead with the nineteen that are no use.
        if cfg:
            wanted = set(cfg.countries) | set(cfg.relocate_to)
            locs.sort(key=lambda l: 0 if (_countries_in(l) & wanted) else 1)
        shown = locs[:6]
        best.location = " / ".join(shown) + (f" +{len(locs) - len(shown)} more"
                                             if len(locs) > len(shown) else "")
        best.flags.append(f"posted in {len(members)} locations")
        out.append(best)
    return out


def run(jobs: list[Job], cfg: Config) -> tuple[list[Job], dict[str, int]]:
    """Full pipeline. Returns (kept, counts_by_drop_reason)."""
    jobs = dedupe(jobs, cfg)
    kept: list[Job] = []
    dropped: dict[str, int] = {}

    def drop(reason: str):
        key = re.sub(r"\(.*?\)", "", reason).strip() or reason
        dropped[key] = dropped.get(key, 0) + 1

    for j in jobs:
        ok, why = match(j, cfg)
        if not ok:
            drop(why)
            continue
        ok, why = apply_salary(j, cfg)
        if not ok:
            drop("stated pay below floor")
            continue
        ok, hits = screen(j, cfg)
        if not ok:
            drop(f"dealbreaker: {', '.join(hits)}")
            continue
        score(j, cfg)
        kept.append(j)

    kept.sort(key=lambda x: (-x.score, x.company.lower()))
    return kept, dropped
