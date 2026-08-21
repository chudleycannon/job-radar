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

# Spelled out, too. "Birmingham, Alabama" and "Cambridge, Massachusetts" were
# reading as UK, because only the two-letter codes were recognised and the
# city list is checked with UK first.
_US_STATE_NAME = re.compile(
    r",\s*(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    r"new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|"
    r"utah|vermont|virginia|washington|west virginia|wisconsin|wyoming)\b",
    re.I)

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
    "IE": r"\bdublin\b(?!,? *(?:oh|ca))|\bcork\b|galway|limerick", "DE": r"\bberlin\b|munich|m\u00fcnchen|hamburg|cologne|k\u00f6ln|frankfurt|stuttgart|d\u00fcsseldorf|dusseldorf|leipzig|n\u00fcrnberg|nuremberg",
    "FR": r"\bparis\b(?!,? *(?:tx|tn))|\blyon\b|marseille|toulouse|bordeaux|\bnantes\b|\blille\b", "ES": r"\bmadrid\b|barcelona|valencia|seville|sevilla|bilbao|malaga|m\u00e1laga|zaragoza",
    "NL": r"amsterdam|rotterdam|utrecht|eindhoven|the hague|den haag|groningen|delft", "CA": r"\btoronto\b|vancouver|montreal|ottawa",
    "AU": r"\bsydney\b|melbourne|brisbane|perth", "NZ": r"auckland|wellington",
    "AE": r"\bdubai\b|abu dhabi", "IN": r"bangalore|bengaluru|hyderabad|mumbai|pune|gurgaon|noida",
    "JP": r"\btokyo\b", "CN": r"beijing|shanghai|shenzhen", "PL": r"warsaw|warszawa|krakow|krak\u00f3w|wroclaw|wroc\u0142aw|gdansk|gda\u0144sk|poznan|\bl\u00f3dz\b",
    "PT": r"\blisbon\b|lisboa|\bporto\b|\boporto\b|coimbra|\bbraga\b|\bfaro\b|aveiro|funchal", "SE": r"stockholm|gothenburg|g\u00f6teborg|malm\u00f6|\bmalmo\b", "CH": r"zurich|z\u00fcrich|geneva|gen\u00e8ve|basel|lausanne|zug\b",
    "IL": r"tel aviv", "BR": r"sao paulo", "ZA": r"cape town|johannesburg",
    "ID": r"jakarta", "TH": r"bangkok", "MY": r"kuala lumpur", "PH": r"manila",
    "IT": r"\bmilan\b|milano|\brome\b|\broma\b|turin|torino|bologna|florence|firenze|naples", "BE": r"brussels|bruxelles|antwerp|\bghent\b|leuven", "AT": r"\bvienna\b|\bwien\b|\bgraz\b|salzburg",
    "DK": r"copenhagen|k\u00f8benhavn|aarhus|\bodense\b", "NO": r"\boslo\b|bergen|trondheim", "FI": r"helsinki|espoo|tampere", "CZ": r"prague|praha|\bbrno\b",
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
    if _US_STATE.search(raw) or _US_STATE_NAME.search(low):
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


# "Remote" with a country named somewhere in the body. Airbnb's "Senior Data
# Scientist" said `Remote` in the location field and "This position is US -
# Remote Eligible" in the description; the tool called it "remote, no country
# named", gave it 20 points for it, and ranked it second for a user who
# cannot work in the US.
_REMOTE_SCOPE = re.compile(
    r"(?:remote|based|located|work|eligible|hire|role is)[^.\n]{0,60}?"
    r"\b(?:in|within|from|across|to)\b[^.\n]{0,40}"
    r"|\b(?:uk|us|usa|united states|united kingdom|eu|europe|emea)\b[\s-]*"
    r"(?:only|based|remote|eligible)", re.I)


