"""A board that answers with nothing is four different events, and they were
one number.

`cmd_scan` counts a source under "responded with no postings at all" whenever
`res.ok` is true and the parse returned zero jobs. Four quite different things
arrive at that line looking identical, and only one of them is a fact about
the employer:

  * the employer really has no vacancies this week. 18 of the 3,568 bundled
    sources probed live on 2026-08-26 were this, and all 18 said so in their
    own payload -- Greenhouse `meta.total: 0`, Ashby `jobs: []`, Breezy `[]`,
    Workday `total: 0`, Oracle `TotalJobsCount: 0`, iCIMS "no jobs were
    found". Nothing is wrong and nothing should be done;
  * the board answered 200 with a page that is not a listing.
    referral-publicisgroupe.icims.com answers "Error: Login is required to
    search for jobs.", parses to zero, and `validate --prune` deletes what it
    reads as dead;
  * the payload could not be parsed at all. `adapters.parse` caught every
    exception and returned `[]` with no trace, so a holding page or a vendor
    error page reached the scan summary as an employer with no vacancies.
    `discover._parse_or_why` fixed this for `validate`, because prune deletes;
    the scan path was left silent;
  * the board answered fine and the SEARCH matched nothing. Three paged
    fetchers reported that as `ok=False, error="no pages returned"`, which
    moves the source out of the "no postings" count and into "did not
    respond" -- the run's own summary then wrong about which happened.

And the fifth event, which is not in that count and should not be: the host
refused. apply.workable.com served 176 boards at the configured 0.7 requests
a second and then refused roughly one in four for the rest of the run, 41 of
419 (9.8%) coming back unknown. Nothing slowed down, because the circuit
breaker arms on refusals in a row and these were interleaved with successes.
Repeated against the same host the same day, 250 boards single threaded at
0.7/s, 250 at 0.35/s and 150 across eight workers at 0.7/s all drew zero
refusals, so no fixed number in the table would have prevented it; only
reacting to the refusal itself does.

Every payload below is a real one, captured from a live board on 2026-08-26
and saved under tests/fixtures. Nothing here touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters, discover as disc, fetch as fetch_mod   # noqa: E402
from jobradar.adapters import platforms   # noqa: E402
from jobradar.models import Source   # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json_fixture(name: str):
    return json.loads(_fixture(name))


# --------------------------------------------------------------------------
# The boards that really are empty. Reporting these as empty is correct and
# the tool must go on doing it: a week with no vacancies is not a bug, and
# treating it as one is how a maintainer is sent hunting something that is
# not there.
# --------------------------------------------------------------------------
EMPTY_BOARDS = [
    ("greenhouse", "empty_board_greenhouse.json",
     "https://boards-api.greenhouse.io/v1/boards/cuyana/jobs"),
    ("ashby", "empty_board_ashby.json",
     "https://api.ashbyhq.com/posting-api/job-board/bitstack"),
    ("breezy", "empty_board_breezy.json",
     "https://immerse-vr.breezy.hr/json"),
    ("oracle", "empty_board_oracle.json",
     "https://fa-ewzx-saasfaprod1.fa.ocs.oraclecloud.com/hcmRestApi/resources"
     "/latest/recruitingCEJobRequisitions?finder=findReqs;siteNumber=CX_1"),
]


def test_a_board_with_no_vacancies_parses_to_zero_and_raises_nothing():
    """The honest zero. Each of these payloads states its own emptiness -- a
    total of 0, or an empty array -- and the parser must agree with it
    quietly. A parser that raised here would turn every quiet week into an
    "unreadable board" and bury the ones that really are unreadable."""
    for platform, name, url in EMPTY_BOARDS:
        src = Source(company="Somebody", platform=platform, url=url)
        jobs = adapters.parse(_json_fixture(name), src)
        assert jobs == [], f"{platform}: {len(jobs)} jobs out of an empty board"
        assert not adapters.unreadable(), (
            f"{platform}: an empty board was recorded as unreadable; "
            f"{adapters.unreadable()}")


def test_an_empty_board_is_dead_and_prunable_and_that_is_correct():
    """`validate` has to be willing to call a genuinely empty board dead, or
    the source list can never be maintained at all. This is the other half of
    the login-wall test below: the fix must not make everything unprunable."""
    adapters.clear_unreadable()
    src = Source(company="Cuyana", platform="greenhouse",
                 url="https://boards-api.greenhouse.io/v1/boards/cuyana/jobs")
    n, jobs, why = _count_with(src, {"jobs": [], "meta": {"total": 0}})
    assert (n, jobs, why) == (0, [], None), (n, jobs, why)


def _count_with(src: Source, payload):
    """`discover.count_jobs` against a payload instead of the network."""
    saved = fetch_mod.fetch_one
    fetch_mod.fetch_one = lambda s, **kw: fetch_mod.Result(
        s, payload=payload, status=200)
    try:
        return disc.count_jobs(src)
    finally:
        fetch_mod.fetch_one = saved


# --------------------------------------------------------------------------
# The board that answered and refused.
# --------------------------------------------------------------------------
def test_icims_saying_login_is_required_is_not_a_board_with_no_vacancies():
    """referral-publicisgroupe.icims.com, live, HTTP 200. Zero postings and a
    perfectly healthy-looking fetch. Before this it was reported as an
    employer with nothing open, `validate` called it dead, and dead is what
    `--prune` deletes from the shipped source list."""
    src = Source(company="Publicis Groupe", platform="icims",
                 url="https://referral-publicisgroupe.icims.com/jobs/search"
                     "?ss=1&in_iframe=1")
    page = _fixture("icims_login_required.html")
    try:
        list(platforms.parse_icims(page, src))
    except platforms.BoardUnreadable as e:
        assert "login" in str(e).lower(), (
            f"the reason has to name what the board actually said, or the "
            f"reader has to go and fetch the page themselves; got {e}")
        return
    raise AssertionError(
        "a page whose only message is 'Login is required to search for jobs' "
        "was read as a board with no vacancies")


def test_icims_saying_no_jobs_were_found_really_is_an_empty_board():
    """The other real page, from the same platform on the same day. The check
    above must not fire on this one: a board that tells us it has nothing is
    the commonest honest zero on iCIMS, four of the five zeros in a 500-board
    sweep."""
    src = Source(company="Penningtons Manches Cooper LLP", platform="icims",
                 url="https://earlycareers-penningtonslaw.icims.com/jobs/"
                     "search?ss=1&in_iframe=1")
    jobs = list(platforms.parse_icims(_fixture("icims_no_jobs_found.html"), src))
    assert jobs == [], jobs


def test_the_cookie_and_geolocation_boxes_do_not_make_a_board_unreadable():
    """Both fixtures carry iCIMS' `iCIMS_NoCookies` box, and 192 of 200 boards
    WITH live postings carry it too. Keying the check on `iCIMS_ErrorMessage`
    rather than on the one div that states a page-level verdict would have
    called nearly all 1,744 iCIMS boards unreadable."""
    for name in ("icims_no_jobs_found.html", "icims_login_required.html"):
        page = _fixture(name)
        assert "iCIMS_NoCookies" in page, (
            f"{name} no longer carries the boilerplate this test exists to "
            f"prove is ignored")
    # The empty one parses quietly despite carrying the same boxes.
    src = Source(company="x", platform="icims",
                 url="https://x.icims.com/jobs/search?ss=1&in_iframe=1")
    assert list(platforms.parse_icims(
        _fixture("icims_no_jobs_found.html"), src)) == []


# --------------------------------------------------------------------------
# The payload nothing could read.
# --------------------------------------------------------------------------
def test_a_payload_the_parser_cannot_read_is_recorded_rather_than_swallowed():
    """`adapters.parse` returning `[]` is right -- one bad board must not end
    a run over 17,810 sources -- and returning it silently is not. Eight of
    the JSON parsers here do `(payload or {}).get(...)`, so a board answering
    HTTP 200 with a page instead of its JSON raises `AttributeError` and used
    to arrive at the scan summary as an employer with no vacancies."""
    adapters.clear_unreadable()
    src = Source(company="Somebody", platform="workable",
                 url="https://apply.workable.com/api/v1/widget/accounts/x")
    # A real error page, from a live board, handed to a JSON parser.
    jobs = adapters.parse(_fixture("icims_login_required.html"), src)
    assert jobs == [], "nothing may be invented out of an unreadable payload"

    recorded = adapters.unreadable()
    assert len(recorded) == 1, (
        f"the failure has to be recorded, or a scan cannot tell this zero "
        f"from an empty board; got {recorded}")
    company, platform, why = recorded[0]
    assert company == "Somebody" and platform == "workable", recorded[0]
    assert "AttributeError" in why, why
    adapters.clear_unreadable()


def test_validate_calls_an_unreadable_payload_unreachable_and_never_prunes_it():
    """The same payload through the other command. `validate --prune` runs
    unattended every Sunday, and "dead" is the verdict it deletes on."""
    src = Source(company="Somebody", platform="workable",
                 url="https://apply.workable.com/api/v1/widget/accounts/x")
    page = _fixture("icims_login_required.html")

    saved = fetch_mod.fetch_one
    fetch_mod.fetch_one = lambda s, **kw: fetch_mod.Result(
        s, payload=page, status=200)
    try:
        row = disc.validate_source(src)
    finally:
        fetch_mod.fetch_one = saved

    assert row["verdict"] == "unreachable", row
    assert row["prunable"] is False, (
        "a board whose answer could not be read is not evidence the employer "
        f"stopped hiring; {row}")


# --------------------------------------------------------------------------
# The search that matched nothing.
# --------------------------------------------------------------------------
class _Page:
    """A session that answers every request with one saved page."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, 0

    def mount(self, prefix, adapter):
        pass

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        body, status = self.body, self.status

        class R:
            status_code = status
            headers = {"Content-Type": "text/html"}
            text = body
            content = body.encode("utf-8")
            encoding = "utf-8"

        return R()


