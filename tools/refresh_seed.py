"""Rebuild the published shard set and upload it, if it looks sane.

A seed nobody rebuilds is worse than no seed at all. Roles die in days, and a
file that says "built 2026-08-28" six months later is a list of vacancies that
have gone, presented with the same confidence as the ones that have not.

Run from a real machine on a real connection, never from GitHub Actions.
Actions runners are Azure addresses and these hosts refuse those far harder
than they refuse a home IP, so a build there would be mostly refusals and
would publish a seed with the roles missing rather than a seed that failed.

**The gate is the point.** This is unattended and it writes to a public
release, so the failure to design against is not "the build crashes", which is
loud, but "the build half works and publishes anyway". A short seed is not
visibly broken: it is a seed with fewer jobs in it, and the jobs that fell off
look exactly like jobs that do not exist. So the new build is compared with
what is already published, and anything that looks like a bad day is kept off
the release and reported instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "maccydee/job-radar"
TAG = "seed-latest"
BASE = f"https://github.com/{REPO}/releases/download/{TAG}"

# How much smaller than the published one a new build may be before it is
# treated as a bad run rather than a quiet market. A fifth is far wider than
# any real week and far narrower than a partial fetch: the failure this is
# built against lost most of a platform, not a tenth of one.
MIN_FRACTION = 0.80

# And a floor that does not depend on there being anything published to
# compare with, for the first run and for the case where the published index
# cannot be read.
MIN_ROLES = 150_000


def _published_index() -> dict | None:
    """The index currently on the release, or None if there is not one."""
    try:
        req = urllib.request.Request(
            f"{BASE}/index.json",
            headers={"User-Agent": "job-radar seed refresh"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _roles(idx: dict | None) -> int:
    if not idx:
        return 0
    return sum(v.get("roles", 0) for v in (idx.get("shards") or {}).values())


def check(new: dict, old: dict | None) -> list[str]:
    """Everything wrong with this build. Empty means publish it."""
    problems = []
    fresh, prev = _roles(new), _roles(old)

    if fresh < MIN_ROLES:
        problems.append(
            f"{fresh:,} roles is below the floor of {MIN_ROLES:,}. A build "
            f"this short is a fetch that went wrong, not a quiet week.")
    if prev and fresh < prev * MIN_FRACTION:
        problems.append(
            f"{fresh:,} roles against {prev:,} published, which is "
            f"{100 * fresh / prev:.0f}% of it. Anything under "
            f"{100 * MIN_FRACTION:.0f}% is treated as a bad run.")

    # The shards everybody downloads. A seed missing either of these hides
    # every role open in more than one country, or every role whose country
    # could not be read, from every reader at once.
    for name in ("unplaced", "multiple"):
        if not (new.get("shards") or {}).get(name):
            problems.append(f"no {name} shard, and every reader takes that one")

    # A platform that vanished entirely. Losing one is the shape of failure a
    # role count alone can hide: Workable is 7% of the roles and 60% of the
    # runtime, so a build that lost all of it still counts as 93% of a good
    # one and sails past the fraction check above.
    if old:
        gone = sorted(set(old.get("shards") or {}) - set(new.get("shards") or {}))
        big = [n for n in gone if (old["shards"][n].get("roles", 0)) >= 500]
        if big:
            problems.append(f"shards that had 500+ roles last time are absent "
                            f"now: {', '.join(big)}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path.home() / "job-radar" / "seed-build"))
    ap.add_argument("--dry-run", action="store_true",
                    help="build and check, upload nothing")
    ap.add_argument("--force", action="store_true",
                    help="upload even if the checks fail. For a genuine "
                         "market change, never for an unattended run.")
    args = ap.parse_args(argv)

    out = Path(args.out)
    # Built beside the old one and only swapped in at the end, so a failed
    # build never leaves a half set where the good one was.
    staging = out.with_name(out.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)

    print("building", flush=True)
    r = subprocess.run([sys.executable, "-m", "jobradar.cli", "seed", "build",
                        "--out", str(staging)],
                       cwd=str(Path(__file__).resolve().parent.parent))
    if r.returncode != 0:
        print(f"build failed with {r.returncode}, publishing nothing")
        return 1

    try:
        new = json.loads((staging / "index.json").read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"build wrote no index ({exc}), publishing nothing")
        return 1

    problems = check(new, _published_index())
    print(f"\n{_roles(new):,} roles in {len(new.get('shards') or {})} shards")
    for p in problems:
        print(f"  REFUSED: {p}")
    if problems and not args.force:
        print("\nNothing was uploaded. The build is in "
              f"{staging} if you want to look at it.")
        return 1
    if args.dry_run:
        print("\nDry run, so nothing was uploaded.")
        return 0

    print("\nuploading", flush=True)
    names = ["index.json"] + sorted(p.name for p in staging.glob("*.jsonl.gz"))
    r = subprocess.run(["gh", "release", "upload", TAG, *names, "--clobber"],
                       cwd=str(staging))
    if r.returncode != 0:
        print(f"upload failed with {r.returncode}")
        return 1

    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    print(f"published {_roles(new):,} roles to {BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