def remote_scope(job: Job) -> set[str]:
    """Countries a nominally-location-free remote role is actually limited to.

    Only consulted when the location field names nothing, and only over the
    first part of the description, where employers put this.
    """
    d = (job.description or "")[:3000]
    if not d:
        return set()
    found: set[str] = set()
    for m in _REMOTE_SCOPE.finditer(d):
        found |= _countries_in(m.group(0))
    return found


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
    rights = work_rights(job)
    if rights and rights not in job.flags:
        job.flags.append(rights)
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
        # Exclusion has to work per location, not against the whole string.
        # Asking whether any wanted COUNTRY appears meant "London" cancelled
        # its own exclusion for anyone with countries: [UK], so the single
        # most load-bearing filter a UK user writes did nothing at all.
        parts = [x.strip() for x in _SPLIT.split(loc) if x.strip()] or [loc]
        survivors = [x for x in parts if not loc_exc.search(x)]
        if not survivors:
            return False, f"location excluded ({loc})"
        # Judge the rest of the rules on what is left after the exclusion.
        loc = " / ".join(survivors)

    if not allowed and not cfg.remote_ok:
        # remote_ok lived entirely inside the country branch, so with no
        # countries set it was dead code and "no, I do not want remote roles"
        # changed nothing at all.
        if job.remote is True or _GENERIC_REMOTE.match(loc or ""):
            return False, "remote role and remote is off"

    if allowed:
        # "Remote" on its own means the employer has not named a country, so
        # take them at their word. "Remote - US" has named one, and being
        # remote does not make a US role open to someone outside the US.
        generic = not loc or bool(_GENERIC_REMOTE.match(loc))
        if generic:
            # Before taking "Remote" at face value, check whether the body
            # names a country. A role restricted to the US is a US role,
            # however the location field is written.
            scope = remote_scope(job)
            if scope and not (scope & allowed):
                return False, (f"remote but restricted to "
                               f"{', '.join(sorted(scope))}")
            # Answering "no" to "include fully remote roles" used to change
            # nothing: only a completely empty location was dropped, so every
            # posting that actually said "Remote" came through.
            if not cfg.remote_ok:
                return False, ("remote role and remote is off" if loc
                               else "no location given and remote is off")
            return True, ""
        if not cfg.remote_ok and job.remote is True:
            return False, "remote role and remote is off"

        found = _countries_in(loc)
        if not found:
            return False, (f"location not recognised ({loc})" if loc.strip()
                           else "no location given")
        if not (found & allowed):
            return False, f"{loc} outside target countries"

    return True, ""


# Whether the employer will sponsor a work visa is, for anyone without the
# right to work in the country, the single fact that decides the application.
# Nothing in the tool had any concept of it: a posting saying "we are unable
# to provide visa sponsorship" and one saying "we can sponsor visas" scored
# identically and looked identical, and the only place that noticed was a
# paid screen, one role at a time.
_NO_SPONSOR = re.compile(
    r"(?:unable|not able|cannot|can.t|do not|don.t|will not|won.t)\s+"
    r"(?:to\s+)?(?:provide|offer|support|sponsor)\w*\s*"
    r"(?:\w+\s+){0,3}?(?:visa|sponsorship|work permit)"
    r"|no\s+(?:visa\s+)?sponsorship"
    r"|without\s+(?:the\s+need\s+for\s+)?(?:visa\s+)?sponsorship"
    r"|sponsorship\s+is\s+not\s+(?:available|offered|provided)"
    r"|must\s+(?:already\s+)?have\s+(?:the\s+)?(?:full\s+)?rights?\s+to\s+work"
    r"|full\s+rights?\s+to\s+work", re.I)