def test_a_search_that_matched_nothing_is_an_empty_board_not_a_failed_fetch():
    """Orano's Avature board, searched live for a title it has no vacancy for:
    HTTP 200, a real results page, not one posting link on it. `fetch_avature`
    returned `ok=False, error="no pages returned"`, so `cmd_scan` counted the
    source under "did not respond" -- and said nothing at all about a board
    that had answered it."""
    src = Source(company="Orano", platform="avature",
                 url="https://nova.orano.group/fr_FR/examplePathName/"
                     "SearchJobs/?jobRecordsPerPage=50")
    session = _Page(_fixture("avature_no_match.html"))
    saved = fetch_mod._thread_session
    fetch_mod._thread_session = lambda: session
    try:
        res = fetch_mod.fetch_avature(src, ["director of engineering"],
                                      retries=0)
    finally:
        fetch_mod._thread_session = saved

    assert res.ok, (
        f"the board answered HTTP 200 with a results page; reporting that as "
        f"a failed fetch is the wrong half of the summary. error={res.error!r}")
    assert adapters.parse(res.payload, src) == []
    assert res.transport is None and not res.throttled


def test_a_board_that_never_answered_is_still_a_failure():
    """The other direction, and the one that must not regress: an empty
    payload is only honest when a page really came back. A board behind a
    dead host must keep reporting a failure, because `validate` turns a
    failure into "unreachable" and anything else into a deletion."""
    src = Source(company="Orano", platform="avature",
                 url="https://nova.orano.group/fr_FR/examplePathName/"
                     "SearchJobs/?jobRecordsPerPage=50")

    class Refusing:
        def mount(self, prefix, adapter):
            pass

        def get(self, url, headers=None, timeout=None):
            class R:
                status_code = 503
                headers = {}
                text = ""
            return R()

    saved_session, saved_sleep = fetch_mod._thread_session, fetch_mod._sleep_backoff
    fetch_mod._thread_session = lambda: Refusing()
    fetch_mod._sleep_backoff = lambda *a, **k: None
    try:
        res = fetch_mod.fetch_avature(src, [""], retries=0)
    finally:
        fetch_mod._thread_session = saved_session
        fetch_mod._sleep_backoff = saved_sleep

    assert not res.ok, "a 503 is not an empty board"
    assert res.status == 503, res


