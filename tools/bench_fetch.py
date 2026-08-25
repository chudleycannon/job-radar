#!/usr/bin/env python3
"""Time the scan's fetch phase on a repeatable sample, and count what failed.

Exists because "the scan got faster" is not a finding on its own. A fetcher
can be made to look fast by giving up sooner: a 429 recorded as an empty
board is indistinguishable from a real empty board, and that is exactly how
250 live Workable employers (Contentful and Ecosia among them) were once
discarded. So every timing here is reported next to the 429 count and the
count of sources that returned jobs, and a run that is faster but returns
fewer jobs is a regression, not a win.

Writes nothing to the database or to state. It calls `fetch_all` directly
rather than shelling out to `job-radar scan`, so the number is the fetch
phase alone with no screening, ranking or enrichment mixed into it.

  python3 tools/bench_fetch.py --sample 600 --concurrency 4 --no-limiter
  python3 tools/bench_fetch.py --sample 600
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib                                  # noqa: E402

from jobradar import adapters                      # noqa: E402
from jobradar.models import Source                 # noqa: E402

BUNDLED = Path(__file__).resolve().parent.parent / "sources" / "sources.json"


def load_sources() -> list[Source]:
    raw = json.loads(BUNDLED.read_text(encoding="utf-8"))
    return [adapters.prepare(Source.from_dict(d)) for d in raw["sources"]]


def stratified(srcs: list[Source], n: int, seed: int = 0,
               exclude: set[str] | None = None) -> list[Source]:
    """A sample that keeps the real host mix, in the real file order.

    A plain `[:n]` slice is not a sample of this list: the file is sorted into
    contiguous per-platform blocks, so the first 400 sources are 400 Ashby
    boards and nothing else. That times one host's patience, not a scan.

    Rounding per host is the trap on the other side. At a 3% sample rate,
    `round(1 * 0.03)` is 0 for every one of the ~7,748 hosts holding a single
    board, so a naive proportional sample contains nothing but the seven busy
    hosts, which is exactly the half of the work the change is meant to speed
    up. Each host therefore gets its whole share plus a weighted coin for the
    remainder, so a host with one board is picked about 3% of the time rather
    than never.
    """
    exclude = exclude or set()
    by_host: dict[str, list[int]] = collections.defaultdict(list)
    for i, s in enumerate(srcs):
        host = urlparse(s.url).netloc
        if host not in exclude:
            by_host[host].append(i)
    rng = random.Random(seed)
    live = sum(len(v) for v in by_host.values())
    share = n / live
    picked: list[int] = []
    for host in sorted(by_host):
        idxs = by_host[host]
        want = len(idxs) * share
        take = int(want) + (1 if rng.random() < want - int(want) else 0)
        if take:
            picked.extend(rng.sample(idxs, min(take, len(idxs))))
    picked.sort()
    return [srcs[i] for i in picked]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=0,
                    help="0 means whatever fetch_all defaults to")
    ap.add_argument("--no-limiter", action="store_true",
                    help="fetch with per-host pacing switched off")
    ap.add_argument("--label", default="run")
    ap.add_argument("--exclude-host", action="append", default=[],
                    help="leave a host out, e.g. while another "
                         "scan is already hammering it")
    ap.add_argument("--module", default="jobradar.fetch",
                    help="which fetch module to time. Point it at a pristine "
                         "copy restored from git to re-measure the baseline "
                         "without checking the working tree back out.")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    fetch_mod = importlib.import_module(args.module)
    srcs = load_sources()
    sample = stratified(srcs, args.sample, args.seed,
                        exclude=set(args.exclude_host))
    hosts = collections.Counter(urlparse(s.url).netloc for s in sample)
    print(f"[{args.label}] {len(sample)} sources over {len(hosts)} hosts")
    for h, c in hosts.most_common(8):
        print(f"    {h:<32} {c}")

    kw = {"timeout": 20, "retries": 2,
          "user_agent": "job-radar/0.1 (+https://github.com/maccydee/job-radar) "
                        "personal job search tool"}
    if args.concurrency:
        kw["concurrency"] = args.concurrency
    if args.no_limiter and "per_host_rps" in fetch_mod.fetch_all.__code__.co_varnames:
        kw["per_host_rps"] = 0

    t0 = time.time()
    results = fetch_mod.fetch_all(sample, **kw)
    wall = time.time() - t0

    jobs = {}
    for r in results:
        if r.ok:
            jobs[r.source.key] = len(adapters.parse(r.payload, r.source))
    with_jobs = sum(1 for v in jobs.values() if v > 0)
    total_jobs = sum(jobs.values())
    n429 = sum(1 for r in results if r.status == 429 or (r.error or "") == "HTTP 429")
    throttled = sum(1 for r in results if r.throttled)
    failed = [r for r in results if not r.ok]
    by_err = collections.Counter((r.error or "?") for r in failed)
    by_host_429: dict[str, int] = collections.Counter(
        urlparse(r.source.url).netloc for r in results
        if r.throttled or (r.error or "") == "HTTP 429")

    print(f"\n[{args.label}] wall {wall:7.1f}s   {len(sample)/wall:5.2f} sources/s")
    print(f"[{args.label}] responded          {len(results) - len(failed)}/{len(sample)}")
    print(f"[{args.label}] returned jobs      {with_jobs}   ({total_jobs:,} postings)")
    print(f"[{args.label}] HTTP 429           {max(n429, throttled)}")
    print(f"[{args.label}] failed             {len(failed)}")
    for e, c in by_err.most_common(10):
        print(f"    {e:<28} {c}")
    if by_host_429:
        print(f"[{args.label}] 429 by host: {dict(by_host_429)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "label": args.label, "sample": len(sample), "wall": wall,
            "responded": len(results) - len(failed), "with_jobs": with_jobs,
            "total_jobs": total_jobs, "http_429": max(n429, throttled),
            "failed": len(failed), "errors": dict(by_err),
            "per_source_jobs": jobs,
        }, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
