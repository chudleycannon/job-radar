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

# Working out which country a posting is in is the single most load-bearing
# thing in this file, and it is harder than it looks, because city names are
# not unique across countries. "New York City" contains "york"; there is a
# Cambridge in Massachusetts, a Birmingham in Alabama, a Manchester in New
# Hampshire and a Newcastle in Australia. Matching city names against a flat
# list and taking the first hit marked 59 of 296 American roles as British.
#
# So the signals are tiered. An explicit country name always beats a city
# name, and a US state code beats both, because ", NY" is unambiguous in a way
# that "york" never is.

# Tier 1: the location says which country outright.
_COUNTRY_MARKERS = {
    "UK": r"united kingdom|\buk\b|\bg\.?b\.?\b|\bengland\b|\bscotland\b|\bwales\b|"
          r"northern ireland|\bbritain\b",
    "US": r"united states|\bu\.?s\.?a\.?\b|\bus\b|\bamericas?\b",
    "IE": r"\bireland\b(?!,? *north)",
    "DE": r"\bgermany\b", "FR": r"\bfrance\b", "ES": r"\bspain\b",
    "NL": r"netherlands", "CA": r"\bcanada\b", "AU": r"\baustralia\b",
    "NZ": r"new zealand", "AE": r"\buae\b|united arab emirates",
    "SG": r"\bsingapore\b", "HK": r"hong kong", "IN": r"\bindia\b",
    "JP": r"\bjapan\b", "CN": r"\bchina\b", "PL": r"\bpoland\b",
    "PT": r"\bportugal\b", "SE": r"\bsweden\b", "CH": r"switzerland",
    "IL": r"\bisrael\b", "BR": r"\bbrazil\b", "MX": r"\bmexico\b",
    "ZA": r"south africa", "ID": r"\bindonesia\b", "TH": r"\bthailand\b",
    "MY": r"\bmalaysia\b", "PH": r"philippines", "IT": r"\bitaly\b",
    "BE": r"\bbelgium\b", "AT": r"\baustria\b", "DK": r"\bdenmark\b",
    "NO": r"\bnorway\b", "FI": r"\bfinland\b", "CZ": r"czech",
    "RO": r"\bromania\b", "TR": r"\bturkey\b", "AR": r"\bargentina\b",
    "VN": r"\bvietnam\b", "KR": r"south korea",
}

# Tier 2: a US state code after a comma ("San Francisco, CA"). Case-sensitive
# on purpose, so it cannot fire on the word "ca" inside ordinary prose.
_US_STATE = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|"
    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    r"WA|WV|WI|WY|DC)\b"
)

# Tier 3: city names, consulted only when nothing above fired. UK entries that
# collide with a bigger foreign city are guarded rather than dropped, since
# "London" and "Manchester" are still the common case in a UK-facing tool.
_CITY_HINTS = {
    "UK": r"\blondon\b(?!,? *(?:ontario|ky|oh))|\bmanchester\b|\bbristol\b|"
          r"\bbirmingham\b|\bleeds\b|\bedinburgh\b|\bglasgow\b|"
          r"\bcambridge\b|\boxford\b|\breading\b|milton keynes|\bcardiff\b|"
          r"\bbelfast\b|\bliverpool\b|\bnewcastle\b|\bsheffield\b|"
          r"\bnottingham\b|\bsouthampton\b|\bbrighton\b|(?<!new )\byork\b|"
          r"\bbath\b|\bleicester\b|\bcoventry\b|\bderby\b|\bswindon\b|"
          r"\bipswich\b|\bnorwich\b|\bexeter\b|\bplymouth\b|"
          # A UK postcode is a strong signal on its own: employers hiring
          # nationally list towns, not cities.
          r"\b[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}\b",
    "US": r"san francisco|new york|seattle|austin|boston|chicago|los angeles|"
          r"denver|atlanta|palo alto|mountain view|menlo park|san jose|"
          r"washington,? d\.?c|bellevue|redmond|sunnyvale",
    "IE": r"\bdublin\b(?!,? *(?:oh|ca))", "DE": r"\bberlin\b|munich|hamburg|cologne",
    "FR": r"\bparis\b(?!,? *(?:tx|tn))", "ES": r"\bmadrid\b|barcelona",
    "NL": r"amsterdam|rotterdam|utrecht", "CA": r"\btoronto\b|vancouver|montreal|ottawa",
    "AU": r"\bsydney\b|melbourne|brisbane|perth", "NZ": r"auckland|wellington",
    "AE": r"\bdubai\b|abu dhabi", "IN": r"bangalore|bengaluru|hyderabad|mumbai|pune|gurgaon|noida",
    "JP": r"\btokyo\b", "CN": r"beijing|shanghai|shenzhen", "PL": r"warsaw|krakow|wroclaw",
    "PT": r"\blisbon\b|\bporto\b", "SE": r"stockholm", "CH": r"zurich|geneva",
    "IL": r"tel aviv", "BR": r"sao paulo", "ZA": r"cape town|johannesburg",
    "ID": r"jakarta", "TH": r"bangkok", "MY": r"kuala lumpur", "PH": r"manila",
    "IT": r"\bmilan\b|\brome\b", "BE": r"brussels", "AT": r"\bvienna\b",
    "DK": r"copenhagen", "NO": r"\boslo\b", "FI": r"helsinki", "CZ": r"prague",
    "RO": r"bucharest", "TR": r"istanbul", "AR": r"buenos aires", "SG": r"\bsingapore\b",
    "HK": r"hong kong", "KR": r"\bseoul\b", "VN": r"hanoi|ho chi minh",
}

