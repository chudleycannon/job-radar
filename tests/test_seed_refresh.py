"""The gate on the unattended seed rebuild.

This runs on a schedule and writes to a public release, so the failure to
design against is not "the build crashes", which is loud and stops there. It
is "the build half works and publishes anyway". A short seed is not visibly
broken: it is a seed with fewer jobs in it, and the jobs that fell off look
exactly like jobs that do not exist. Nobody downloading it would ever know.

So the checks are about the shapes a partial fetch takes, not about whether
the command exited zero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import refresh_seed as rs  # noqa: E402


def _idx(**shards):
    return {"schema": 1, "shards": {n: {"roles": r, "bytes": r * 900}
                                    for n, r in shards.items()}}


GOOD = _idx(US=151_000, UK=19_700, unplaced=16_200, multiple=5_000,
            CA=9_100, IN=7_500, DE=6_200)


def test_a_healthy_rebuild_publishes():
    assert rs.check(GOOD, GOOD) == []


def test_a_build_that_lost_most_of_its_roles_is_refused():
    small = _idx(US=40_000, UK=5_000, unplaced=4_000, multiple=1_000)
    assert rs.check(small, GOOD), "a build a third the size was published"


def test_a_first_run_with_nothing_published_still_has_a_floor():
    """There is nothing to compare against, and "no previous build" must not
    mean "anything goes"."""
    assert rs.check(_idx(US=900, unplaced=10, multiple=5), None)
    assert rs.check(GOOD, None) == []


def test_losing_a_whole_platform_is_caught_even_though_the_count_survives():
    """The one a role count alone cannot see.

    Workable is about 7% of the roles and 60% of the runtime, so a build that
    fetched none of it is still 93% of a good one and sails past any
    percentage check. What gives it away is that shards which had roles last
    time have none now.
    """
    lost = _idx(US=151_000, UK=19_700, unplaced=16_200, multiple=5_000,
                CA=9_100, IN=7_500)          # DE gone entirely
    problems = rs.check(lost, GOOD)
    assert any("absent" in p for p in problems), problems


def test_a_missing_everybody_shard_is_refused():
    """`unplaced` and `multiple` go to every reader, so losing either hides
    those roles from everyone at once rather than from one country."""
    for drop in ("unplaced", "multiple"):
        shards = {k: v["roles"] for k, v in GOOD["shards"].items() if k != drop}
        problems = rs.check(_idx(**shards), GOOD)
        assert any(drop in p for p in problems), (drop, problems)


def test_a_normal_weeks_movement_is_not_a_failure():
    """The threshold has to be wider than the market and narrower than a
    broken fetch, or the guard becomes something people switch off."""
    for factor in (0.92, 0.97, 1.0, 1.15):
        moved = _idx(**{k: int(v["roles"] * factor)
                        for k, v in GOOD["shards"].items()})
        assert rs.check(moved, GOOD) == [], factor


def test_the_checks_run_before_anything_is_uploaded():
    import inspect
    src = inspect.getsource(rs.main)
    assert src.index("problems = check(") < src.index("gh"), \
        "the upload can happen before the build is checked"
    assert "staging" in src, "a failed build overwrites the good one in place"
