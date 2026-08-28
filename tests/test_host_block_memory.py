"""A host that said "not for 23 hours" was asked again by the next process.

`_blocked_until` is monotonic and lives on a per-process `HostLimiter`, so
the careful handling inside a run was undone by the run ending. Observed on
apply.workable.com, the one host here whose limit is a long-window quota
rather than a rate, answering

    rate limited (HTTP 429, host blocked for another 82613s)

to a `validate` that had done nothing to deserve it: an earlier process had
tripped the block, and every process since had re-asked and been refused.
That is impolite to somebody else's server and it wastes the run.

Only long blocks are remembered. The circuit breaker's own short block is a
within-run measure, and persisting it would hold a host shut over a transient
wobble, which is the opposite failure.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.fetch import HostLimiter  # noqa: E402

URL = "https://apply.workable.com/api/v1/widget/accounts/acme"
LONG = 82613.0


def _path():
    return Path(tempfile.mkdtemp()) / "state" / "host-blocks.json"


def test_a_long_block_survives_into_the_next_process():
    p = _path()
    first = HostLimiter()
    first.remember_blocks(p)
    first.block(URL, LONG)

    second = HostLimiter()          # a fresh process would look like this
    assert second.blocked_for(URL) == 0.0, "fixture is not proving anything"
    second.remember_blocks(p)
    left = second.blocked_for(URL)
    assert left > LONG - 60, f"only {left}s remembered of {LONG}"


def test_a_short_block_is_not_written_down():
    """The breaker's own block is a within-run measure. Remembering it would
    keep a host shut over a wobble that had already passed."""
    p = _path()
    lim = HostLimiter()
    lim.remember_blocks(p)
    lim.block(URL, 60)
    assert not p.exists() or json.loads(p.read_text(encoding="utf-8")) == {}


def test_a_block_that_has_expired_is_not_honoured():
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"apply.workable.com": time.time() - 10}),
                 encoding="utf-8")
    lim = HostLimiter()
    lim.remember_blocks(p)
    assert lim.blocked_for(URL) == 0.0


def test_an_absurd_entry_is_dropped_rather_than_obeyed():
    """Wall clock can move: a laptop that slept, a clock that synced. An entry
    claiming a week is a clock problem, not a host that means it."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"apply.workable.com": time.time() + 7 * 86400}),
                 encoding="utf-8")
    lim = HostLimiter()
    lim.remember_blocks(p)
    assert lim.blocked_for(URL) == 0.0


def test_unreadable_or_missing_state_never_stops_a_scan():
    """A host block that cannot be read is a reason to be careful, not a
    reason to refuse to run. The host will say no again if it means it."""
    for content in (None, "{ not json", json.dumps(["a", "list"]),
                    json.dumps({"apply.workable.com": "soon"})):
        p = _path()
        if content is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        lim = HostLimiter()
        lim.remember_blocks(p)          # must not raise
        assert lim.blocked_for(URL) == 0.0


def test_nothing_is_written_when_no_path_was_given():
    """A test, a benchmark or a hand probe has no business writing into
    anybody's state directory.

    The body used to assert only `blocked_for(URL) > 0`, which is the in-
    process half and is already covered twice in this file. It never looked
    at a filesystem, so it could not fail if `block` started writing to a
    path of its own choosing, which is the fault the name promises to guard.
    """
    import os

    d = _path().parent
    d.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in d.iterdir()}
    cwd_before = {p.name for p in Path(os.getcwd()).iterdir()}

    lim = HostLimiter()                    # no remember_blocks, so no path
    lim.block(URL, LONG)

    assert lim.blocked_for(URL) > 0, "the block does not hold in-process"
    assert {p.name for p in d.iterdir()} == before, (
        "a limiter given no path wrote into the state directory anyway")
    assert {p.name for p in Path(os.getcwd()).iterdir()} == cwd_before, (
        "a limiter given no path wrote into the working directory")


def test_the_longest_block_wins_rather_than_the_latest():
    p = _path()
    lim = HostLimiter()
    lim.remember_blocks(p)
    lim.block(URL, LONG)
    lim.block(URL, 1000)
    assert lim.blocked_for(URL) > LONG - 60


def test_the_scan_asks_for_this_and_puts_it_somewhere_durable():
    """The fix only exists if the scan passes a path, and a temp directory
    would lose it exactly as reliably as not writing it at all."""
    import ast
    import inspect
    import textwrap

    from jobradar import cli

    # Parsed rather than greped. This read
    #     "/tmp" not in src.split("blocks_path=")[1][:200]
    # over the raw source of cmd_scan, so a comment saying why the path is not
    # in /tmp -- which is a comment this codebase would write -- would fail it
    # on its own explanation, and the 200-character window was arbitrary.
    fn = ast.parse(textwrap.dedent(inspect.getsource(cli.cmd_scan))).body[0]
    passed = [kw.value for n in ast.walk(fn) if isinstance(n, ast.Call)
              for kw in n.keywords if kw.arg == "blocks_path"]
    assert passed, "the scan no longer remembers host blocks"
    for v in passed:
        expr = ast.unparse(v)
        assert "/tmp" not in expr and "gettempdir" not in expr \
            and "mkdtemp" not in expr, (
            f"blocks_path={expr} puts the memory somewhere that is cleared, "
            f"which loses it exactly as reliably as not writing it")
