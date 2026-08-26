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

`--platforms` answers a different question from the sample run above: not "how
long did this take" but "where does the hour actually go". It reads a small
sample of each platform, counts the REQUESTS rather than the boards, and times
the HTTP call itself rather than the call plus whatever the limiter made it
wait. Those three separations are what make the arithmetic work, because the
scan is not one bottleneck:

  * A single-host platform is paced, so its floor is requests x 1/rps and its
    latency barely matters. apply.workable.com is 2,094 requests at 0.7/s and
    that alone is 50 minutes, whatever the network does.
  * A one-board-per-host platform is not paced by anything, so its cost is
    request-seconds / concurrency and its latency is the whole story.
  * A paged platform costs several requests per board, and that multiplier is
    invisible in a board count. Workday's page cap going from 3 to 10 changes
    this number and nothing else in the report would show it.

  python3 tools/bench_fetch.py --platforms
  python3 tools/bench_fetch.py --platforms workday,greenhouse --per-platform 40
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib                                  # noqa: E402

from jobradar import adapters                      # noqa: E402
from jobradar.models import Source                 # noqa: E402

BUNDLED = Path(__file__).resolve().parent.parent / "sources" / "sources.json"

# Set once in `main`, so `--module` still decides which fetch module is
# being measured and the helpers below do not each have to be handed it.
fetch_mod_holder: dict = {}


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


# Boards to read per platform in `--platforms` mode. Small on the single-host
# platforms because every one of their boards is paced through one host and a
# big sample is just a long wait, generous on the paged ones because the
# number being measured there is requests PER BOARD and a handful of boards
# does not pin it down. Workable stays at 12: at 0.7 requests a second that is
# already 17 seconds, and it is the one host in this list with a standing
# quota that has answered 429 for hours at a time.
PLATFORM_SAMPLE = {
    "greenhouse": 40, "ashby": 40, "workable": 12, "icims": 30, "workday": 25,
    "personio": 30, "breezy": 30, "recruitee": 30, "smartrecruiters": 30,
    "oracle": 25, "jobvite": 20, "teamtailor": 15, "phenom": 8, "avature": 8,
    "rmk": 8,
}


