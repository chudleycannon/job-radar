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

    # The database is the source of truth for what you already did about a
    # role. It beats whatever the scanner thinks of it today.
    from . import store
    con = store.connect(args.db)
    mig = store.migrate(con)
    if mig["roles"] or mig["statuses"]:
        _say(f"  migrated {mig['roles']} roles and {mig['statuses']} statuses "
             f"into the database")

    store.upsert_roles(con, kept)
    new_ids = store.new_since_last_run(con, [j.uid for j in kept])

    settled = store.settled_uids(con)
    hidden = [j for j in kept if j.uid in settled]
    kept = [j for j in kept if j.uid not in settled]

    # Carry each role's status onto the job so the dashboard can show it.
    for j in kept:
        row = con.execute("SELECT status FROM role_state WHERE uid=?",
                          (j.uid,)).fetchone()
        j.app_status = row["status"] if row and row["status"] != "new" else ""
    if hidden:
        _say(f"  {len(hidden)} settled and hidden")

    store.bump_runs(con)
    new = [j for j in kept if j.uid in new_ids]
    seen = [j for j in kept if j.uid not in new_ids]
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
        # A one-directional export so a fresh GitHub Actions runner has a
        # seen-set to commit. Nothing reads it back except a clone with no
        # database yet.
        state.record(kept, counts)
        state.save()
        con.close()

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


def _append_sources(cfg_path: Path, new: list[Source]) -> None:   # used by discover --add
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

    if args.prune and not args.file:
        _say("  --prune needs --file: it rewrites a source list, and there is "
             "no file to rewrite without one.")
    if args.prune and args.file:
        # Compare whole URLs. Source.key deliberately keeps the query string
        # (LinkedIn searches differ only by it), so matching it against a
        # stripped URL never matched and 82% of sources could never be pruned.
        dead_urls = {r["url"] for r in dead}
        keep = [s for s in srcs if s.url not in dead_urls]
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
def _resolve_uid(con, target: str):
    """Find one role from a URL, a company name, or a uid. Returns (uid, why).

    Fails loudly with the candidates rather than guessing: recording a status
    against the wrong role is worse than not recording it.
    """
    import re as _re
    t = target.strip()
    row = con.execute("SELECT uid FROM roles WHERE uid=?", (t,)).fetchone()
    if row:
        return row["uid"], ""

    if t.startswith("http"):
        clean = _re.sub(r"[?#].*$", "", t.rstrip("/"))
        rows = con.execute(
            "SELECT uid, company, title FROM roles "
            "WHERE url=? OR url LIKE ?", (t, clean + "%")).fetchall()
        if len(rows) == 1:
            return rows[0]["uid"], ""
        if not rows:
            return None, f"no role in the database has that URL"
        return None, f"{len(rows)} roles share that URL"

    rows = con.execute(
        "SELECT uid, company, title FROM roles WHERE company LIKE ? "
        "ORDER BY last_seen DESC", (f"%{t}%",)).fetchall()
    if not rows:
        return None, f"nothing matches {t!r}"
    if len(rows) == 1:
        return rows[0]["uid"], ""
    listing = "\n".join(f"    {r['uid']}  {r['company']} - {r['title'][:52]}"
                         for r in rows[:8])
    return None, (f"{len(rows)} roles match {t!r}. Pick one by uid:\n{listing}")


