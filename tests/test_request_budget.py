"""How many requests one scan makes, and where a request can be saved.

The scan's wall clock is not set by this machine and not by how fast the
requests go out. It is set by the SLOWEST HOST'S QUEUE: every host is paced on
its own clock, so the run cannot finish before the busiest host's requests
have been spent at that host's rate. Measured from the bundled list on
2026-08-27:

    apply.workable.com        2,094 boards   0.7 req/s   49.9 min   <- floor
    boards-api.greenhouse.io  4,078 boards   3.0 req/s   22.7 min
    api.ashbyhq.com           2,607 boards   3.0 req/s   14.5 min
    api.smartrecruiters.com     910 boards   3.0 req/s    5.1 min
    everything else                                    under 1.5 min

Those are the rates the limiter actually applies, which are not all the rates
`PER_HOST_RPS` states -- see
`test_the_floor_hosts_rate_is_the_one_the_table_states` for why the three
above 3.0 do not survive `gap_for`.

7,749 of the 7,781 hosts carry a single board each, so they cost one request
and finish in parallel with everything else.

The consequence, and it is the whole reason this file exists: **a request
saved anywhere except apply.workable.com saves no wall clock at all.** Cutting
Greenhouse to zero would take 4,078 requests out of the run and end it at the
same minute. Only Workable's 2,094 move the finish line, and only until the
run drops below Greenhouse's 22.7 minutes.

What was measured against the owner's own database, before anything here was
written. Two readings, and the difference between them is the point:

  * Over the whole history (3,692 roles, 20 runs, 2026-08-18 to 08-27), 1,770
    of 17,810 boards (9.9%) have ever produced a role that passed the filters.
    On Workable it is 153 of 2,094 (7.3%). It is tempting to read that as
    16,040 boards of waste.
  * It is not. Taking the one clean steady-state run, 2026-08-27 -- same
    source list as the run before it, no host throttled -- 327 new matching
    roles arrived from 178 boards, and 57 of those boards (32%) had never
    produced anything before. 65 of the 327 roles (20%) came from them.
    Reading only the boards with a track record would have missed a fifth of
    the day's roles, including two Workable employers by name.
  * And a prune is self-fulfilling: a board that is never read can never
    produce, so it can never earn its way back onto the list. Only a rotation
    avoids that, and a rotation pays in delay rather than in coverage. A
    weekly cold cycle would cut apply.workable.com from 2,094 requests to
    about 430 (49.9 min to 10.2 min, with the floor then moving to
    Greenhouse's 22.7) at the cost of seeing a cold board's roles a mean of
    3.5 days late.
  * 2,088 of the 2,094 Workable boards answered with at least one posting, so
    they are not dead boards. They are live boards that happen not to be
    advertising this owner's job today. Across all platforms only 302 boards
    (1.7%) answered with nothing at all, and 257 of those are Workday, where
    an empty answer means the keyword search matched nothing rather than that
    the board is gone. Six of them are on Workable: skipping all six saves
    8.6 seconds.

So there is no tail to cut. Nor is anything asked for twice: the bundled list
holds no duplicate URL, the queue reorder neither duplicates nor drops a
source, the enrichment pass already refuses to re-read an advert it has, and
its fetcher chain stops at the first answer. The one thing a scan really does
spend twice is a RETRY into a refusal, and the measurement below shows that
what those retries cost is not the requests but the pacing they compound.

Conditional requests were measured too, because they are the first thing
anyone reaches for here. 13 platforms sampled live on 2026-08-27: 9 offer an
ETag and answer a conditional re-ask with a real 304 and a zero-byte body
(Greenhouse, Ashby, SmartRecruiters, Personio, Breezy, Pinpoint, Teamtailor,
Lever, Lever EU); 4 offer no validator at all (Recruitee, Jobvite, iCIMS,
Oracle). A 304 came back in 0.109s against 0.309s for the 251 KB full answer,
so it is 65% cheaper -- and it is still a request, so it takes a Workable slot
exactly like a 200 and moves the floor by nothing. Nor is the volume it would
remove a constraint: 201 KB mean over 57 boards is about 3.5 GB a scan, and 16
parallel pulls measured 98 Mbit/s with no contention at all. So the honest
answer is that conditional requests buy bandwidth and nothing else here.

This file pins that arithmetic so the next person does not re-derive it.
Nothing here touches the network.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import enrich, fetch as fetch_mod, store
from jobradar.models import Source

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "sources" / "sources.json"


# ---------------------------------------------------------------- the budget

def _bundled():
    return json.loads(BUNDLED.read_text(encoding="utf-8"))["sources"]


def test_no_board_is_in_the_bundled_list_twice():
    """Two entries with the same URL are two requests for one answer.

    Cheapest possible saving and worth checking on every change to the list,
    because the list is assembled by merging harvests and a merge is exactly
    where a duplicate gets in. Compared on `Source.key`, which is the URL with
    the fragment dropped, because that is what the fetcher actually asks for.
    """
    keys = Counter(Source.from_dict(d).key for d in _bundled())
    dupes = {k: n for k, n in keys.items() if n > 1}
    assert not dupes, (
        f"{len(dupes)} URL(s) appear more than once, costing "
        f"{sum(n - 1 for n in dupes.values())} wasted requests a scan: "
        f"{list(dupes)[:5]}")


def test_workable_is_the_floor_and_no_other_host_can_move_it():
    """The arithmetic that decides where a saved request is worth anything.

    Per host: requests / the rate that host is ACTUALLY paced at. The run
    cannot beat the largest of those. If this ever stops naming
    apply.workable.com, the advice in this file's docstring has expired and
    the next optimisation belongs somewhere else.

    The rate comes from a real `HostLimiter`, not from reading `PER_HOST_RPS`,
    and that distinction is the whole reason this test is shaped this way.
    See `test_a_raised_rate_in_the_table_is_the_rate_the_limiter_uses`.
    """
    per_host: Counter = Counter()
    for d in _bundled():
        per_host[urlparse(Source.from_dict(d).url).netloc] += 1

    lim = fetch_mod.HostLimiter()

    def minutes(host: str) -> float:
        return per_host[host] * lim.gap_for(host) / 60.0

    ranked = sorted(per_host, key=minutes, reverse=True)
    floor, second = ranked[0], ranked[1]
    assert floor == "apply.workable.com", (
        f"the floor is now {floor} at {minutes(floor):.1f} min, not "
        f"apply.workable.com")
    # The size of the prize, stated rather than implied: emptying the Workable
    # queue entirely buys the difference between these two and not a second
    # more, because the next host's queue is then the floor.
    assert minutes(floor) > minutes(second), (
        f"{floor} {minutes(floor):.1f} min vs {second} {minutes(second):.1f} min")


def test_the_floor_hosts_rate_is_the_one_the_table_states():
    """The one rate that must never drift, read from the limiter not the table.

    A test that recomputes `gap_for`'s own `min()` by hand agrees with the
    implementation by construction and can only ever pass. This asks the
    limiter what it will actually do.

    Scoped to the overrides that sit BELOW `DEFAULT_PER_HOST_RPS`, which is
    every override that currently governs anything. Overrides ABOVE the
    default do not survive `gap_for`: it takes `min(self.rps, override)` and
    `self.rps` is the default, so `boards-api.greenhouse.io` and
    `api.ashbyhq.com` at 5.0 and `api.smartrecruiters.com` at 8.0 are all
    still paced at 3.0. Measured, not inferred:

        host                      table   effective
        apply.workable.com          0.7        0.70
        boards-api.greenhouse.io    5.0        3.00
        api.ashbyhq.com             5.0        3.00
        api.smartrecruiters.com     8.0        3.00

    That is not asserted here, because asserting it would pin the bug shut.
    It costs no wall clock either way -- all three are far under Workable's
    49.9 minutes, so the run finishes at the same minute -- but the floor
    behind Workable is 22.7 minutes and not the 13.6 the table implies, and
    anything reasoning about what a Workable reduction would buy needs the
    real number. `min` is right for a ceiling and wrong for a measurement,
    and telling the two apart needs to know whether the caller set `rps` or
    took the default.
    """
    lim = fetch_mod.HostLimiter()
    ceilings = {h: r for h, r in fetch_mod.PER_HOST_RPS.items()
                if r <= fetch_mod.DEFAULT_PER_HOST_RPS}
    assert "apply.workable.com" in ceilings, (
        "apply.workable.com is the host that sets the scan's floor; it must "
        "stay in the table below the default rate")
    for host, want in ceilings.items():
        got = 1.0 / lim.gap_for(host)
        assert abs(got - want) < 1e-9, (
            f"{host} is set to {want} req/s in PER_HOST_RPS but the limiter "
            f"paces it at {got:.2f} req/s")


def test_a_scan_asks_each_source_exactly_once():
    """`fetch_all` must not ask the same board twice in one run.

    The queue is reordered by `interleave_by_host` before it is submitted, and
    a reordering that duplicated or dropped entries would be invisible in the
    output: the run would simply cost more, or quietly miss employers.
    """
    srcs = [Source(company=f"c{i}",
                   url=f"https://h{i % 7}.example.test/api/{i}",
                   platform="greenhouse") for i in range(50)]
    asked: list[str] = []

    class Recording:
        def mount(self, prefix, adapter): pass

        def get(self, url, headers=None, timeout=None):
            asked.append(url)

            class R:
                status_code = 200
                headers = {"Content-Type": "application/json"}
                text = "[]"

                @staticmethod
                def json():
                    return []
            return R()

    import threading
    fetch_mod._local = threading.local()
    fetch_mod._local.session = Recording()

    queued = fetch_mod.interleave_by_host(list(srcs))
    assert len(queued) == len(srcs), "the reorder changed how many boards are read"
    assert {s.key for s in queued} == {s.key for s in srcs}, (
        "the reorder changed WHICH boards are read")
    counts = Counter(s.key for s in queued)
    assert not [k for k, n in counts.items() if n > 1], (
        f"a source is queued twice: {[k for k, n in counts.items() if n > 1]}")


# ------------------------------------------------- retries into a refusal

def _resp(status: int, headers_d=None, tally=None, hdrs=None):
    if tally is not None:
        tally["n"] = tally.get("n", 0) + 1

    class R:
        status_code = status
        headers = hdrs if hdrs is not None else {}
        text = ""
    return R()


def test_a_304_is_unknown_and_not_an_empty_board():
    """The trap under the obvious answer to "can we ask for less?".

    Conditional requests are the first thing anyone reaches for on a list of
    17,807 boards, and this is what happens if they are bolted on without
    reading the rest of `fetch_one`: 304 is under 400, so it fell past the
    error branch, its empty body parsed to zero postings, `ok` stayed True,
    and the board went into the run as an employer with no vacancies. That is
    the exact shape that once threw away 250 live employers, and it is what
    `validate --prune` offers to delete.

    Say the other half out loud too, because it is the answer to the question
    that sends people here: a 304 IS A REQUEST. It saves bytes and parsing. It
    cannot save one second on a host whose limit is counted in requests, and
    the host that sets this scan's floor is exactly that kind of host.
    """
    class NotModified:
        def mount(self, prefix, adapter): pass

        def get(self, url, headers=None, timeout=None):
            class R:
                status_code = 304
                headers = {}
                text = ""
                content = b""
                encoding = None
            return R()

    res = fetch_mod.fetch_one(
        Source(company="c", url="https://boards-api.greenhouse.io/v1/boards/c/jobs",
               platform="greenhouse"),
        session=NotModified(), limiter=fetch_mod.HostLimiter(rps=0))

    assert not res.ok, (
        "a 304 was accepted as a good read; its body is empty, so the board "
        "would be recorded as an employer with no vacancies")
    assert "304" in (res.error or ""), res.error


def test_retrying_a_refusal_is_charged_to_the_host_that_sets_the_floor():
    """Where the scan's time goes above its 49.9 minute floor, measured.

    Two facts about `fetch_one` combine, and neither is wrong on its own:
    every attempt claims a paced slot on the host, and every 429 widens that
    host's gap by `HOST_SLOWDOWN_STEP`. So ONE board that is refused three
    times is counted as three separate facts about apply.workable.com and
    takes its gap from 1.4s to 11.4s by itself, after which it needs sixty
    clean answers to climb back down.

    Driving the real limiter over 2,094 boards, no network, summing the gaps
    it would have made the run wait:

        429 rate      today   widen once per SOURCE   no retry at all
        none          49.9m   49.9m                   49.9m
        1 in 200     109.0m   56.1m                   55.1m
        1 in 100     162.9m   61.9m                   59.9m
        1 in 50      271.5m   73.9m                   69.9m

    The requests themselves are almost nothing: 22 extra at 1-in-200. The
    fifty-three minutes are the compounding. That makes this a PACING fault
    rather than a request-count one, which is why the fix is not in this
    change -- but the arithmetic is pinned here so the next person does not
    have to rediscover it, and so a rate change cannot quietly remove the
    property it depends on.
    """
    lim = fetch_mod.HostLimiter()
    url = "https://apply.workable.com/api/v1/widget/accounts/x"
    base = lim.gap_for("apply.workable.com")
    for _ in range(3):                      # one board, three attempts
        lim.note_throttle(url)
    assert lim.gap_for("apply.workable.com") == base * 8, (
        f"one refused board moved the host's gap from {base:.2f}s to "
        f"{lim.gap_for('apply.workable.com'):.2f}s")
    # And climbing back down is deliberately slow, which is the other half of
    # the cost: three steps at OK_RUN_TO_SPEED_UP successes each.
    for _ in range(fetch_mod.OK_RUN_TO_SPEED_UP * 3):
        lim.note_ok(url)
    assert lim.gap_for("apply.workable.com") == base


def test_a_500_is_still_retried():
    """A server error is a transient, not a refusal, and retrying it is how a
    board that blinked still gets read. This is the behaviour the 429 change
    must not take with it."""
    tally: dict = {}

    class Broken:
        def mount(self, prefix, adapter): pass

        def get(self, url, headers=None, timeout=None):
            return _resp(503, tally=tally, hdrs={})

    lim = fetch_mod.HostLimiter(rps=0)
    from unittest import mock
    with mock.patch.object(fetch_mod, "_sleep_backoff", lambda *a, **k: None):
        fetch_mod.fetch_one(
            Source(company="c", url="https://boards-api.greenhouse.io/v1/boards/c/jobs",
                   platform="greenhouse"),
            session=Broken(), limiter=lim, retries=2)
    assert tally["n"] == 3, (
        f"a 503 cost {tally['n']} attempts; it should still spend all three")


# --------------------------------------------------------- the enrich pass

def _db_with(rows):
    con = store.connect(":memory:")
    store._ensure_columns(con)
    for uid, url, platform, desc in rows:
        con.execute(
            "INSERT INTO roles (uid, company, title, url, platform, "
            "description, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
            (uid, "c", "Engineering Manager", url, platform, desc,
             "2026-08-27", "2026-08-27"))
    con.commit()
    return con


def test_enrichment_does_not_re_read_a_posting_whose_advert_is_stored():
    """The pass must never spend a request on text it already has.

    This is the cheap half of the question and it was already right: the
    candidate query carries a length floor, so a role with a full advert is
    not selected at all. Pinned because the floor is assembled by string
    concatenation in `_floor_sql`, which is one typo away from selecting
    everything.
    """
    con = _db_with([
        ("full", "https://boards.eu.icims.com/jobs/101/em/job", "icims",
         "A" * 4000),
        ("empty", "https://boards.eu.icims.com/jobs/102/em/job", "icims",
         ""),
    ])
    uids = {r["uid"] for r in enrich.candidates(con)}
    assert "full" not in uids, "a stored advert was queued for re-fetching"
    assert "empty" in uids, "a role with no advert was not queued"


def test_enrichment_asks_one_fetcher_when_the_first_one_answers():
    """`fetcher_for` returns up to two fetchers and `_try` stops at the first
    that returns text. A chain that always ran to the end would double this
    pass's request count for every role it can already read."""
    calls: list[str] = []

    def good(url, session=None, timeout=20):
        calls.append("good")
        return "A" * 500

    def never(url, session=None, timeout=20):
        calls.append("never")
        return ""

    rows = [{"uid": "u", "url": "https://x.test/j/1", "platform": "p",
             "desc_len": 0}]

    class Row(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    from unittest import mock
    with mock.patch.object(enrich, "fetcher_for", lambda u, p="": [good, never]):
        out = list(enrich._texts([Row(rows[0])], pause=0, concurrency=1))
    assert calls == ["good"], f"the chain ran on past its answer: {calls}"
    assert out[0][2] == "A" * 500


def _main() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except Exception as e:
                print(f"  FAIL  {name}: {type(e).__name__}: {e}")
                fails += 1
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_main())


