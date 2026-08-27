"""Salary parsing and the one filter rule that matters.

The rule, in full:

  * A posting with a **stated** figure below your floor is dropped. You know
    it is too low, so it is noise.
  * A posting with **no** stated figure is kept and labelled "unconfirmed
    salary". Most of the market does not publish pay; filtering those out
    silently bins the majority of real roles.

Only `confirmed=True` salaries can ever disqualify a posting.
"""

from __future__ import annotations

import re

from .models import Salary

_CUR = {"£": "GBP", "$": "USD", "€": "EUR", "gbp": "GBP", "usd": "USD", "eur": "EUR"}

# 120,000 / 120000 / 120k / 120.5k / 189.6K
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[kK]\b|\d{4,}(?:\.\d+)?"

_RANGE = re.compile(
    rf"(?P<c1>[£$€])?\s?(?P<lo>{_NUM})\s*(?:-|–|—|to|up to)\s*(?P<c2>[£$€])?\s?(?P<hi>{_NUM})",
    re.I,
)
_SINGLE = re.compile(rf"(?P<c>[£$€])\s?(?P<v>{_NUM})", re.I)

# Day and hour rates are small numbers, so the annual patterns above skip them
# on purpose: a bare "600" in a job description is far more likely to be a
# headcount than a salary. Once the text says "per day", a small number is
# meaningful and these looser patterns take over.
#
# The trailing lookahead is what stops this eating a thousands separator. The
# rate patterns run FIRST whenever the block says "per day" or "per hour", and
# without the guard "$1,200 per day" matched as "$1": four digits at most, and
# the comma ends the number. That is a 264,000-a-year contract stored as one
# dollar a day, and then silently dropped by any floor at all. Refusing to
# match a number that is followed by more digits or by a comma hands
# "$1,200" back to the annual patterns above, which read the separator.
_NUM_RATE = r"\d{1,4}(?:\.\d+)?(?![\d,])"
_RANGE_RATE = re.compile(
    rf"(?P<c1>[£$€])\s?(?P<lo>{_NUM_RATE})\s*(?:-|–|—|to)\s*(?P<c2>[£$€])?\s?(?P<hi>{_NUM_RATE})",
    re.I,
)
_SINGLE_RATE = re.compile(rf"(?P<c>[£$€])\s?(?P<v>{_NUM_RATE})", re.I)

_PER_DAY = re.compile(r"\b(per|a|/)\s?day\b|\bday rate\b|\bdaily\b|\bpd\b", re.I)
_PER_HOUR = re.compile(r"\b(per|an|/)\s?h(ou)?r\b|\bhourly\b", re.I)
# Needed as a first-class answer, not just as "no rate word found". A figure
# can sit between a rate word and a year word -- "$19-$27 per hour (~$39,000 -
# $56,000 annually)" -- and whichever is NEARER is the one describing it.
_PER_MONTH = re.compile(
    r"\bper month\b|\ba month\b|\bmonthly\b|\bpcm\b|\bper calendar month\b|"
    r"/\s?month\b", re.I)
_PER_YEAR = re.compile(
    r"\bper annum\b|\bannually\b|\bannualized\b|\bannualised\b|\bper year\b|"
    r"\ba year\b|\byearly\b|/\s?(?:year|yr|annum)\b|\bp\.?a\.?\b|"
    r"\bannual (?:base )?(?:salary|pay|compensation)\b", re.I)

# How far either side of a figure a period word still describes it.
#
# `_period` used to answer from the whole chunk it was given, and the chunk
# handed to the second pass is up to 19,600 characters. So one "daily" in a
# benefits paragraph re-read a whole annual salary as a day rate: four
# postings in a 13,588-posting sample had their period set by a word more
# than 300 characters from the figure, and five Bezos Academy adverts stating
# "$19-$27 per hour" came out as $19 a DAY because `_PER_DAY` is tested
# before `_PER_HOUR` and "a day" appeared elsewhere in the body.
_PERIOD_WINDOW = 60