def cmd_applied(args) -> int:
    """Record what happened with a role. Writes the database, same as the
    dashboard does, so the two cannot disagree."""
    from . import store
    con = store.connect(args.db)
    try:
        if args.status not in store.STATUSES:
            _say(f"status must be one of: {', '.join(store.STATUSES)}")
            return 1
        uid, why = _resolve_uid(con, args.target)
        if not uid:
            _say(f"Could not identify a role: {why}")
            return 1
        row = con.execute("SELECT company, title FROM roles WHERE uid=?",
                          (uid,)).fetchone()
        store.set_status(con, uid, args.status, args.note)
        _say(f"{row['company']} - {row['title'][:56]}")
        _say(f"  -> {args.status}")
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------- generate
def cmd_generate(args) -> int:
    """Run a screen, CV or cover letter from the command line.

    The same queue and the same runner the dashboard uses. The design's rule
    is that nothing generates on a schedule; an explicit command is still a
    deliberate spend.
    """
    from . import runner, store
    con = store.connect(args.db)
    try:
        if args.kind not in runner.KINDS:
            _say(f"kind must be one of: {', '.join(runner.KINDS)}")
            return 1
        uid, why = _resolve_uid(con, args.target)
        if not uid:
            _say(f"Could not identify a role: {why}")
            return 1
        if args.kind == "cover_letter" and not store.has_artifact(con, uid, "cv"):
            _say("Draft the CV first: the letter is checked against it for "
                 "repeated phrasing.")
            return 1
        row = con.execute("SELECT company, title FROM roles WHERE uid=?",
                          (uid,)).fetchone()
        job_id = store.enqueue(con, uid, args.kind)
        _say(f"{row['company']} - {row['title'][:56]}")
        _say(f"  {args.kind}, job {job_id}. This spends tokens.")
    finally:
        con.close()

    runner.run_job(job_id, db_path=args.db, base=args.docs)

    con = store.connect(args.db)
    try:
        j = con.execute("SELECT state, error FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
        if j["state"] != "done":
            _say(f"  failed: {j['error']}")
            return 1
        for a in store.artifacts_for(con, uid):
            if a["kind"] == args.kind:
                rating = f"  {a['rating']:.0f}/100" if a["rating"] else ""
                _say(f"  wrote {a['path']}{rating}")
                gates = json.loads(a["gates"] or "{}")
                bad = [k for k, v in gates.items() if v is False]
                _say(f"  gates: {'all passed' if not bad else 'FAILED ' + ', '.join(bad)}")
                break
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------- list
def cmd_list(args) -> int:
    """Everything the dashboard shows, as text."""
    from . import store
    con = store.connect(args.db)
    try:
        q = ("SELECT r.*, COALESCE(s.status,'new') status FROM roles r "
             "LEFT JOIN role_state s ON s.uid=r.uid")
        params = []
        if args.status:
            q += " WHERE COALESCE(s.status,'new')=?"
            params.append(args.status)
        elif not args.all:
            q += (" WHERE COALESCE(s.status,'new') NOT IN "
                  "('rejected','withdrawn','skipped','closed')")
        q += " ORDER BY r.score DESC, r.company COLLATE NOCASE"
        rows = con.execute(q, params).fetchall()
        if args.limit:
            rows = rows[: args.limit]

        arts = {}
        for a in con.execute("SELECT * FROM artifacts"):
            arts.setdefault(a["uid"], []).append(a)

        if args.json:
            out = []
            for r in rows:
                d = dict(r)
                d["artifacts"] = [
                    {"kind": a["kind"], "path": a["path"], "rating": a["rating"],
                     "gates": json.loads(a["gates"] or "{}")}
                    for a in arts.get(r["uid"], [])]
                out.append(d)
            print(json.dumps(out, indent=1, default=str))
            return 0

        for r in rows:
            docs = []
            for a in arts.get(r["uid"], []):
                if a["kind"] == "jd_snapshot":
                    continue
                bad = [k for k, v in json.loads(a["gates"] or "{}").items()
                       if v is False]
                mark = f" {a['rating']:.0f}/100" if a["rating"] else ""
                mark += f" [{len(bad)} gate(s) failed]" if bad else ""
                docs.append(f"{a['kind']}{mark}")
            status = "" if r["status"] == "new" else f"  [{r['status']}]"
            _say(f"{r['score']:>5.0f}  {r['title'][:52]:<52} {r['company'][:22]:<22}"
                 f"  {r['salary_label'] or 'unconfirmed':<20}{status}")
            _say(f"       {r['uid']}  {r['location'][:60]}")
            if docs:
                _say(f"       {' | '.join(docs)}")
        _say(f"\n{len(rows)} role(s)")
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    from .serve import serve
    return serve(db_path=args.db, host=args.host, port=args.port,
                 open_browser=not args.no_browser, docs_base=args.docs)


# ---------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    from .setup_wizard import run as wizard
    return wizard(Path(args.config or "config.yaml"),
                  non_interactive=args.defaults, cv=args.cv)


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
    s.add_argument("--db", default=None, help="database path (default data/job-radar.db)")
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
    ap.add_argument("target", help="a posting URL, a company name, or a uid")
    ap.add_argument("-s", "--status", default="applied",
                    help="new|interested|applied|submitted|interviewing|offer|"
                         "rejected|withdrawn|skipped|closed")
    ap.add_argument("--note", default=None)
    ap.add_argument("--db", default=None)
    ap.set_defaults(func=cmd_applied)

    g = sub.add_parser("generate", help="screen a role, or draft a CV or cover letter")
    g.add_argument("target", help="a posting URL, a company name, or a uid")
    g.add_argument("-k", "--kind", default="screen",
                   help="screen | cv | cover_letter")
    g.add_argument("--db", default=None)
    g.add_argument("--docs", default=None)
    g.set_defaults(func=cmd_generate)

    ls = sub.add_parser("list", help="the dashboard, as text")
    ls.add_argument("--status", default=None)
    ls.add_argument("--all", action="store_true", help="include settled roles")
    ls.add_argument("--limit", type=int, default=0)
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--db", default=None)
    ls.set_defaults(func=cmd_list)

    sv = sub.add_parser("serve", help="open the dashboard you can act from")
    sv.add_argument("--db", default=None)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--docs", default=None, help="where generated documents go")
    sv.add_argument("--no-browser", action="store_true")
    sv.set_defaults(func=cmd_serve)

    w = sub.add_parser("setup", help="build a config by answering a few questions")
    w.add_argument("--defaults", action="store_true", help="write a default config, ask nothing")
    w.add_argument("--cv", default=None, help="path to your CV (required with --defaults)")
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