def test_no_paged_fetcher_still_says_no_pages_returned():
    """The string said two things at once and got one of them wrong. It is
    gone from all three fetchers that used it; `_no_rows` is what they call
    instead, and it decides between the two answers rather than merging
    them."""
    # Read as an AST, not as text. This greped the raw file for
    # 'error="no pages returned"', and fetch.py already contains the phrase
    # "no pages returned" in a comment explaining why it went; the test
    # escaped only because the comment omits the `error=` prefix. Move that
    # sentence one word and it fails on its own explanation, which is the
    # thing CLAUDE.md names three previous instances of.
    #
    # ast.unparse cannot emit comments and docstrings are dropped explicitly,
    # so what is searched here is code and nothing else.
    import ast

    tree = ast.parse(Path(fetch_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)

    assert "no pages returned" not in code, (
        "a board that answered and a board that never did are still being "
        "reported with the same string")
    callers = [n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                       and c.func.id == "_no_rows" for c in ast.walk(n))]
    assert sorted(callers) == ["fetch_avature", "fetch_nhs", "fetch_rmk"], (
        "fetch_avature, fetch_rmk and fetch_nhs should all go through the "
        f"same decision, and these do: {sorted(callers)}")


# --------------------------------------------------------------------------
# The host that refused. Not an empty board, and the pacing has to notice.
# --------------------------------------------------------------------------
def test_a_host_that_refuses_one_request_in_four_is_slowed_down():
    """Measured on apply.workable.com, 2026-08-26: 176 boards served at the
    configured 0.7 requests a second, then 41 of the next 243 refused. The
    refusals never came three in a row, so `note_refusal` -- which only fires
    once a source has spent every retry -- never armed the breaker, and the
    rate they were a complaint about never changed."""
    lim = fetch_mod.HostLimiter(rps=3.0)
    workable = "https://apply.workable.com/api/v1/widget/accounts/x"
    before = lim.gap_for("apply.workable.com")

    lim.note_throttle(workable)
    after = lim.gap_for("apply.workable.com")
    assert after > before, (
        f"a 429 left the host's pacing exactly as it was: {before} -> {after}")
    assert after == before * fetch_mod.HOST_SLOWDOWN_STEP

    # And it does not run away. A bad afternoon must not turn into a scan
    # that never finishes.
    for _ in range(20):
        lim.note_throttle(workable)
    assert lim.slowdown_for(workable) == fetch_mod.MAX_HOST_SLOWDOWN