# "Remote" with nothing else attached. Anything more specific than this names
# a place, and a place has to clear the country filter even when it is remote:
# a US-remote role is not open to someone in the UK.
_GENERIC_REMOTE = re.compile(
    r"^\s*(?:fully\s+|100%\s+)?(?:remote|anywhere|global(?:ly)?|worldwide|distributed)"
    r"[\s,\-/]*$",
    re.I,
)

# Deliberately NOT splitting on commas. A comma binds a place to its
# qualifier ("Cambridge, MA"), and splitting there throws away the state code
# that identifies the country. Postings separate genuinely distinct locations
# with a pipe or a slash.
_SPLIT = re.compile(r"[;|/]| or |\bor\b")


def _country_of(location: str) -> str | None:
    """Best single guess at the country a location string refers to.

    Tiered deliberately: an explicit country name beats a US state code beats
    a city name. Returns None when nothing identifies it, which callers treat
    as unknown rather than as a match.
    """
    if not location:
        return None
    raw = location
    low = raw.lower()

    for code, pat in _COUNTRY_MARKERS.items():
        if re.search(pat, low):
            return code
    if _US_STATE.search(raw):
        return "US"
    for code, pat in _CITY_HINTS.items():
        if re.search(pat, low):
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


# Whether a role is remote, hybrid or office-based is rarely a field. Ashby
# and Workable expose it; the rest bury it in prose or omit it. So this reads
# the flag where there is one and the text where there is not, and reports
# "unstated" rather than guessing, which is over half of postings.
_HYBRID = re.compile(
    r"\bhybrid\b|\d\s*days?\s*(?:a week\s*)?(?:in|per week in)\s*(?:the\s*)?office|"
    r"\b\d+\s*days? on[- ]?site", re.I)
_ONSITE = re.compile(
    r"\bon[- ]?site\b|\bin[- ]person\b|\boffice[- ]based\b|100% in office|"
    r"\bfull[- ]?time in the office\b", re.I)
_REMOTE_TXT = re.compile(
    r"\bfully remote\b|\b100% remote\b|\bremote[- ]first\b|\bwork from anywhere\b|"
    r"\bremote\b", re.I)

# Bits that are not a city: countries, regions and the wrapper words postings
# put in front of a place.
_NOT_A_CITY = re.compile(
    r"^(?:remote|hybrid|on[- ]?site|anywhere|global|worldwide|europe|emea|americas?|apac|"
    r"united kingdom|uk|england|scotland|wales|northern ireland|united states|usa?|"
    r"canada|australia|ireland|germany|france|spain|netherlands|india|singapore|"
    r"various|multiple locations|flexible|tbc|n/?a)$", re.I)


def work_mode(job: Job) -> str:
    """remote | hybrid | office | unstated.

    Hybrid is checked first on purpose: a posting saying "remote/hybrid" is
    describing a hybrid job, and reading the word "remote" first would file it
    wrongly in the more attractive bucket.
    """
    blob = f"{job.title} {job.location} {(job.description or '')[:2500]}"
    if _HYBRID.search(blob):
        return "hybrid"
    if _ONSITE.search(blob):
        return "office"
    if job.remote is True or _REMOTE_TXT.search(blob):
        return "remote"
    return "unstated"


def city_of(location: str) -> str:
    """The town, where a posting names one. Empty when it does not."""
    if not location:
        return ""
    first = re.split(r"[|/]", location)[0]
    first = re.sub(r"^\s*(?:remote|hybrid|on[- ]?site)\s*[-–—:,]\s*", "", first, flags=re.I)
    part = first.split(",")[0].strip(" -–—")
    # Snowflake ship "US-CA-Menlo Park"; LinkedIn ship "London Area". Both are
    # the same city as everyone else's, so normalise rather than splitting the
    # filter into near-duplicates.
    part = re.sub(r"^[A-Z]{2}-[A-Z]{2}-", "", part)
    part = re.sub(r"\s+Area$", "", part, flags=re.I)
    part = re.sub(r"\s*\b[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}\b\s*$", "", part, flags=re.I)
    if not part or _NOT_A_CITY.match(part) or len(part) > 34:
        return ""
    return part.strip()


def enrich(job: Job) -> Job:
    """Fill the derived fields the dashboard filters on."""
    job.work_mode = work_mode(job)
    found = _countries_in(job.location)
    job.country = job.country or (sorted(found)[0] if len(found) == 1 else
                                  ("multiple" if found else None))
    job.city = city_of(job.location)
    return job


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
        enrich(j)
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