def _period_near(text: str, start: int, end: int) -> str | None:
    """The period named CLOSEST to a figure, or None when none is close.

    Distance decides, not pattern order. Reading day before hour turned
    "per hour" into a day rate whenever the body happened to say "a day"
    somewhere else, and a day rate is 8x an hour rate, so the annualised
    figure that the floor then compares against is wrong by that much in
    whichever direction happens to hurt.
    """
    lead = text[max(0, start - _PERIOD_WINDOW):start]
    trail = text[end:end + _PERIOD_WINDOW]
    best: tuple[int, str] | None = None
    for period, pat in (("day", _PER_DAY), ("hour", _PER_HOUR),
                        ("month", _PER_MONTH), ("year", _PER_YEAR)):
        for m in pat.finditer(lead):
            gap = len(lead) - m.end()
            if best is None or gap < best[0]:
                best = (gap, period)
        m = pat.search(trail)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), period)
    return best[1] if best else None


# HTML entities that survive into a description and break a pay range apart.
#
# Greenhouse double-encodes: the adapter's single `html.unescape` turns
# `&amp;nbsp;` into `&nbsp;` and stops, so "£60,000 to &nbsp; £70,000" reaches
# the parser with a literal entity sitting in the middle of the range. `\s*`
# does not match it, the range collapses to its first figure, and the posting
# is filed £10,000 lower than it pays.
_ENTITY = re.compile(r"&(?:nbsp|#160|#xa0);|&(?:mdash|#8212|#x2014);|"
                     r"&(?:ndash|#8211|#x2013);|&(?:amp|#38);", re.I)
_ENTITY_TEXT = {"nbsp": " ", "#160": " ", "#xa0": " ",
                "mdash": "—", "#8212": "—", "#x2014": "—",
                "ndash": "–", "#8211": "–", "#x2013": "–",
                "amp": "&", "#38": "&"}


def _de_entity(text: str) -> str:
    return _ENTITY.sub(
        lambda m: _ENTITY_TEXT[m.group(0).strip("&;").lower()], text)


# A figure whose very next words name it as a bonus, an allowance or equity is
# not base pay, and treating it as pay is the worst kind of parse: IVC's "Head
# Veterinary Nurse ... we are also offering up to £3,000 welcome bonus" was
# stored as a £3,000 SALARY and then dropped by any floor at all, on a posting
# that stated no salary. Three postings in a 13,588 sample, and every one of
# them a role deleted rather than shown.
#
# Only the words IMMEDIATELY after the number count. "Salary up to £75,000 DOE
# Welcome Bonus up to £5,000" states a real salary and must keep it, so
# anything between the figure and the bonus word means the bonus word is
# describing a different number.
_IS_BONUS = re.compile(
    r"^\s*\)?\s*(?:welcome|signing|sign.?on|sign.?up|joining|referral|"
    r"retention|golden hello|relocation|cpd|training|learning|wellness|"
    r"home ?office|equipment|travel|car|lunch|book|study|kit|wfh)\s*"
    r"(?:bonus|allowance|budget|stipend|support|voucher|fund)"
    r"|^\s*\)?\s*(?:in\s+)?(?:equity|stock|shares|share options|stock options)\b",
    re.I)
# ...unless the figure was introduced as pay in the same breath.
_PAY_LEAD = re.compile(
    r"(?:salary|salaries|compensation|base(?:\s+pay)?|\bpay\b|package|"
    r"remuneration|earnings|wage|ote|rate|per annum|p\.?a\.?)\W{0,15}$", re.I)
# A bonus phrase carrying its OWN amount is describing that amount, not the
# figure in front of it. "Up to £65,000 Welcome bonus of up to £5,000" and
# "£3,000 welcome bonus for this position" are the same shape to a regex and
# opposite in meaning, and this is what separates them. Without it the first
# one was read as a £5,000 salary, which is worse than the bug being fixed.
_BONUS_OWN_AMOUNT = re.compile(r"^[^.]{0,24}?[£$€]\s?\d")