def test_a_boards_retries_widen_the_host_once_between_them():
    """A board that is refused, retried and refused again is one fact about
    the host. Counting it three times compounds, because the step is 2x and
    the ceiling 8x: a single unlucky board on its third attempt takes
    apply.workable.com from a 1.43 second gap to 11.43 by itself, and then
    needs sixty clean answers to climb back.

    The cost is the compounding rather than the count. Simulated over 2,094
    boards by driving the real limiter with no network and summing the gaps,
    with a refused board's retries failing 80% of the time, which is what a
    host that is refusing actually does: one refusal in two hundred is 135
    minutes counting every attempt, and 66 counting once per source.
    """
    import inspect
    import re

    from jobradar import fetch as fetch_mod

    src = inspect.getsource(fetch_mod.fetch_one)
    call = re.search(r"if r\.status_code == 429 and lim is not None[^\n]*", src)
    assert call, "the throttle notice moved; check it is still once per source"
    assert "attempt == 0" in call.group(0), (
        "note_throttle is being called on every retry again, so one board can "
        "widen the host three times")

    # And the property it protects, measured rather than asserted about.
    lim = fetch_mod.HostLimiter(rps=3.0)
    url = "https://apply.workable.com/x"
    base = lim.gap_for("apply.workable.com")
    for _ in range(3):                       # one board, three attempts
        lim.note_throttle(url)
    worst = lim.gap_for("apply.workable.com")
    assert worst >= base * fetch_mod.MAX_HOST_SLOWDOWN, (
        "three notices should reach the ceiling, which is the thing to avoid")

    lim2 = fetch_mod.HostLimiter(rps=3.0)
    lim2.note_throttle(url)                  # the same board, counted once
    assert lim2.gap_for("apply.workable.com") == base * fetch_mod.HOST_SLOWDOWN_STEP


