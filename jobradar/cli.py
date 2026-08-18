"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import adapters, output, sources as src_mod
from .config import Config, load as load_cfg
from .discover import discover as run_discover, validate_source
from .fetch import detect_throttling, fetch_all
from .models import Source
from .screen import run as screen_run
from .state import State


def _say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- scan
def cmd_scan(args) -> int:
    cfg = load_cfg(args.config)
    srcs = src_mod.load(cfg)
    if args.limit:
        srcs = srcs[: args.limit]
    if not srcs:
        _say("No sources. Run `job-radar setup` or check sources.use_bundled.")
        return 1

    state = State(Path(args.state) if args.state else None)
    _say(f"Fetching {len(srcs)} sources at concurrency {cfg.concurrency}...")

    done = {"n": 0}

    def tick(res):
        done["n"] += 1
        if done["n"] % 25 == 0:
            _say(f"  {done['n']}/{len(srcs)}")

    results = fetch_all(
        srcs, concurrency=cfg.concurrency, timeout=cfg.timeout,
        retries=cfg.retries, user_agent=cfg.user_agent,
        search_terms=cfg.titles_include, on_result=tick,
    )

    all_jobs, counts, ok = [], {}, 0
    for res in results:
        if not res.ok:
            continue
        ok += 1
        jobs = adapters.parse(res.payload, res.source)
        for j in jobs:
            j.sector = j.sector or res.source.sector
            j.country = j.country or res.source.country
        counts[res.source.key] = len(jobs)
        all_jobs.extend(jobs)

    throttled = detect_throttling(results, counts, state.source_counts)

    _say(f"  {ok}/{len(srcs)} responded, {len(all_jobs)} postings")
    if throttled:
        _say(f"  ! {len(throttled)} sources look throttled (returned nothing "
             f"but have before): {', '.join(throttled[:6])}")

    kept, dropped = screen_run(all_jobs, cfg)

    # What you already did about a role beats what the scanner thinks of it.
    from .applications import Tracker, SETTLED
    tracker = Tracker.load()
    if tracker.apps:
        tagged = tracker.annotate(kept)
        settled = [j for j in kept if j.app_status in SETTLED]
        kept = [j for j in kept if j.app_status not in SETTLED]
        _say(f"  {tagged} already tracked, {len(settled)} settled and hidden")

    new, seen = state.split(kept)
    _say(f"  {len(kept)} match your config, {len(new)} new")

    meta = {
        "sources_ok": ok, "sources_total": len(srcs),
        "postings": len(all_jobs), "matching": len(kept),
        "new": len(new), "throttled": throttled, "dropped": dropped,
    }
    outdir = Path(args.out or cfg.out_dir)
    written = []
    if "html" in cfg.formats:
        written.append(output.html_out.write(
            outdir / "index.html", new=new, seen=seen, dropped=dropped,
            sources_ok=ok, sources_total=len(srcs), throttled=throttled,
        ))
    if "json" in cfg.formats:
        written.append(output.write_json(outdir / "roles.json", new, seen, meta))
    if "markdown" in cfg.formats or "md" in cfg.formats:
        written.append(output.write_markdown(outdir / "roles.md", new, seen, meta))

    if not args.dry_run:
        state.record(kept, counts)
        state.save()

    for p in written:
        _say(f"  wrote {p}")
    return 0


# ---------------------------------------------------------------- discover
def cmd_discover(args) -> int:
    results = []
    for target in args.targets:
        _say(f"Looking for {target}...")
        found = run_discover(target, company=args.company, validate=not args.no_validate)
        if not found:
            _say("  nothing found. Try the careers page URL directly.")
            continue
        for f in found:
            if f.identity == "blocked":
                _say(f"  blocked. {f.note}")
                continue
            mark = {"ok": "verified", "mismatch": "WRONG COMPANY?",
                    "unchecked": "unverified"}.get(f.identity, f.identity)
            _say(f"  {f.platform:<16} {f.live_jobs:>4} jobs  [{mark}]  {f.url}")
            if f.note:
                _say(f"                   {f.note}")
            results.append(f)

    good = [f for f in results if f.live_jobs > 0 and f.identity != "mismatch"]
    if args.add and good:
        cfg_path = Path(args.config or "config.yaml")
        _append_sources(cfg_path, [f.to_source() for f in good])
        _say(f"\nAdded {len(good)} source(s) to {cfg_path}")
    elif good and not args.add:
        _say("\nRe-run with --add to write these into your config.")
    return 0 if results else 1


def _append_sources(cfg_path: Path, new: list[Source]) -> None:
    import yaml
    raw = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    raw = raw or {}
    raw.setdefault("sources", {}).setdefault("extra", [])
    have = {s.get("url") for s in raw["sources"]["extra"] if isinstance(s, dict)}
    for s in new:
        if s.url not in have:
            raw["sources"]["extra"].append(s.to_dict())
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