# The same question from the other side: "relocation allowance of up to
# £2,000" names the figure that FOLLOWS it. Nearest label wins, so a real
# "competitive relocation package and a salary of £70,000" still reads as pay.
_BONUS_LEAD = re.compile(
    r"\b(?:welcome|signing|sign.?on|sign.?up|joining|referral|retention|"
    r"golden hello|relocation|cpd|training|learning|wellness|home ?office|"
    r"equipment|travel|car|study|kit)\s+"
    r"(?:bonus|allowance|budget|stipend|support|package|voucher|fund)"
    r"|\bequity\b|\bstock options?\b|\bshare options?\b", re.I)
_BONUS_WINDOW = 60


def _is_bonus_figure(text: str, start: int, end: int) -> bool:
    """Is this number a bonus, an allowance or equity rather than pay?"""
    after = text[end:end + 40]
    m = _IS_BONUS.match(after)
    if m and not _BONUS_OWN_AMOUNT.match(after[m.end():]) \
            and not _PAY_LEAD.search(text[max(0, start - 40):start]):
        return True
    lead = text[max(0, start - _BONUS_WINDOW):start]
    bonus = max((x.end() for x in _BONUS_LEAD.finditer(lead)), default=-1)
    if bonus < 0:
        return False
    pay = max((x.end() for x in _PAY_WORD.finditer(lead)), default=-1)
    return bonus > pay


_PAY_WORD = re.compile(
    r"\b(?:salary|salaries|compensation|base pay|base salary|pay|package|"
    r"remuneration|earnings|wage|ote|rate|per annum|pay range|salary range)\b",
    re.I)

# Text that means "we are not telling you", not "zero"
_NOISE = re.compile(r"competitive|doe|depending on experience|negotiable", re.I)


def _to_float(tok: str) -> float | None:
    tok = tok.strip().replace(",", "")
    mult = 1.0
    if tok.lower().endswith("k"):
        mult = 1000.0
        tok = tok[:-1].strip()
    try:
        return float(tok) * mult
    except ValueError:
        return None


def _period(text: str) -> str:
    if _PER_DAY.search(text):
        return "day"
    if _PER_HOUR.search(text):
        return "hour"
    return "year"


# A number range in prose is not a salary. "40,000 to 120,000 requests per
# second" and "grew from 25,000 to 90,000 members" both parse as money if the
# currency symbol is optional, and because this runs over the first 1500
# characters of a description for most adapters, a confirmed-but-wrong figure
# below the floor silently DROPS a real role. So an unsymbolled range has to
# be introduced by something that means pay.
_PAY_CONTEXT = re.compile(
    r"\b(salary|salaries|compensation|comp\b|pay|paid|package|remuneration|"
    r"base|earnings|wage|ote|bonus|range for this role|pay range|"
    r"salary range|annum|per year|pa\b)\b", re.I)


# What a bare number most likely means, given where the job is.
#
# `enrich` used to pass the READER's configured floor currency as the default
# for any figure with no symbol on it, anywhere in the world. So an Indian
# posting reading "Annual salary: 900,000 to 1,100,000" was stored, confirmed,
# as "$900k - $1,100k" for a reader whose floor was in dollars: about 8,500
# pounds presented as most of a million, and `confirmed=True`, so it could
# clear a floor it comes nowhere near.
#
# The reader's own currency is the one thing that cannot be evidence here. The
# job's country can. Where that is unknown the answer is to say nothing: an
# unconfirmed salary is shown to the reader and labelled, and can never
# disqualify a role, which is the safe direction. A figure that carries its
# own symbol never reaches this at all.
#
# Only the countries the bundled boards actually produce. A country missing
# from here is not a bug, it is an unconfirmed salary.
CURRENCY_OF_COUNTRY = {
    "UK": "GBP", "GB": "GBP", "IE": "EUR", "US": "USD", "CA": "CAD",
    "AU": "AUD", "NZ": "NZD", "IN": "INR", "SG": "SGD", "AE": "AED",
    "JP": "JPY", "CN": "CNY", "HK": "HKD", "CH": "CHF", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "BR": "BRL",
    "MX": "MXN", "ZA": "ZAR", "IL": "ILS", "TR": "TRY", "KR": "KRW",
    "PH": "PHP", "MY": "MYR", "TH": "THB", "ID": "IDR", "VN": "VND",
    "AR": "ARS", "CL": "CLP", "CO": "COP", "NG": "NGN", "KE": "KES",
    "EG": "EGP", "SA": "SAR", "QA": "QAR", "PK": "PKR", "BD": "BDT",
    "UA": "UAH", "RO": "RON", "HU": "HUF", "BG": "BGN", "RS": "RSD",
    "IS": "ISK",
}
# The euro, spelled out so the map above stays one line per fact.
for _cc in ("AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR",
            "IT", "LT", "LU", "LV", "MT", "NL", "PT", "SI", "SK"):
    CURRENCY_OF_COUNTRY[_cc] = "EUR"