def test_slowing_one_host_down_leaves_every_other_host_alone():
    """7,749 of the 7,781 hosts in the bundled list carry a single board. One
    busy API host must not slow all of them down; that is the whole reason
    the pacing is per host."""
    lim = fetch_mod.HostLimiter(rps=3.0)
    lim.note_throttle("https://apply.workable.com/api/v1/widget/accounts/x")
    assert lim.gap_for("boards-api.greenhouse.io") == 1 / 3.0
    assert lim.slowdown_for("https://careers.example.com/jobs") == 1.0


def test_a_host_that_recovers_earns_its_rate_back():
    """A permanent penalty for one momentary refusal is its own fault: it
    would cost the remaining seventeen thousand sources of the scan."""
    lim = fetch_mod.HostLimiter(rps=3.0)
    url = "https://api.example.com/jobs"
    lim.note_throttle(url)
    lim.note_throttle(url)
    assert lim.slowdown_for(url) == fetch_mod.HOST_SLOWDOWN_STEP ** 2

    for _ in range(fetch_mod.OK_RUN_TO_SPEED_UP):
        lim.note_ok(url)
    assert lim.slowdown_for(url) == fetch_mod.HOST_SLOWDOWN_STEP, (
        "a run of clean answers should buy back one step, not all of them: "
        "recovering faster than it slowed is how a host ends up oscillating "
        "around the rate that refuses")

    for _ in range(fetch_mod.OK_RUN_TO_SPEED_UP):
        lim.note_ok(url)
    assert lim.slowdown_for(url) == 1.0