# ---------------------------------------------------------------- validate
def cmd_validate(args) -> int:
    cfg = load_cfg(args.config) if Path(args.config or "config.yaml").exists() else Config()
    srcs = src_mod.load_file(args.file) if args.file else src_mod.load(cfg)
    if args.limit:
        srcs = srcs[: args.limit]
    _say(f"Validating {len(srcs)} sources...")

    rows, dead, mismatch = [], [], []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, cfg.concurrency)) as ex:
        for i, row in enumerate(ex.map(validate_source, srcs), 1):
            rows.append(row)
            if row["verdict"] == "dead":
                dead.append(row)
            elif row["verdict"] == "mismatch":
                mismatch.append(row)
            if i % 25 == 0:
                _say(f"  {i}/{len(srcs)}")

    _say(f"\n  live: {len(rows) - len(dead)}   dead: {len(dead)}   "
         f"identity mismatch: {len(mismatch)}")
    for r in dead[:40]:
        _say(f"  DEAD      {r['company']} <- {r['url']}")
    for r in mismatch[:40]:
        _say(f"  MISMATCH  {r['company']}: {r['note']}")

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps({
            "checked": datetime.now().isoformat(timespec="seconds"),
            "total": len(rows), "dead": dead, "mismatch": mismatch, "rows": rows,
        }, indent=1))
        _say(f"  wrote {args.report}")

    if args.prune and args.file:
        keep = [s for s in srcs if s.key not in {r["url"].split("?")[0] for r in dead}]
        src_mod.save(keep, args.file, meta={"pruned": len(srcs) - len(keep),
                                            "checked": datetime.now().date().isoformat()})
        _say(f"  pruned {len(srcs) - len(keep)} dead sources from {args.file}")
    return 0


# ---------------------------------------------------------------- coverage
def cmd_coverage(args) -> int:
    cfg = load_cfg(args.config) if Path(args.config or "config.yaml").exists() else Config()
    srcs = src_mod.load_file(args.file) if args.file else src_mod.load(cfg)
    cov = src_mod.coverage(srcs)
    _say(f"{cov['total']} sources\n")
    for label, key in (("By sector", "by_sector"), ("By country", "by_country"),
                       ("By platform", "by_platform")):
        _say(label)
        for k, v in cov[key].items():
            _say(f"  {v:>5}  {k}")
        _say()
    return 0


# ---------------------------------------------------------------- applied
def cmd_applied(args) -> int:
    """Record what happened with a role, without hand-editing YAML."""
    import yaml
    from .applications import STATUSES, Tracker

    if args.status not in STATUSES:
        _say(f"status must be one of: {', '.join(STATUSES)}")
        return 1

    path = Path(args.file or "applications.local.yaml")
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw if isinstance(raw, dict) else {"applications": raw or []}
    raw.setdefault("applications", [])

    target = args.target
    entry = {"status": args.status}
    if target.startswith("http"):
        entry["url"] = target
        # Fill in the company and title from the last scan if we can, so the
        # entry is readable later rather than being a bare URL.
        roles = Path(args.out or "out") / "roles.json"
        if roles.exists():
            data = json.loads(roles.read_text())
            for j in data.get("new", []) + data.get("matching", []):
                if j.get("url", "").rstrip("/") == target.rstrip("/"):
                    entry["org"], entry["role"] = j.get("company", ""), j.get("title", "")
                    break
    else:
        entry["org"] = target
        if args.role:
            entry["role"] = args.role
    if args.note:
        entry["note"] = args.note
    if args.date:
        entry["date"] = args.date

    # Update in place when this role is already tracked, rather than stacking
    # duplicate entries that disagree with each other.
    key = entry.get("url") or (entry.get("org", "").lower(), entry.get("role", "").lower())
    replaced = False
    for i, existing in enumerate(raw["applications"]):
        if not isinstance(existing, dict):
            continue
        ekey = existing.get("url") or (str(existing.get("org", "")).lower(),
                                       str(existing.get("role", "")).lower())
        if ekey == key:
            raw["applications"][i] = {**existing, **entry}
            replaced = True
            break
    if not replaced:
        raw["applications"].append(entry)

    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    what = entry.get("org") or entry.get("url")
    _say(f"{'updated' if replaced else 'recorded'}: {what} -> {args.status}")
    _say(f"  {path} now tracks {len(raw['applications'])} role(s)")
    return 0


# ---------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    from .setup_wizard import run as wizard
    return wizard(Path(args.config or "config.yaml"), non_interactive=args.defaults)


# ---------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="job-radar",
        description="Watch employer job boards directly, and only be told about "
                    "roles that pass your own filters.",
    )
    p.add_argument("-c", "--config", default=None, help="config.yaml path")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="fetch every source and report matches")
    s.add_argument("-o", "--out", default=None)
    s.add_argument("--state", default=None)
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--dry-run", action="store_true",
                   help="do not record what was seen (re-reports the same roles next time)")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("discover", help="find a company's job board from its careers page")
    d.add_argument("targets", nargs="+", help="domain, careers URL, or company name")
    d.add_argument("--company", default=None)
    d.add_argument("--add", action="store_true", help="write results into your config")
    d.add_argument("--no-validate", action="store_true")
    d.set_defaults(func=cmd_discover)

    v = sub.add_parser("validate", help="check known sources are alive and are who they claim")
    v.add_argument("--file", default=None, help="a sources.json to check instead of the config set")
    v.add_argument("--report", default=None)
    v.add_argument("--limit", type=int, default=0)
    v.add_argument("--prune", action="store_true", help="rewrite --file without dead sources")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("coverage", help="where the source list is thin")
    c.add_argument("--file", default=None)
    c.set_defaults(func=cmd_coverage)

    ap = sub.add_parser("applied", help="record what happened with a role")
    ap.add_argument("target", help="the posting URL, or just a company name")
    ap.add_argument("-s", "--status", default="applied",
                    help="interested|applied|submitted|interviewing|offer|rejected|withdrawn|closed")
    ap.add_argument("--role", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--out", default=None)
    ap.set_defaults(func=cmd_applied)

    w = sub.add_parser("setup", help="build a config by answering a few questions")
    w.add_argument("--defaults", action="store_true", help="write a default config, ask nothing")
    w.set_defaults(func=cmd_setup)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        _say(str(e))
        return 1
    except KeyboardInterrupt:
        _say("\nstopped")
        return 130


if __name__ == "__main__":
    sys.exit(main())