def measure_platforms(srcs, only, per_platform, ua, terms, concurrency):
    """Requests per board and true per-request latency, platform by platform.

    Timed by wrapping `requests.Session.get/post`, not by reading
    `Result.elapsed`. `fetch_one` starts its clock before `HostLimiter.wait`,
    so `elapsed` on a paced host is mostly the wait: on apply.workable.com it
    reports about 1.4 seconds for a request that actually takes 0.21. Using it
    here would attribute the pacing to the network and hide the fact that
    Workable's floor is entirely self-imposed.
    """
    import requests

    calls: list[tuple[str, float]] = []
    lock = threading.Lock()
    cur = threading.local()
    real_get, real_post = requests.Session.get, requests.Session.post

    def wrap(orig):
        def inner(self, url, *a, **kw):
            t0 = time.time()
            try:
                return orig(self, url, *a, **kw)
            finally:
                with lock:
                    calls.append((getattr(cur, "plat", "?"), time.time() - t0))
        return inner

    real_dispatch = fetch_mod_holder["mod"]._fetch_dispatch

    def dispatch(src, *a, **kw):
        cur.plat = src.platform
        return real_dispatch(src, *a, **kw)

    by_plat: dict[str, list] = collections.defaultdict(list)
    for s in srcs:
        by_plat[s.platform].append(s)
    plats = only or [p for p in PLATFORM_SAMPLE if p in by_plat]

    requests.Session.get = wrap(real_get)
    requests.Session.post = wrap(real_post)
    fetch_mod_holder["mod"]._fetch_dispatch = dispatch
    rows = {}
    try:
        for plat in plats:
            pool = by_plat.get(plat)
            if not pool:
                print(f"  {plat}: not in the source list")
                continue
            n = per_platform or PLATFORM_SAMPLE.get(plat, 20)
            sample = random.Random(7).sample(pool, min(n, len(pool)))
            with lock:
                calls.clear()
            t0 = time.time()
            res = fetch_mod_holder["mod"].fetch_all(
                sample, concurrency=concurrency, timeout=20, retries=2,
                user_agent=ua, search_terms=terms)
            wall = time.time() - t0
            with lock:
                mine = [c for c in calls if c[0] == plat] or calls[:]
            lat = sorted(t for _p, t in mine)
            hosts = len({urlparse(s.url).netloc for s in sample})
            n429 = sum(1 for r in res if r.throttled or r.status == 429)
            rows[plat] = {
                "boards": len(sample), "hosts": hosts,
                "requests": len(mine),
                "req_per_board": round(len(mine) / max(1, len(sample)), 2),
                "latency_p50": round(lat[len(lat) // 2], 3) if lat else 0.0,
                "latency_mean": round(sum(lat) / len(lat), 3) if lat else 0.0,
                "wall": round(wall, 1),
                "ok": sum(1 for r in res if r.ok), "http_429": n429,
                "jobs": sum(len(adapters.parse(r.payload, r.source))
                            for r in res if r.ok),
            }
            r = rows[plat]
            print(f"  {plat:<16} {r['boards']:>3} boards on {r['hosts']:>4} host(s)  "
                  f"{r['req_per_board']:>5.2f} req/board  "
                  f"latency p50 {r['latency_p50']:.3f}s  "
                  f"{r['jobs']:>6,} postings"
                  + ("  ** 429 **" if n429 else ""), flush=True)
    finally:
        requests.Session.get, requests.Session.post = real_get, real_post
        fetch_mod_holder["mod"]._fetch_dispatch = real_dispatch
    return rows


def project(rows, srcs) -> None:
    """Turn the per-platform rates into a whole-scan estimate.

    Two independent floors, and the scan cannot beat the larger:
      per-host   requests on that host x 1/rps, for a host that is paced
      machine    total request-seconds / concurrency
    """
    counts = collections.Counter(s.platform for s in srcs)
    host_of: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for s in srcs:
        host_of[s.platform][urlparse(s.url).netloc] += 1

    print("\n  projected over the whole source list")
    work = 0.0
    host_req: collections.Counter = collections.Counter()
    for plat, r in sorted(rows.items()):
        n = counts.get(plat, 0)
        reqs = n * r["req_per_board"]
        work += reqs * r["latency_mean"]
        for host, c in host_of[plat].items():
            host_req[host] += c * r["req_per_board"]
    print(f"    total network work   {work / 60:8.1f} min of request-seconds")

    # Falls back rather than requiring `main` to have run. `project` is pure
    # arithmetic over rates it is handed, so it is the one part of this tool
    # that can be exercised with no network, and a KeyError here would make it
    # untestable for the sake of a global.
    mod = fetch_mod_holder.get("mod") or importlib.import_module("jobradar.fetch")
    limiter = mod.HostLimiter()
    floors = []
    for host, reqs in host_req.items():
        gap = limiter.gap_for(host)
        if gap > 0 and reqs > 1:
            floors.append((reqs * gap, host, int(reqs), gap))
    floors.sort(reverse=True)
    print("    per-host pacing floors, worst first:")
    for secs, host, reqs, gap in floors[:6]:
        print(f"      {host:<34} {reqs:>6,} req x {gap:.2f}s = "
              f"{secs / 60:6.1f} min")
    if floors:
        print(f"\n    the scan cannot finish faster than {floors[0][0] / 60:.1f} min "
              f"({floors[0][1]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=600)
    ap.add_argument("--platforms", nargs="?", const="", default=None,
                    help="per-platform throughput instead of one timed "
                         "sample. Optionally a comma-separated subset.")
    ap.add_argument("--per-platform", type=int, default=0,
                    help="boards per platform; 0 uses PLATFORM_SAMPLE")
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
    fetch_mod_holder["mod"] = fetch_mod
    srcs = load_sources()

    if args.platforms is not None:
        only = [p.strip() for p in args.platforms.split(",") if p.strip()]
        ua = ("job-radar/0.1 (+https://github.com/maccydee/job-radar) "
              "personal job search tool")
        terms = ["engineering manager", "senior engineering manager",
                 "head of engineering", "director of engineering"]
        print(f"[{args.label}] per-platform throughput")
        rows = measure_platforms(srcs, only, args.per_platform, ua, terms,
                                 args.concurrency or 16)
        if not only:
            project(rows, srcs)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(rows, indent=1),
                                           encoding="utf-8")
        return 0

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