def currency_of_country(country: str | None) -> str | None:
    """The currency a bare number in that country probably means. None when
    we do not know, which is not a failure: it means "do not guess"."""
    if not country:
        return None
    return CURRENCY_OF_COUNTRY.get(country.strip().upper())


def parse_text(text: str | None, default_currency: str | None = None) -> Salary:
    """Best-effort parse of a free-text pay string.

    Returns an unconfirmed Salary when nothing usable is found, which is the
    common case and not an error.
    """
    if not text:
        return Salary()
    full = " ".join(_de_entity(text).split())
    t = full[:400]
    if _NOISE.search(t) and not re.search(r"[£$€]\s?\d", t):
        return Salary(raw=t.strip()[:120])

    got = _scan(full, 0, 400, default_currency, need_context=False)
    if got.confirmed:
        return got
    # Most adapters hand this the whole description, and employers who do
    # publish a figure usually put it at the bottom: "Base salary range:
    # £48,000 - £72,000" sat at character 4,109 of a GoCardless posting and
    # 9% of a real scan came back "unconfirmed" while stating a range in the
    # body. Beyond the opening block the risk of reading a funding round or a
    # customer count as pay goes up, so out there a figure only counts when
    # pay is being discussed right next to it, symbol or no symbol.
    if len(full) > 400:
        got = _scan(full, 400, 20_000, default_currency, need_context=True)
        if got.confirmed:
            return got
    return got


def _scan(full: str, begin: int, stop: int, default_currency: str | None, *,
          need_context: bool) -> Salary:
    """Read one block of text for a pay figure.

    The annual-shaped patterns run first because they are the specific ones:
    `_SINGLE_RATE` reads "£45,000" as "£45" (its number is at most four
    digits and it stops at the comma), so letting the rate family go first
    turns a real salary into a rate one time in ten.

    The period is then taken from the words nearest THAT figure rather than
    from anywhere in the block, and a figure the text calls a bonus is
    skipped rather than filed as pay.
    """
    # The window is a slice of `full`, but the period and bonus questions are
    # asked of `full` at absolute positions. Asking them of the slice loses
    # any label that straddles the cut: "$10,000+ per month" sat at character
    # 383 and the block ended at 400, so "per mont" did not match "per month"
    # and a monthly figure was confirmed as an annual salary.
    t = full[begin:stop]
    chunk_period = _period(t)
    families = [(_RANGE, _SINGLE, False), (_RANGE_RATE, _SINGLE_RATE, True)]
    if chunk_period != "year":
        # What the shipped code did: a block that says "per hour" is read with
        # the rate patterns. Keeping that order matters, because the annual
        # patterns would otherwise reach past an hourly base to a commission
        # figure further down and report that as the salary.
        families.reverse()

    for rng, single, rate in families:
        for m in rng.finditer(t):
            lo, hi = _to_float(m.group("lo")), _to_float(m.group("hi"))
            if lo is None or hi is None or hi < lo:
                continue
            if _is_bonus_figure(full, begin + m.start(), begin + m.end()):
                continue
            symbol = m.group("c1") or m.group("c2")
            cur = _CUR.get((symbol or "").lower()) or default_currency
            if not symbol or need_context:
                # Only believe it if pay is being discussed within the
                # preceding stretch of text. Required always for an
                # unsymbolled range, and everywhere once we are past the
                # opening block.
                lead = t[max(0, m.start() - 120):m.start()]
                if not _PAY_CONTEXT.search(lead):
                    return Salary()
            period = _period_near(full, begin + m.start(), begin + m.end()) or (
                chunk_period if rate or len(t) <= 400 else "year")
            if period == "month" or (rate and period == "year"):
                # The rate patterns exist only to read day and hour rates:
                # their number is four digits at most, so believing one as an
                # annual figure files a 13.45 an hour job as 13.45 a YEAR and
                # then hides it behind any floor at all. A monthly figure has
                # no period to be stored in, and reading it as annual is the
                # same mistake twelve times over.
                continue
            return Salary(min=lo, max=hi, currency=cur, period=period,
                          raw=m.group(0).strip(), confirmed=True)

        for m in single.finditer(t):
            v = _to_float(m.group("v"))
            if v is None or v <= 0:
                continue
            if _is_bonus_figure(full, begin + m.start(), begin + m.end()):
                continue
            if need_context and not _PAY_CONTEXT.search(
                    t[max(0, m.start() - 120):m.start()]):
                return Salary()
            period = _period_near(full, begin + m.start(), begin + m.end()) or (
                chunk_period if rate or len(t) <= 400 else "year")
            if period == "month" or (rate and period == "year"):
                continue
            cur = _CUR.get((m.group("c") or "").lower()) or default_currency
            return Salary(min=v, max=v, currency=cur, period=period,
                          raw=m.group(0).strip(), confirmed=True)

    return Salary()


