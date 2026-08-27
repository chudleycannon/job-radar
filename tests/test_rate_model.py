"""What apply.workable.com's rate limit is, measured, and why 0.7 stands.

0.7 requests a second against this one host is the floor of the whole scan:
2,094 boards at 1.43s each is 49.9 minutes, and Workable answers in 0.213s,
so 85% of that is the limiter waiting. Half an hour a run is sitting there
if the real limit is higher, which is why this keeps getting reopened.

It was reopened again on 2026-08-27 and measured with 571 live requests. The
result is not a rate. It is that THERE IS NO STATIC RULE TO FIND, and the
evidence for that is arithmetic rather than opinion:

  * 2026-08-26: a SUSTAINED 1.5/s run was refused with HTTP 429 at request
    301, 202 seconds in.
  * 2026-08-27: 320 requests at 3.0/s, then 250 more at 3.0/s five minutes
    later, every one HTTP 200. More requests, twice the rate, no refusal.

A host that refuses 301 requests at 1.5/s and accepts 570 at 3.0/s is not
running a token bucket and is not running a rolling window. Both of those
models are falsified below by the two observations above, over a grid of
every parameter pair either could have. The tests are the argument: they do
not assert a conclusion somebody wrote down, they re-derive it.

WHAT IT ACTUALLY LOOKS LIKE. The endpoint sits behind Cloudflare (server:
cloudflare, CF-RAY, cf-cache-status: DYNAMIC, a __cf_bm bot-management
cookie) and publishes no rate-limit headers at all. Workable DOES document
limits, 10 requests per 10 seconds for an account token, and those responses
carry X-Rate-Limit-Limit / -Remaining / -Reset, but that is the
AUTHENTICATED api and it is scoped per token. This public widget endpoint is
a different regime with no headers to read, so the tool cannot pace itself
from what the host says. It has to guess, and the guess cannot be checked.

The two refusals on record both happened during heavy, long-running,
many-host activity from this machine; every clean run, including both of
today's, was an isolated single-purpose run. That is a hypothesis about
Cloudflare's adaptive posture, not a measurement, and it is deliberately NOT
encoded as a model here. It is written down so the next person does not
spend another 571 requests rediscovering that the pattern of OUR requests is
not what separates the refusals from the passes.

WHY THE RATE STAYS AT 0.7. Not because 0.7 is right, it demonstrably is not:
a real scan on 2026-08-26 got 41 of 419 boards back as 429 at exactly this
rate. It stays because nothing measured supports a better number. Every
clean run above 1.5/s on record is under two minutes long, and both refusals
came from runs over 200 seconds, so a 12-minute scan phase at 3.0/s is
untested in the only dimension where this host has ever said no. The
downside is not a slow scan, it is the `Retry-After: 57841` this host has
already answered once: sixteen hours with no Workable coverage at all, which
is the largest single-platform block in the list.

The probe that produced today's numbers is tools/probe_rate_limit.py and its
raw log, every status and every header, is tools/rate_probe_log.jsonl.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.fetch import PER_HOST_RPS          # noqa: E402

HOST = "apply.workable.com"

# Every run against this host anybody has recorded, refused or clean.
#
# `refused_at` is the request number that came back 429, or None for a run
# that finished. `rate` is requests per second aimed at THIS host. The 8
# workers on 2026-08-26c are workers, not rate: the pacing was still 0.7.
OBSERVATIONS = [
    # label,             date,          rate, requests, seconds, refused_at
    ("ramped burst",     "2026-08-26",  3.00,   90,      None,   None),
    ("250 single thread","2026-08-26",  0.70,  250,       357,   None),
    ("250 at half rate", "2026-08-26",  0.35,  250,       714,   None),
    ("150 on 8 workers", "2026-08-26",  0.70,  150,       214,   None),
    ("sustained 1.5",    "2026-08-26",  1.50,  301,       202,    301),
    ("real scan",        "2026-08-26",  0.70,  419,      None,    176),
    ("P1 rested",        "2026-08-27",  3.00,  320,     106.5,   None),
    ("P2 after 5 min",   "2026-08-27",  3.00,  250,      83.2,   None),
]

# The two that do the work. Everything else is consistent with several
# models; only these two together rule the static ones out.
REFUSED_AT_1_5 = 301            # 1.5/s, 429 on request 301, t=202s
CLEAN_AT_3_0 = 320              # 3.0/s, 320 requests, no refusal, t=106.5s

# Today's cost, in requests to a live host. Recorded so a future run of this
# work knows what the last one spent and does not treat 600 as free.
REQUESTS_SPENT_2026_08_27 = 571
REFUSALS_SEEN_2026_08_27 = 0

# The obvious objection to a clean run this size is that it never reached
# Workable at all. It did. `cf-cache-status` was DYNAMIC on all 571, so
# Cloudflare served none of them from its edge, and only 19 carried
# Workable's own `x-cached-response`. Board URLs were distinct throughout,
# which is what makes that true rather than lucky.
CF_EDGE_CACHE_HITS = 0
APP_CACHE_HITS = 19

# Latency, seconds, over the 571. The scan's own figure for this host is
# 0.213s. Three times the rate did not make the host slower, which is the
# other thing a host does when it is unhappy and did not happen here.
LATENCY_MEDIAN = 0.143
LATENCY_P95 = 0.230
SCAN_BASELINE_LATENCY = 0.213

# Response headers the widget endpoint actually returned, 2026-08-27, on a
# 200. Pinned because their ABSENCE is the finding: no header here says
# anything about a budget, so there is nothing to pace from.
OBSERVED_HEADERS = {
    "CF-RAY", "Connection", "Content-Length",
    "Content-Security-Policy-Report-Only", "Content-Type", "Date",
    "X-KB", "X-TS", "access-control-allow-origin", "cf-cache-status",
    "content-disposition", "content-encoding", "server", "set-cookie",
    "strict-transport-security", "vary", "x-content-type-options",
    "x-sv", "x-xss-protection",
}
DOCUMENTED_API_HEADERS = {
    "X-Rate-Limit-Limit", "X-Rate-Limit-Remaining", "X-Rate-Limit-Reset",
}

WORKABLE_BOARDS = 2094          # sources.json, apply.workable.com entries


# ---- the two models a limit like this is usually made of -------------------

def bucket_first_refusal(rate: float, n: int, capacity: float,
                         refill: float) -> int | None:
    """Request number that a token bucket would refuse, or None.

    Starts FULL, which is the assumption most generous to the model: a
    bucket that cannot explain the record even when handed a full bucket
    cannot explain it at all.
    """
    tokens = float(capacity)
    prev = 0.0
    for i in range(n):
        now = i / rate
        tokens = min(capacity, tokens + refill * (now - prev))
        prev = now
        if tokens < 1.0:
            return i + 1
        tokens -= 1.0
    return None


def window_first_refusal(rate: float, n: int, limit: int,
                         window: float) -> int | None:
    """Request number a rolling `limit` per `window` would refuse, or None."""
    times: list[float] = []
    head = 0
    for i in range(n):
        now = i / rate
        while head < len(times) and times[head] <= now - window:
            head += 1
        if len(times) - head >= limit:
            return i + 1
        times.append(now)
    return None


# ---- the falsification ----------------------------------------------------

def test_no_token_bucket_of_any_size_explains_both_runs():
    """A bucket that refuses the 1.5/s run must refuse the 3.0/s one sooner.

    This is the whole finding in one assertion. A token bucket drains at
    (rate - refill), so the FASTER run empties it first: any (capacity,
    refill) tuned to refuse request 301 at 1.5/s refuses well before request
    320 at 3.0/s. The host did the opposite, so it is not a token bucket.
    """
    survivors = [
        (cap, round(refill, 2))
        for cap in range(1, 601)
        for refill in (x / 20 for x in range(0, 61))       # 0 .. 3.0 by 0.05
        if bucket_first_refusal(1.5, 340, cap, refill) == REFUSED_AT_1_5
        and bucket_first_refusal(3.0, CLEAN_AT_3_0, cap, refill) is None
    ]
    assert survivors == [], f"a bucket does fit after all: {survivors[:5]}"


def test_no_rolling_window_of_any_shape_explains_both_runs():
    """Same argument for `N requests per M seconds`, over every N and M.

    A window that is wider than either run counts requests, so it refuses
    both at the same COUNT and 320 clean beats 301 refused. A window
    narrower than either run counts rate, so the 3.0/s run fills it first.
    Neither leaves room for what happened.
    """
    survivors = [
        (limit, window)
        for limit in range(1, 601, 2)
        for window in range(10, 1801, 10)
        if window_first_refusal(1.5, 340, limit, window) == REFUSED_AT_1_5
        and window_first_refusal(3.0, CLEAN_AT_3_0, limit, window) is None
    ]
    assert survivors == [], f"a window does fit after all: {survivors[:5]}"


def test_the_fastest_run_on_record_passed_and_the_slower_ones_were_refused():
    """The inversion, stated directly, so it cannot be argued away later.

    Both refusals came at rates BELOW the rate of every clean run today. Any
    future claim that "we were going too fast" has to get past this: the
    fastest traffic this host has ever been sent from here is also the only
    traffic it never once refused.
    """
    refused = [rate for _, _, rate, _, _, at in OBSERVATIONS if at]
    clean = [rate for _, _, rate, _, _, at in OBSERVATIONS if not at]
    assert max(refused) == 1.5
    assert max(clean) == 3.0
    assert max(clean) > max(refused)


def test_the_widget_endpoint_gives_the_tool_nothing_to_pace_from():
    """No budget header on a 200, so no header-driven limiter is possible.

    Worth pinning because the obvious better answer, read Remaining and
    Reset and pace exactly, was checked and is not available here. The
    documented trio belongs to the authenticated API and is scoped per
    token, not per IP.
    """
    assert not (OBSERVED_HEADERS & DOCUMENTED_API_HEADERS)
    lowered = {h.lower() for h in OBSERVED_HEADERS}
    assert not any("ratelimit" in h.replace("-", "") for h in lowered)
    assert "retry-after" not in lowered
    # Cloudflare, which is what makes the limit adaptive and undocumented.
    assert {"cf-ray", "server", "cf-cache-status"} <= lowered


def test_the_measurement_cost_is_recorded_with_its_refusal_count():
    """571 requests, zero refusals. A clean run is only evidence with a cost.

    The number matters as much as the result: it says how much of a 600
    request budget bought this, so the next attempt can judge whether a
    bigger one would buy anything.
    """
    assert REQUESTS_SPENT_2026_08_27 == 571
    assert REFUSALS_SEEN_2026_08_27 == 0
    today = [o for o in OBSERVATIONS if o[1] == "2026-08-27"]
    assert sum(o[3] for o in today) + 1 == REQUESTS_SPENT_2026_08_27
    assert all(o[5] is None for o in today)


def test_the_clean_run_was_not_a_cache_answering_itself():
    """571 requests that mostly reached Workable, not Cloudflare's edge.

    A burst of repeated URLs behind a CDN measures the CDN. This one used a
    distinct board per request and came back DYNAMIC every time, so the
    clean result is a statement about the origin's limit and not about a
    cache hit rate.
    """
    assert CF_EDGE_CACHE_HITS == 0
    origin = REQUESTS_SPENT_2026_08_27 - CF_EDGE_CACHE_HITS - APP_CACHE_HITS
    assert origin / REQUESTS_SPENT_2026_08_27 > 0.95


def test_three_times_the_rate_did_not_make_the_host_any_slower():
    """No soft throttling either, which is the quieter way to say no.

    A host that is being pushed usually gets slower before it starts
    refusing. At 3.0/s this one answered FASTER than the 0.213s the scan
    sees at 0.7/s, so there is no sign of strain to read as a warning.
    """
    assert LATENCY_MEDIAN < SCAN_BASELINE_LATENCY
    assert LATENCY_P95 < 1.5 * SCAN_BASELINE_LATENCY


def test_workable_stays_paced_at_the_rate_the_scans_were_measured_at():
    """0.7 until something longer than two minutes says otherwise.

    Not a claim that 0.7 is safe. It is not: a real scan at exactly this
    rate had 41 of 419 boards refused. It is a claim that no measured number
    beats it, because every clean run above 1.5/s lasted under 107 seconds
    and both refusals came from runs past 200 seconds. Raising this without
    a clean run of at least 2,094 requests at the new rate is arguing with
    the only two failures anybody has actually seen.
    """
    assert PER_HOST_RPS[HOST] == 0.7
    longest_clean_above_1_5 = max(
        secs for _, _, rate, _, secs, at in OBSERVATIONS
        if rate > 1.5 and at is None and secs
    )
    assert longest_clean_above_1_5 < 200
    assert min(secs for _, _, _, _, secs, at in OBSERVATIONS
               if at and secs) > longest_clean_above_1_5


def test_a_burst_then_pause_schedule_would_not_have_paid_for_itself():
    """Why the head-burst idea was dropped rather than shipped.

    The refusals on record began at requests 176 and 301, so the first
    ~150 requests to this host have never been refused at any rate. Front
    loading exactly those is the one schedule the evidence supports, and the
    arithmetic says it saves two minutes of a fifty minute floor. Two
    minutes is not worth a schedule that has to be right about a limit
    nobody can characterise.
    """
    flat = WORKABLE_BOARDS / 0.7
    head = 150 / 2.0 + (WORKABLE_BOARDS - 150) / 0.7
    saved = flat - head
    assert 130 < saved < 145                      # 139 seconds
    assert saved / flat < 0.05                    # under 5% of the floor


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
