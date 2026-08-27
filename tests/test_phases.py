"""Reading in passes, fastest first, and saying how long each will take.

A scan reads 17,807 boards and takes the better part of an hour. Half the
roles are on hosts nobody rate limits and arrive in about five minutes; the
last 6% sit behind Workable and cost fifty. Read as one interleaved lump none
of it is usable until all of it is, and the way people actually use this is to
set it up, look at the dashboard, apply to something and shut the laptop.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import sources as src_mod
from jobradar.cli import _mins, _phase_minutes
from jobradar.models import Source


def _s(url: str) -> Source:
    return Source(company="c", platform="x", url=url)


def test_every_source_lands_in_exactly_one_pass():
    """A source that falls through the phase table is a board nobody reads,
    and it would not show up as an error anywhere: the scan would simply
    return fewer roles and look like it worked."""
    srcs = [_s("https://api.ashbyhq.com/posting-api/job-board/a"),
            _s("https://boards-api.greenhouse.io/v1/boards/b/jobs"),
            _s("https://apply.workable.com/api/v1/widget/accounts/c"),
            _s("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/x/jobs"),
            _s("https://careers-acme.icims.com/jobs/search")]
    grouped = src_mod.in_phases(srcs)
    seen = [s for _, _, group in grouped for s in group]
    assert len(seen) == len(srcs), "a source fell out of the phase table"
    assert {id(s) for s in seen} == {id(s) for s in srcs}


def test_the_slow_host_is_read_last_and_the_unpaced_ones_first():
    srcs = [_s("https://apply.workable.com/api/v1/widget/accounts/c"),
            _s("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/x/jobs"),
            _s("https://boards-api.greenhouse.io/v1/boards/b/jobs")]
    order = [label for _, label, _ in src_mod.in_phases(srcs)]
    assert order[0] == "the fast ones"
    assert order[-1] == "Workable's own boards"


def test_a_one_board_host_is_never_treated_as_a_shared_one():
    """Workday puts its 1,489 boards on 1,467 different hostnames and iCIMS
    gives every customer their own, so neither is paced by anything. Filing
    them behind a shared host would put half the roles in the slow pass."""
    assert src_mod.phase_of(
        _s("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/x/jobs")) == 1
    assert src_mod.phase_of(_s("https://careers-x.icims.com/jobs/search")) == 1
    assert src_mod.phase_of(
        _s("https://jobs.workable.com/api/v1/jobs?day_range=7")) == 1


def test_every_phase_runs_on_every_scan():
    """A posting can appear and be filled inside a week, so a pass that is
    skipped is a role nobody ever saw. The phases decide the order, not what
    gets read. Asserted because "skip the slow one" is the obvious next idea
    and it is the wrong one.
    """
    import inspect

    from jobradar import cli
    body = inspect.getsource(cli.cmd_scan)
    start = body.index("phases = src_mod.in_phases")
    loop = body[start:start + 2500]
    assert "for n, label, group, mins in est" in loop
    # No filtering of the pass list between building it and reading it.
    assert "est[" not in loop and "est[:1]" not in loop


def test_the_estimate_is_derived_from_the_rate_not_written_down():
    """The old estimate was a constant that said forty minutes while the next
    line of output said something else. Raising a host's rate has to move it
    without anyone remembering to edit a number."""
    from jobradar.fetch import DEFAULT_PER_HOST_RPS, PER_HOST_RPS

    workable = [_s(f"https://apply.workable.com/api/v1/widget/accounts/{i}")
                for i in range(2094)]
    mins = _phase_minutes(workable)
    assert abs(mins - 2094 / PER_HOST_RPS["apply.workable.com"] / 60) < 0.01
    assert mins > 45, "Workable's pass should be the fifty minute one"

    ashby = [_s(f"https://api.ashbyhq.com/posting-api/job-board/{i}")
             for i in range(2607)]
    assert _phase_minutes(ashby) < mins / 3


def test_a_duration_a_person_can_act_on():
    """"about 0 minutes" is what a plain format prints for anything under
    thirty seconds, and it reads as a bug rather than as fast."""
    assert _mins(0.2) == "under a minute"
    assert _mins(1) == "about 1 minute"
    assert _mins(14) == "about 14 minutes"
    assert "hours" in _mins(120)