def test_a_run_says_which_hosts_refused_it():
    """`note_throttle` widened a host's gap on every 429 and nothing reported
    that it had fired. A retried 429 comes back as an ordinary 200 with
    `throttled` False, and `detect_throttling` only sees boards that returned
    nothing, so "no throttling was reported" was not the same statement as
    "no throttling happened". The multiplier was being returned so a caller
    could say so out loud, and no caller read it.

    The Workable fault one level up: a host refusing quietly while the run
    looks healthy.
    """
    import io
    import contextlib
    from unittest import mock

    from jobradar import fetch as fetch_mod
    from jobradar.models import Source

    srcs = [Source(company=f"c{i}", platform="greenhouse",
                   url=f"https://busy.invalid/{i}") for i in range(3)]

    def refusing(src, limiter, *a, **k):
        limiter.note_throttle(src.url)
        return fetch_mod.Result(src, payload={"jobs": []})

    out = io.StringIO()
    with mock.patch.object(fetch_mod, "_fetch_dispatch", refusing), \
            contextlib.redirect_stdout(out):
        fetch_mod.fetch_all(srcs, concurrency=1)
    assert "busy.invalid" in out.getvalue(), out.getvalue()
    assert "slower" in out.getvalue()

    # And a clean run stays quiet, or the line means nothing.
    def clean(src, limiter, *a, **k):
        return fetch_mod.Result(src, payload={"jobs": []})

    out = io.StringIO()
    with mock.patch.object(fetch_mod, "_fetch_dispatch", clean), \
            contextlib.redirect_stdout(out):
        fetch_mod.fetch_all(srcs, concurrency=1)
    assert "slower" not in out.getvalue(), out.getvalue()
