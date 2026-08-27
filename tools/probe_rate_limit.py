#!/usr/bin/env python3
"""Measure what apply.workable.com's undocumented rate limit actually is.

The tool paces that host at 0.7 requests a second and that single number is
the floor of a 60 minute scan: 2,094 boards x 1.43s is 49.9 minutes, and
Workable's own response time is 0.213s, so 85% of the scan is the limiter
waiting. Raising it is worth half an hour a run. Guessing at it is worth a
sixteen hour ban: this host has answered `Retry-After: 57841` once already.

So this measures rather than guesses, under rules that make a bad measurement
survivable:

  * It STOPS on the first 429 of any probe. Pushing through a refusal to see
    how bad it gets is how the ban happened.
  * Every probe has a hard request cap, and the cap is spent before the edge
    is found rather than after.
  * Every response's status and full headers go to a JSONL log, so a later
    argument about the model is settled from the record and not from memory.
  * `Retry-After` on any response aborts the whole run, loudly. That header
    means the host has stopped negotiating.

It does NOT rotate user agents, use proxies, or spread requests over hosts.
The point is to find a published-but-undocumented limit and stay inside it,
not to get round one. Requests carry the same User-Agent and Accept the scan
sends, because a limit measured under a different identity is not this tool's
limit.

  python3 tools/probe_rate_limit.py headers                 # 1 request
  python3 tools/probe_rate_limit.py burst --rate 3 --cap 210 --label rested
  python3 tools/probe_rate_limit.py burst --rate 3 --cap 210 --offset 400

`burst` is the measurement that pays: from a bucket holding B tokens, a run
at rate R fails on request n where n - R_refill * (n / R) = B, so counting n
recovers B once the refill rate is known, and two bursts either side of a
known idle gap recover both B and the refill rate together. A sustained run
at a candidate rate answers the same question and costs several hundred
requests to do it, which is most of a budget for one bit of information.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests                                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "sources" / "sources.json"
HOST = "apply.workable.com"

# The log lives next to this script rather than in a temp directory, because
# the whole value of the exercise is the record: a probe that cost 500
# requests against a host that bans is not one to repeat because the numbers
# were written somewhere that gets swept at midnight.
LOG = ROOT / "tools" / "rate_probe_log.jsonl"

# Exactly what a scan sends. Config's default, not a browser's.
USER_AGENT = ("job-radar/0.1 (+https://github.com/maccydee/job-radar) "
              "personal job search tool")
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

TIMEOUT = 20


def tokens(offset: int, n: int) -> list[str]:
    """Distinct board URLs, so nothing is answered from a per-URL cache."""
    raw = json.loads(BUNDLED.read_text(encoding="utf-8"))["sources"]
    urls = [s["url"] for s in raw if HOST in (s.get("url") or "")]
    if offset + n > len(urls):
        raise SystemExit(f"only {len(urls)} boards, asked for {offset}+{n}")
    return urls[offset:offset + n]


def log(rec: dict) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def spent() -> int:
    """Requests this task has already sent to the host. The budget is 600."""
    if not LOG.exists():
        return 0
    return sum(1 for line in LOG.read_text(encoding="utf-8").splitlines()
               if line.strip() and json.loads(line).get("kind") == "request")


def one(session: requests.Session, url: str, label: str, i: int) -> dict:
    t0 = time.monotonic()
    rec: dict = {"kind": "request", "label": label, "i": i, "url": url,
                 "at": datetime.now(timezone.utc).isoformat()}
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as exc:                        # noqa: BLE001
        rec |= {"status": None, "error": f"{type(exc).__name__}: {exc}",
                "elapsed": round(time.monotonic() - t0, 3)}
        log(rec)
        return rec
    rec |= {"status": r.status_code,
            "elapsed": round(time.monotonic() - t0, 3),
            "bytes": len(r.content),
            "headers": dict(r.headers)}
    log(rec)
    return rec


def cmd_headers(args: argparse.Namespace) -> int:
    """One request, every response header printed. Costs one request.

    Workable documents a rate limit for its AUTHENTICATED API, 10 requests
    per 10 seconds per account token, and says the responses carry
    X-Rate-Limit-Limit / -Remaining / -Reset. It documents nothing about this
    public widget endpoint. If those headers turn up here, the tool should
    read them and pace itself exactly, and every number below is wasted
    effort. That is worth one request to find out.
    """
    url = tokens(args.offset, 1)[0]
    with requests.Session() as s:
        rec = one(s, url, "headers", 0)
    print(f"GET {url}")
    print(f"  status {rec.get('status')}  {rec.get('elapsed')}s  "
          f"{rec.get('bytes')} bytes")
    for k, v in sorted((rec.get("headers") or {}).items()):
        print(f"  {k}: {v}")
    rl = [k for k in (rec.get("headers") or {})
          if "rate" in k.lower() or "limit" in k.lower()
          or "retry" in k.lower()]
    print(f"\n  rate-limit-ish headers: {rl or 'NONE'}")
    print(f"  budget spent: {spent()}")
    return 0


def cmd_burst(args: argparse.Namespace) -> int:
    """Requests at a fixed rate until the host refuses, or until the cap.

    Stops on the first 429, every time. The number that matters is how many
    got through, so there is nothing to learn from the second refusal that is
    worth the risk of asking for it.
    """
    urls = tokens(args.offset, args.cap)
    gap = 1.0 / args.rate
    already = spent()
    if already + args.cap > args.budget:
        raise SystemExit(f"cap {args.cap} would take the run past the "
                         f"{args.budget} budget ({already} spent)")

    print(f"burst label={args.label} rate={args.rate}/s cap={args.cap} "
          f"offset={args.offset}  (spent so far: {already})")
    log({"kind": "probe_start", "label": args.label, "rate": args.rate,
         "cap": args.cap, "offset": args.offset,
         "at": datetime.now(timezone.utc).isoformat()})

    t0 = time.monotonic()
    statuses: dict[str, int] = {}
    sent = refused_at = 0
    with requests.Session() as s:
        for i, url in enumerate(urls):
            due = t0 + i * gap
            now = time.monotonic()
            if due > now:
                time.sleep(due - now)
            rec = one(s, url, args.label, i)
            sent += 1
            key = str(rec.get("status") or rec.get("error", "")[:40])
            statuses[key] = statuses.get(key, 0) + 1

            ra = (rec.get("headers") or {}).get("Retry-After")
            if ra:
                print(f"\n  !! Retry-After: {ra} on request {i + 1}. "
                      f"STOPPING THE WHOLE TASK.")
                log({"kind": "abort", "reason": "retry-after", "value": ra,
                     "i": i})
                break
            if rec.get("status") == 429:
                refused_at = i + 1
                print(f"\n  429 on request {refused_at} at "
                      f"t+{time.monotonic() - t0:.1f}s. Stopping.")
                break
            if (i + 1) % 25 == 0:
                print(f"  {i + 1}/{args.cap}  t+{time.monotonic() - t0:.0f}s")

    elapsed = time.monotonic() - t0
    summary = {"kind": "probe_end", "label": args.label, "rate": args.rate,
               "sent": sent, "refused_at": refused_at,
               "elapsed": round(elapsed, 1),
               "achieved_rps": round(sent / elapsed, 3) if elapsed else None,
               "statuses": statuses,
               "at": datetime.now(timezone.utc).isoformat()}
    log(summary)
    print(f"\n  sent {sent} in {elapsed:.1f}s "
          f"({sent / elapsed:.2f}/s achieved)")
    print(f"  statuses {statuses}")
    print(f"  first refusal at request {refused_at or 'none (hit cap)'}")
    print(f"  budget spent: {spent()}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--budget", type=int, default=600,
                   help="hard ceiling on requests across the whole task")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("headers", help="one request, dump every header")
    h.add_argument("--offset", type=int, default=0)
    h.set_defaults(fn=cmd_headers)

    b = sub.add_parser("burst", help="fixed rate until the first 429")
    b.add_argument("--rate", type=float, required=True)
    b.add_argument("--cap", type=int, required=True)
    b.add_argument("--offset", type=int, default=0)
    b.add_argument("--label", default="burst")
    b.set_defaults(fn=cmd_burst)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