# US export control. A separate barrier from a visa and it stacks on top of
# one: EAR and ITAR restrict releasing controlled technology to foreign
# persons, so a role can be perfectly willing to sponsor and still be closed.
# Datadog's posting carries this in its standard footer and says nothing at
# all about sponsorship, so reading only for the word "sponsor" reported a
# clean bill of health on a role with two immigration problems.
_EXPORT_CONTROL = re.compile(
    r"export control|\bITAR\b|\bEAR\b(?!\w)|"
    r"eligible for any required authoriz|"
    r"(?:must be|require[sd]?)\s+(?:a\s+)?(?:US|U\.S\.)\s+(?:person|citizen)|"
    r"security clearance|\bSC clearance\b|developed vetting|"
    r"\bDV cleared\b|baseline personnel security", re.I)

_WILL_SPONSOR = re.compile(
    r"(?:can|able to|will|do|happy to|willing to)\s+(?:\w+\s+){0,2}?sponsor"
    r"|(?:visa|sponsorship)\s+(?:support\s+)?(?:is\s+)?(?:available|offered|provided)"
    r"|we\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship"
    r"|relocation\s+and\s+visa", re.I)


def work_rights(job: Job) -> str:
    '''"no sponsorship", "sponsorship offered", or "" for not stated.

    Read from the description the tool has already downloaded. Reported, never
    used to drop a role: most people running this already have the right to
    work where they are looking, and for them it is noise rather than a filter.
    '''
    d = job.description or ""
    if not d:
        return ""
    if _NO_SPONSOR.search(d):
        return "no sponsorship"
    # Checked before the willing case: a role that sponsors and is also export
    # controlled is still one you may not be allowed to do, and reporting
    # "sponsorship offered" on it would be the more reassuring half of the
    # truth.
    if _EXPORT_CONTROL.search(d):
        return "export control or clearance"
    if _WILL_SPONSOR.search(d):
        return "sponsorship offered"
    return ""


def screen(job: Job, cfg: Config) -> tuple[bool, list[str]]:
    """Dealbreaker scan over the description. Returns (keep, hits)."""
    # Warn on a posting too thin to have been screened properly, but still run
    # the patterns over whatever text is there.
    #
    # Two separate faults. The flag was added only for LinkedIn, so a Workday
    # role whose enrichment failed passed every dealbreaker with no warning at
    # all. And the first attempt at fixing that skipped the patterns entirely
    # below a length threshold, which is worse: a thirty-character description
    # saying "take home exercise" contains the disqualifying sentence, and
    # refusing to read it is the same silent pass by another route.
    text = (job.description or "").strip()
    if len(text) < 200:
        job.flags.append("not screened: no description from this source")
    if not text:
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


# Seniority words, roughly ordered. Used to notice when a posting sits well
# above or below the level you asked for, which the score was blind to: a
# Principal role and a grade-I role got identical numbers because nothing in
# the calculation read the candidate at all.
_LEVELS = [
    (1, r"\b(?:junior|graduate|trainee|apprentice|entry.level|assistant)\b|\bI\b$"),
    (2, r"\b(?:analyst|associate|engineer|officer|advisor|coordinator|executive)\b"),
    (3, r"\b(?:senior|snr|specialist|lead(?!ership)|supervisor)\b"),
    (4, r"\b(?:manager|management)\b"),
    (5, r"\b(?:principal|staff|head of|senior manager|group manager)\b"),
    (6, r"\b(?:director|vp|vice president|chief|c-level|partner)\b"),
]


def seniority(title: str) -> int:
    """Rough level of a title, 0 when nothing identifies one.

    An explicit junior marker wins outright. Taking the maximum meant "Junior
    Data Engineer" scored as mid-level, because "engineer" outranks "junior".
    """
    title = title or ""
    if re.search(_LEVELS[0][1], title, re.I):
        return 1
    best = 0
    for lvl, pat in _LEVELS[1:]:
        if re.search(pat, title, re.I):
            best = max(best, lvl)
    return best


