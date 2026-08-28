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

# And a ceiling. A build twice the size is not a good week either: the shape
# it catches is every role emitted twice, which is 200% and reads as success.
MAX_FRACTION = 1.50


class Unknown(Exception):
    """The published index could not be read. Not the same as "none"."""


def _published_index() -> dict | None:
    """The index on the release. None if there is genuinely none.

    Raises `Unknown` if it could not be read, which is a different answer and
    was being collapsed into the same one. `check()` treats "nothing to
    compare with" as permission to skip its only comparative test, so a
    single flaky HTTPS request turned the gate off: the same 159,220-role
    build was REFUSED when the index loaded, at 55% of what is published, and
    PUBLISHED when it did not. One dropped connection and the guard becomes a
    floor of 150,000, which is 52% of the current set.

    This project's own note says a value meaning "we cannot say" must never be
    read as "no", and this file said it too, one function further down.
    """
    req = urllib.request.Request(
        f"{BASE}/index.json",
        headers={"User-Agent": "job-radar seed refresh"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None          # genuinely nothing published yet
        raise Unknown(f"the published index answered HTTP {exc.code}") from None
    except Exception as exc:
        raise Unknown(f"the published index could not be read ({exc})") from None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise Unknown(f"the published index is not readable JSON ({exc})") from None


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

    if prev and fresh > prev * MAX_FRACTION:
        # No upper bound at all meant a build that emitted every role twice,
        # 579,280 of them, sailed through as a very good week.
        problems.append(
            f"{fresh:,} roles against {prev:,} published, which is "
            f"{100 * fresh / prev:.0f}% of it. Anything over "
            f"{100 * MAX_FRACTION:.0f}% is treated as a bad run too.")

    # A shard that collapsed without disappearing. Only a vanished shard was
    # caught, so DE going from 6,261 roles to 3 published without a word.
    if old:
        for name, was in (old.get("shards") or {}).items():
            now = (new.get("shards") or {}).get(name)
            if not now or was.get("roles", 0) < 500:
                continue
            if now.get("roles", 0) < was["roles"] * MIN_FRACTION:
                problems.append(
                    f"{name} went from {was['roles']:,} roles to "
                    f"{now.get('roles', 0):,}")

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


def _shards_look_read(out: Path) -> list[str]:
    """Open a shard and look at a role. Nothing else here ever does.

    Every other check reads the index, and the index is written by
    `Writer.finish` from its own counters, so it agrees with itself whatever
    happened. A build of 289,640 rows with every description empty passes all
    of them, and descriptions are the whole reason the seed carries adverts
    rather than links.

    Deliberately a sample and a low bar. This is a gate against a broken
    build, not a quality score, and a real advert is many hundreds of
    characters: anything that reads like adverts at all clears it easily.
    """
    import gzip
    problems = []
    for name in ("unplaced", "multiple"):
        path = out / f"{name}.jsonl.gz"
        if not path.exists():
            continue                      # already reported by `check`
        rows, described, located = 0, 0, 0
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            fh.readline()                 # header
            for line in fh:
                if rows >= 400:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    problems.append(f"{path.name} has a row that is not JSON")
                    break
                rows += 1
                if len((row.get("d") or "").strip()) >= 200:
                    described += 1
                if (row.get("l") or "").strip():
                    located += 1
        if rows and described < rows * 0.5:
            problems.append(
                f"{path.name}: only {described} of {rows} sampled roles carry "
                f"an advert. Without one a role cannot be scored, screened or "
                f"written from, which is the whole reason the seed is 242MB "
                f"rather than 20MB.")
        if rows and located < rows * 0.5:
            problems.append(
                f"{path.name}: only {located} of {rows} sampled roles have a "
                f"location")
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

    try:
        published = _published_index()
    except Unknown as exc:
        # Refusing rather than proceeding without the comparison. The gate's
        # only test against reality is the published set, and running without
        # it is running with the guard off. A week-old seed is a small cost;
        # publishing a half-built one over a good one is not.
        print(f"{exc}, so this build cannot be compared with what is live. "
              f"Publishing nothing; it will try again next week.")
        return 1

    problems = check(new, published) + _shards_look_read(staging)
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

    # Shards first, index LAST. Uploading the index first meant that for the
    # length of a 165-asset, 242MB upload the release carried a new index
    # over old shards, and a failure partway through left it that way until
    # the next run. Every reader in between would fetch an index promising
    # roles the shards beside it do not have. `seed.fetch` writes its own
    # index last for exactly this reason; the publisher was doing the
    # opposite. The docstring's claim that a bad run never leaves a half set
    # was true of the local directory and false of the thing people fetch.
    print("\nuploading shards", flush=True)
    shards = sorted(p.name for p in staging.glob("*.jsonl.gz"))
    r = subprocess.run(["gh", "release", "upload", TAG, *shards, "--clobber"],
                       cwd=str(staging))
    if r.returncode != 0:
        print(f"upload failed with {r.returncode}; the published index still "
              f"describes the previous set, which is still there")
        return 1
    print("uploading index", flush=True)
    r = subprocess.run(["gh", "release", "upload", TAG, "index.json",
                        "--clobber"], cwd=str(staging))
    if r.returncode != 0:
        print(f"index upload failed with {r.returncode}. The new shards are "
              f"live under the old index, which lists fewer of them; re-run "
              f"to finish.")
        return 1

    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    print(f"published {_roles(new):,} roles to {BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