def test_a_scattered_429_slows_the_host_without_shutting_it():
    """The breaker's own rule stays: a host serving between 429s is busy, not
    shut, and blocking it would silently skip boards that would have answered.
    Slowing down is the answer that keeps every board reachable."""
    from unittest import mock

    seq = iter([429, 200, 429, 200, 429, 200])

    class Flaky:
        def mount(self, prefix, adapter):
            pass

        def get(self, url, headers=None, timeout=None):
            code = next(seq)

            class R:
                status_code = code
                headers = {"Content-Type": "application/json"}
                text = "[]"

                @staticmethod
                def json():
                    return []
            return R()

    lim = fetch_mod.HostLimiter(rps=0)     # no real sleeping in a test
    with mock.patch.object(fetch_mod, "_sleep_backoff", lambda *a, **k: None):
        for i in range(3):
            res = fetch_mod.fetch_one(
                Source(company=f"c{i}", platform="workable",
                       url=f"https://busy.example.com/{i}"),
                session=Flaky(), limiter=lim, retries=1)
            assert res.ok, "a source that answered on its retry is fine"

    assert lim.blocked_for("https://busy.example.com/x") == 0, (
        "a host that keeps answering must not be shut out")
    assert lim.slowdown_for("https://busy.example.com/x") > 1.0, (
        "three 429s went past without the pacing changing at all, which is "
        "the state that let 41 of 419 Workable boards come back unknown")


def test_a_throttled_board_is_never_recorded_as_having_no_jobs():
    """The oldest fault in this file, pinned again from the other end: 41 of
    the 419 Workable boards probed came back 429, and if a 429 reached the
    adapter as an empty array those employers would be deleted as dead."""
    src = Source(company="Clinigen", platform="workable",
                 url="https://apply.workable.com/api/v1/widget/accounts/clinigen")
    saved = fetch_mod.fetch_one
    fetch_mod.fetch_one = lambda s, **kw: fetch_mod.Result(
        s, error="HTTP 429", status=429, throttled=True)
    try:
        row = disc.validate_source(src)
    finally:
        fetch_mod.fetch_one = saved

    assert row["verdict"] == "unreachable", row
    assert row["prunable"] is False, row
    assert "rate limited" in row["note"], row["note"]


# --------------------------------------------------------------------------
# The one that is not a board problem at all.
# --------------------------------------------------------------------------
def test_a_tls_handshake_failure_is_this_machines_fault_and_says_so():
    """Roke's board is live -- `curl` gets HTTP 200 from it -- and this
    machine's Python is linked against LibreSSL 2.8.3, which cannot complete
    the handshake their host requires. It is the one source in 3,568 that
    failed below HTTP, it will fail every week, and it must never be read as
    a board that has gone away."""
    import requests

    alert = fetch_mod.handshake_failure(
        requests.exceptions.SSLError(
            "SSLError(SSLError(1, '[SSL: TLSV1_ALERT_PROTOCOL_VERSION] "
            "tlsv1 alert protocol version (_ssl.c:1000)'))"))
    assert alert == "TLSV1_ALERT_PROTOCOL_VERSION", alert

    alerts: list = []
    src = Source(company="Roke", platform="custom",
                 url="https://www.roke.co.uk/wp-json/wp/v2/job?per_page=100")
    saved = fetch_mod.fetch_one
    fetch_mod.fetch_one = lambda s, **kw: fetch_mod.Result(
        s, error="TLS handshake failed (TLSV1_ALERT_PROTOCOL_VERSION): the "
                 "board was never reached", transport=alert)
    try:
        row = disc.validate_source(src)
    finally:
        fetch_mod.fetch_one = saved

    assert row["verdict"] == "unreachable", row
    assert row["transport"] == alert, row
    assert row["prunable"] is False, (
        "a handshake this machine cannot complete is a fact about this "
        f"machine, and it must never delete a live employer; {row}")
    assert disc.prunable(row) is False


def _tests():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"  pass  {name}")
        except Exception as exc:      # noqa: BLE001 - a runner reports, not raises
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"{len(_tests()) - failed}/{len(_tests())} passed")
    sys.exit(1 if failed else 0)
