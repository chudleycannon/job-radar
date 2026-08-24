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
_NUM_RATE = r"\d{1,4}(?:\.\d+)?"
_RANGE_RATE = re.compile(
    rf"(?P<c1>[£$€])\s?(?P<lo>{_NUM_RATE})\s*(?:-|–|—|to)\s*(?P<c2>[£$€])?\s?(?P<hi>{_NUM_RATE})",
    re.I,
)
_SINGLE_RATE = re.compile(rf"(?P<c>[£$€])\s?(?P<v>{_NUM_RATE})", re.I)

_PER_DAY = re.compile(r"\b(per|a|/)\s?day\b|\bday rate\b|\bdaily\b|\bpd\b", re.I)
_PER_HOUR = re.compile(r"\b(per|an|/)\s?h(ou)?r\b|\bhourly\b", re.I)

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


def parse_text(text: str | None, default_currency: str | None = None) -> Salary:
    """Best-effort parse of a free-text pay string.

    Returns an unconfirmed Salary when nothing usable is found, which is the
    common case and not an error.
    """
    if not text:
        return Salary()
    full = " ".join(text.split())
    t = full[:400]
    if _NOISE.search(t) and not re.search(r"[£$€]\s?\d", t):
        return Salary(raw=t.strip()[:120])

    got = _scan(t, default_currency, need_context=False)
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
        got = _scan(full[400:20_000], default_currency, need_context=True)
        if got.confirmed:
            return got
    return got


def _scan(t: str, default_currency: str | None, *, need_context: bool) -> Salary:
    period = _period(t)
    rng, single = (_RANGE_RATE, _SINGLE_RATE) if period != "year" else (_RANGE, _SINGLE)

    m = rng.search(t)
    if m:
        lo, hi = _to_float(m.group("lo")), _to_float(m.group("hi"))
        symbol = m.group("c1") or m.group("c2")
        cur = _CUR.get((symbol or "").lower()) or default_currency
        if not symbol or need_context:
            # Only believe it if pay is being discussed within the preceding
            # stretch of text. Required always for an unsymbolled range, and
            # everywhere once we are past the opening block.
            lead = t[max(0, m.start() - 120):m.start()]
            if not _PAY_CONTEXT.search(lead):
                return Salary()
        if lo is not None and hi is not None and hi >= lo:
            return Salary(
                min=lo, max=hi, currency=cur, period=period,
                raw=m.group(0).strip(), confirmed=True,
            )

    m = single.search(t)
    if m:
        v = _to_float(m.group("v"))
        cur = _CUR.get((m.group("c") or "").lower()) or default_currency
        if need_context and not _PAY_CONTEXT.search(t[max(0, m.start() - 120):m.start()]):
            return Salary()
        if v is not None and v > 0:
            return Salary(
                min=v, max=v, currency=cur, period=period,
                raw=m.group(0).strip(), confirmed=True,
            )

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
    return Salary(
        min=lo, max=hi or lo, currency=cur, period="year",
        raw=(r.get("title") or f"{lo:,.0f}-{hi:,.0f}" if lo and hi else None),
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