def from_ashby(comp: dict | None) -> Salary:
    """Ashby returns structured pay when the board is fetched with
    `includeCompensation=true`. Without that param the field is absent entirely.
    """
    if not comp:
        return Salary()
    raw = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    s = parse_text(raw)
    if s.confirmed:
        s.raw = (raw or s.raw or "").strip()[:120]
    return s


def from_greenhouse(ranges: list | None) -> Salary:
    """Greenhouse exposes `pay_input_ranges` only when the board is fetched
    with `pay_transparency=true`. `content=true` does NOT trigger it; they are
    separate parameters, which is an easy one to miss.
    """
    if not ranges:
        return Salary()
    r = ranges[0] if isinstance(ranges, list) else ranges
    if not isinstance(r, dict):
        return Salary()

    def _amt(*keys):
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v) / 100.0 if k.endswith("_cents") else float(v)
        return None

    lo = _amt("min_cents", "min_value", "min")
    hi = _amt("max_cents", "max_value", "max")
    cur = r.get("currency_type") or r.get("currency")
    if lo is None and hi is None:
        return parse_text(r.get("title"))

    # Greenhouse states no period anywhere in `pay_input_ranges`, so this used
    # to assume "year" for every figure in it. Databricks publish hourly rates
    # through the same field and label them in the title: "SF Bay Area Hourly
    # Rate", min_cents 5400. Read as annual that is a salary of $54, which is
    # not a rounding error, it is a role that gets discarded by any floor.
    title = str(r.get("title") or "")
    period = "year"
    if re.search(r"\bhourly\b|\bper hour\b|/\s*hr\b|\bhour rate\b", title, re.I):
        period = "hour"
    elif re.search(r"\bdaily\b|\bper day\b|/\s*day\b|\bday rate\b", title, re.I):
        period = "day"
    elif (hi or lo or 0) < 2000:
        # Unlabelled and far too small to be a year's pay. Databricks send
        # 17040 cents under the generic title "Local Pay Range", which is
        # $170.40 and is plainly a rate, but nothing in the payload says which
        # kind. This follows the rule the Reed adapter already states: an
        # unlabelled figure below 2,000 is left unconfirmed rather than
        # asserted as an annual salary, because only a confirmed figure can
        # disqualify a role and a wrong one disqualifies it wrongly.
        return Salary(min=lo, max=hi or lo, currency=cur, period="year",
                      raw=title or None, confirmed=False)

    return Salary(
        min=lo, max=hi or lo, currency=cur, period=period,
        raw=(title or f"{lo:,.0f}-{hi:,.0f}" if lo and hi else None),
        confirmed=True,
    )