def _target_band(cfg: Config) -> tuple[int, int]:
    """The span of levels you asked for, not just the top of it.

    Taking the maximum meant listing one director title alongside a manager
    title marked every manager role as two levels too junior, which is the
    opposite of what listing both means.
    """
    levels = [l for l in (seniority(t) for t in cfg.titles_include) if l]
    return (min(levels), max(levels)) if levels else (0, 0)


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
        # The description may name one even when the location field does not.
        scope = remote_scope(job)
        if scope & set(cfg.countries):
            s += 20
            why.append("remote, body says " + ", ".join(sorted(scope)))
        elif scope:
            why.append("remote but restricted to " + ", ".join(sorted(scope)))
        else:
            s += 20
            why.append("remote, no country named")
    elif found & set(cfg.relocate_to):
        s += 8
        why.append("in " + ", ".join(sorted(found & set(cfg.relocate_to))) + ", relocation")

    if job.salary.confirmed:
        # Publishing a figure is worth points, but only fully when the figure
        # tells you something. A EUR floor cannot read a sterling number, and
        # a 13.50/hour NHS post was collecting the same transparency bonus as
        # a role paying twice the floor.
        comparable_cur = not (cfg.salary_currency and job.salary.currency
                              and job.salary.currency != cfg.salary_currency)
        s += 10 if comparable_cur else 4
        why.append(f"pay stated ({job.salary.raw})" if comparable_cur
                   else f"pay stated ({job.salary.raw}), not comparable to your floor")
        top = job.salary.annualised()
        # The same currency guard `clears_floor` applies. Without it the
        # filter refused to compare GBP against a EUR floor while the scorer
        # went ahead and awarded points for it, so one row said "not compared"
        # and the next said "comfortably above your floor" about the same pay.
        if top and cfg.salary_floor and comparable_cur and top >= cfg.salary_floor * 1.15:
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

    # A role two levels above what you asked for is not a better match for
    # being more senior. Without this the top of the list was whatever was
    # posted most recently, regardless of whether it was reachable.
    low, high = _target_band(cfg)
    lvl = seniority(job.title)
    if high and lvl:
        above, below = lvl - high, low - lvl
        if above >= 2:
            s -= 25
            why.append(f"reads {above} levels above your targets")
            job.flags.append("a stretch: this sits well above the titles you asked for")
        elif above == 1:
            s -= 8
            why.append("a level above your targets")
        elif below >= 2:
            s -= 15
            why.append(f"reads {below} levels below your targets")

    job.score = round(max(min(s, 100.0), 0.0), 1)
    job.reasons = why
    return job.score


# How direct a source is. A posting read from the employer's own applicant
# tracking system is the employer speaking; the same role on a keyword search
# is a copy, usually with no description, sometimes reposted by an agency. So
# when the same job arrives twice, keep the one closest to the employer.
def directness(platform: str) -> int:
    return {"linkedin": 0, "nhs": 1}.get((platform or "").lower(), 2)


def dedupe(jobs: list[Job], cfg: Config | None = None) -> list[Job]:
    """Collapse the same role posted once per location, or once per source.

    Several ATSs publish one posting per office, so a single job appears six
    times with six URLs. Merging them on company+title and joining the
    locations turns six rows back into the one job it actually is.

    The same grouping catches a role that arrived from two sources: Wise's
    Risk API role came in from both LinkedIn and SmartRecruiters under
    identical titles. The SmartRecruiters copy is the one to keep, because it
    is the employer's own board and carries the description that LinkedIn's
    does not.
    """
    groups: dict[tuple[str, str], list[Job]] = {}
    for j in jobs:
        groups.setdefault((j.company.strip().lower(), j.title.strip().lower()), []).append(j)

    out: list[Job] = []
    for members in groups.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        best = max(members, key=lambda x: (directness(x.platform),
                                           x.salary.confirmed,
                                           len(x.description or "")))
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
        if len({m.platform for m in members}) > 1:
            others = sorted({m.platform for m in members} - {best.platform})
            best.flags.append("also listed on " + ", ".join(others))
        if len({(m.location or "").lower() for m in members}) > 1:
            best.flags.append(f"posted in {len(locs)} locations")
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