def from_pinpoint(posting: dict | None) -> Salary:
    """Pinpoint publishes pay as real numbers, not as a sentence to parse.

    `compensation_visible` is the employer's own switch. When it is off the
    minimum and maximum are still present but must not be shown or filtered
    on, so this treats a hidden figure as no figure at all rather than as a
    confirmed one. Getting that backwards would drop roles on a floor the
    employer never published.

    Frequencies seen live are `year`, `hour` and `week`. Salary only models
    year, day and hour, so weekly and monthly rates are annualised here rather
    than dropped: a rate this tool cannot express is a rate the floor cannot
    act on, which quietly loses the role.
    """
    if not isinstance(posting, dict) or not posting.get("compensation_visible"):
        return Salary()

    def _amt(key):
        v = posting.get(key)
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    lo, hi = _amt("compensation_minimum"), _amt("compensation_maximum")
    raw = (posting.get("compensation") or "").strip()
    if lo is None and hi is None:
        return parse_text(raw)

    freq = (posting.get("compensation_frequency") or "year").lower()
    mult = {"week": 52.0, "month": 12.0, "annual": 1.0, "year": 1.0}.get(freq)
    if mult is not None:
        period = "year"
        lo = lo * mult if lo is not None else None
        hi = hi * mult if hi is not None else None
    else:
        period = freq if freq in ("day", "hour") else "year"

    return Salary(
        min=lo, max=hi if hi is not None else lo,
        currency=(posting.get("compensation_currency") or "").upper() or None,
        period=period, raw=raw[:120] or None, confirmed=True,
    )


# What Reed's `salaryType` says, in the vocabulary Salary understands.
_REED_PERIOD = {
    "per annum": "year", "annum": "year", "annual": "year", "annually": "year",
    "per day": "day", "day": "day", "daily": "day",
    "per hour": "hour", "hour": "hour", "hourly": "hour",
}
# Salary models only year, day and hour, so weekly and monthly rates are
# annualised rather than dropped, exactly as from_pinpoint does: a rate this
# tool cannot express is a rate the floor cannot act on, which quietly loses
# the role.
_REED_ANNUALISE = {"per week": 52.0, "week": 52.0, "weekly": 52.0,
                   "per month": 12.0, "month": 12.0, "monthly": 12.0}

# Reed's SEARCH endpoint returns `minimumSalary` and `maximumSalary` as bare
# numbers with no period at all; only the per-job details endpoint carries
# `salaryType`. So a figure off the search endpoint has to be read for what it
# plainly is. Nothing below this can be a UK annual salary (the National
# Minimum Wage alone puts a full-time year several times higher, and even a few
# hours a week runs to thousands), while senior contract day rates top out
# somewhere near 1,500. So a number under this is a rate Reed has not labelled,
# and treating it as an annual figure would read a 650 a day contract as 650 a
# year and bin it against any floor at all.
_REED_MIN_ANNUAL = 2000.0


def from_reed(job: dict | None) -> Salary:
    """Reed publishes pay as numbers, but which numbers depends on the endpoint.

    The details endpoint states `yearlyMinimumSalary` / `yearlyMaximumSalary`,
    which is Reed's own annualisation and is preferred over doing it here.
    The search endpoint states neither those nor `salaryType`, so an unlabelled
    figure is only trusted as annual when it is big enough to be one.

    An unlabelled rate comes back UNCONFIRMED rather than as a guess. That is
    the safe direction: an unconfirmed salary is shown to the reader and can
    never disqualify a role, whereas a wrongly annualised one silently deletes
    it. `parse_reed` then gets a second go at it from the advert text, which
    does say "per day".

    Reed also lets an employer hide the salary, in which case none of these
    fields are populated at all. That is "the employer published no figure",
    not a parse failure, and it must stay unconfirmed for the same reason.
    """
    if not isinstance(job, dict):
        return Salary()

    def _amt(key):
        v = job.get(key)
        try:
            return float(v) if v is not None and float(v) > 0 else None
        except (TypeError, ValueError):
            return None

    raw = str(job.get("salary") or "").strip()[:120] or None
    currency = str(job.get("currency") or "").strip().upper() or None

    lo, hi = _amt("yearlyMinimumSalary"), _amt("yearlyMaximumSalary")
    period = "year"
    if lo is None and hi is None:
        lo, hi = _amt("minimumSalary"), _amt("maximumSalary")
        stype = str(job.get("salaryType") or "").strip().lower()
        mult = _REED_ANNUALISE.get(stype)
        if mult is not None:
            lo = lo * mult if lo is not None else None
            hi = hi * mult if hi is not None else None
        elif stype:
            period = _REED_PERIOD.get(stype, "year")
        else:
            top = hi if hi is not None else lo
            if top is not None and top < _REED_MIN_ANNUAL:
                return Salary(min=lo, max=hi if hi is not None else lo,
                              currency=currency, period="year", raw=raw,
                              confirmed=False)

    if lo is None and hi is None:
        return parse_text(raw)

    return Salary(
        min=lo, max=hi if hi is not None else lo, currency=currency,
        period=period, raw=raw, confirmed=True,
    )


def clears_floor(sal: Salary, floor: float | None, currency: str | None = None) -> tuple[bool, str]:
    """Apply the rule. Returns (keep, why).

    Unconfirmed salaries always pass. Cross-currency comparison is refused
    rather than guessed at, because a wrong FX assumption silently drops real
    roles; those are kept and flagged instead.
    """
    if not floor:
        return True, ""
    if not sal.confirmed:
        return True, "unconfirmed salary"
    if currency and sal.currency and sal.currency != currency:
        return True, f"salary in {sal.currency}, floor in {currency}, not compared"
    if currency and not sal.currency:
        # A figure with no currency mark used to be compared against the floor
        # as a bare number, so an unsymbolled 2,500,000 zloty cleared a
        # 55,000 euro floor and an unsymbolled 45,000 of anything failed it.
        # Neither outcome was flagged.
        return True, f"salary has no currency, floor in {currency}, not compared"
    top = sal.annualised()
    if top is None:
        return True, "unconfirmed salary"
    if top < floor:
        shown = sal.raw or f"{top:,.0f}"
        return False, f"stated pay {shown} below floor"
    return True, ""


# Adzuna publishes a figure for almost every advert, but only some of them come
# from the employer. `salary_is_predicted` is "1" when the number is Adzuna's
# own Jobsworth estimate, produced by a model from the title and location, and
# "0" when the advertiser stated it. Treating an estimate as a stated figure is
# the worst available outcome in both directions: a low estimate silently DROPS
# a real role against the floor, and a high one promotes a role that pays
# nothing like it. Only `confirmed=True` may disqualify a posting, so an
# estimate has to come back unconfirmed.
#
# The currency is not in the payload at all. It follows from which country
# endpoint was called, because Adzuna runs one index per country and states
# pay "in the local currency", so `parse_adzuna` passes it in from the URL.
_ADZUNA_MIN_ANNUAL = 2000.0


def from_adzuna(job: dict | None, currency: str | None = None) -> Salary:
    """Adzuna's pay fields, with the predicted ones refused.

    Three ways this comes back unconfirmed, and all three are correct:

      * `salary_is_predicted == "1"`. A Jobsworth estimate is a model output,
        not something the employer wrote down.
      * Neither figure is present. The advertiser published no pay.
      * The figure is too small to be an annual salary. Adzuna's own filters
        are annual and it normalises rates upward, but a feed that arrives
        unnormalised would put a bare `650` day rate in the same field, and
        reading that as a year's pay bins the role against any floor at all.
        Same threshold and same reasoning as `from_reed`.

    The numbers are kept on the unconfirmed Salary rather than thrown away, so
    the reader still sees what Adzuna thought.
    """
    if not isinstance(job, dict):
        return Salary()

    def _amt(key):
        v = job.get(key)
        try:
            return float(v) if v is not None and float(v) > 0 else None
        except (TypeError, ValueError):
            return None

    lo, hi = _amt("salary_min"), _amt("salary_max")
    if lo is None and hi is None:
        return Salary(currency=currency)

    top = hi if hi is not None else lo
    predicted = str(job.get("salary_is_predicted") or "").strip() == "1"
    plausible = top is not None and top >= _ADZUNA_MIN_ANNUAL
    return Salary(
        min=lo, max=hi if hi is not None else lo, currency=currency,
        period="year",
        raw=("estimated by Adzuna" if predicted else None),
        confirmed=not predicted and plausible,
    )
