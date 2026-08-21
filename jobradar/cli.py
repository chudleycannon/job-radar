"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import adapters, output, sources as src_mod
from .config import Config, ConfigError, load as load_cfg
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

    if len(cfg.titles_include) > 6:
        _say(f"  note: only the first 6 of your {len(cfg.titles_include)} titles "
             f"are used as search terms (Workday uses 3). Order matters.")
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

    _say(f"  {ok}/{len(srcs)} responded, {len(all_jobs):,} postings")
    # "responded" is not "worked". A board that answers with nothing looks
    # identical to one that is not being watched at all, which is how five
    # hand-added charity boards were reported as healthy.
    empty = [r.source.company for r in results
             if r.ok and counts.get(r.source.key, 0) == 0]
    if empty and len(empty) <= 12:
        _say(f"  ! {len(empty)} source(s) responded with no postings at all: "
             f"{', '.join(empty)}")
        _say("    Run `job-radar validate` to see whether they are dead or "
             "just unreadable.")
    elif empty:
        _say(f"  ! {len(empty)} sources responded with no postings at all. "
             f"Run `job-radar validate`.")
    if throttled:
        _say(f"  ! {len(throttled)} sources look throttled (returned nothing "
             f"but have before): {', '.join(throttled[:6])}")

    kept, dropped = screen_run(all_jobs, cfg)

    # The database is the source of truth for what you already did about a
    # role. It beats whatever the scanner thinks of it today.
    from . import store
    con = store.connect(args.db)
    mig = {"roles": 0, "statuses": 0} if args.dry_run else store.migrate(con)
    if mig["roles"] or mig["statuses"]:
        _say(f"  migrated {mig['roles']} roles and {mig['statuses']} statuses "
             f"into the database")

    # A dry run must not touch the database. It used to insert every role and
    # increment the run counter, so trying the tool out once silently spent
    # the newness of everything it saw: the next real scan reported those
    # roles as already known. The help text promised the opposite.
    if args.dry_run:
        new_ids = set()
    else:
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

    # Read this BEFORE bumping, or it is always False and the first-run
    # message below is dead code: day one printed "0 new" and was
    # indistinguishable from a no-change repeat.
    first_run = store.current_run(con) == 0
    if not args.dry_run:
        store.bump_runs(con)
    new = [j for j in kept if j.uid in new_ids]
    seen = [j for j in kept if j.uid not in new_ids]
    if args.dry_run and kept:
        _say(f"  {len(kept)} match your config. Dry run, so nothing was "
             f"recorded and none can be marked new.")
    elif first_run and kept:
        # "0 new" on a first run reads as "we found nothing", when in fact
        # everything is new and there is nothing to compare against yet.
        _say(f"  {len(kept)} match your config. First run, so none are marked "
             f"new yet; from the next scan you will only be shown changes.")
    else:
        _say(f"  {len(kept)} match your config, {len(new)} new")
        if args.limit:
            # Boards 26..307 were never asked. Their roles enter the database
            # on the next full scan and are stamped new then, which is not
            # what new means.
            _say(f"  (only {args.limit} sources were read; roles on the rest "
                 f"will be marked new when a full scan first sees them)")
    if kept:
        _coverage_note(kept, srcs, cfg)

    if not kept:
        # An empty page reads as "the market is empty" when it usually means
        # the filters or the sources do not fit the person running it.
        _say("")
        _say("  Nothing matched. Where they went:")
        for reason, n in sorted(dropped.items(), key=lambda x: -x[1])[:5]:
            _say(f"    {n:>6}  {reason}")
        total_srcs = len(src_mod.load_file(src_mod.BUNDLED)) if cfg.use_bundled_sources else 0
        if cfg.sectors and total_srcs:
            _say(f"    your `sectors` setting cut the bundled list to {len(srcs)} "
                 f"of {total_srcs} sources")
        _say("")
        _say("  Most often this is the titles. Check `titles.include` in "
             f"{cfg.path} matches how postings are actually worded,")
        _say("  and add employers yourself with `job-radar discover <company>"
             " --add`.")

    meta = {
        "sources_ok": ok, "sources_total": len(srcs),
        # The raw count, matching what the CLI printed. The HTML used to sum
        # the drop reasons instead, which is post-dedupe, so the two numbers
        # disagreed by however many duplicate postings there were.
        "postings": len(all_jobs), "matching": len(kept),
        "new": len(new), "throttled": throttled, "dropped": dropped,
    }
    outdir = Path(args.out or cfg.out_dir)
    written = []
    if "html" in cfg.formats:
        written.append(output.html_out.write(
            outdir / "index.html", new=new, seen=seen, dropped=dropped,
            sources_ok=ok, sources_total=len(srcs), throttled=throttled,
            postings=len(all_jobs),
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
            # Telling someone to try the careers page URL when they just gave
            # you the careers page URL is a dead end with no next step in it.
            if target.startswith("http"):
                _say("  nothing found. Their careers page did not reveal a "
                     "job board this tool can read, and no board answered to "
                     "their name. Either it is rendered by JavaScript, or the "
                     "platform has no adapter yet. Look for the URL the "
                     "'View vacancies' link goes to and try that; if it is on "
                     "a platform listed under 'What this does not cover' in "
                     "the README, there is nothing to add.")
            else:
                _say("  nothing found. Try their careers page URL directly, "
                     "or the URL you land on after clicking through to their "
                     "vacancy list.")
            continue
        for f in found:
            if f.identity == "blocked":
                _say(f"  blocked. {f.note}")
                continue
            if f.identity == "unsupported":
                _say(f"  found their board: {f.note}")
                continue
            mark = {"ok": "verified", "mismatch": "WRONG COMPANY?",
                    "unchecked": "unverified"}.get(f.identity, f.identity)
            _say(f"  {f.platform:<16} {f.live_jobs:>4} jobs  [{mark}]  {f.url}")
            if f.note:
                _say(f"                   {f.note}")
            results.append(f)

    good = [f for f in results if f.live_jobs > 0 and f.identity != "mismatch"]
    if args.add and good:
        cfg_path = _cfg_path(args.config)
        if not cfg_path.exists():
            # Writing a file containing only a sources block produced a config
            # that then failed to load with "titles.include is empty", which
            # points at the wrong problem.
            _say(f"\nNo config at {cfg_path}. Run `job-radar setup` first, "
                 f"then re-run this with --add.")
            return 1
        n = _append_sources(cfg_path, [f.to_source() for f in good])
        if n:
            _say(f"\nAdded {n} source(s) to {cfg_path}")
        else:
            # It used to say "Added 1" while correctly writing nothing, so
            # running the same discover twice looked like it had duplicated
            # the entry.
            _say(f"\nAlready in {cfg_path}; nothing to add.")
    elif good and not args.add:
        _say("\nRe-run with --add to write these into your config.")
    return 0 if results else 1


# Keyword searches return leads, not postings: no description, no salary, and
# often an agency rather than the employer.
_KEYWORD_PLATFORMS = {"linkedin", "nhs"}


def _coverage_note(kept, srcs, cfg) -> None:
    """Say what this result is actually made of.

    "80 match your config" was true and useless: zero of the 265 employer
    boards had produced a hospitality role, the top ten were NHS service
    managers, and no command in the tool would say so. The scan already knows
    every match's platform and sector, so this is arithmetic on numbers it has
    computed. Naming the composition lets someone see in one line that their
    field is not represented, instead of after two hours and a hand-written
    audit of sources.json.
    """
    from collections import Counter
    boards = [j for j in kept if j.platform not in _KEYWORD_PLATFORMS]
    leads = len(kept) - len(boards)

    if not boards:
        _say(f"  none from an employer board: all {len(kept)} are keyword "
             f"search leads, which carry no description and usually no "
             f"salary, so your dealbreakers and salary floor never ran.")
    elif leads:
        _say(f"  {len(boards)} from employer boards, {leads} keyword search "
             f"leads (no description, usually no salary).")

    sec = Counter((j.sector or "untagged") for j in kept)
    top = ", ".join(f"{k} {n}" for k, n in sec.most_common(4))
    _say(f"  by sector: {top}")
    _say("  If your field is not in that list, the bundled employers do not "
         "cover it. `job-radar discover <employer> --add` is the fix; adding "
         "twenty employers beats any setting in the config.")


def _cfg_path(raw) -> Path:
    """The config path as given, with surrounding whitespace removed.

    A path pasted with a stray leading space produced "no config at
    ` /path/c.yaml`", which reads as the file being missing rather than as a
    typo in the argument.
    """
    return Path(str(raw or "config.yaml").strip()).expanduser()


def _cfg_or_default(raw) -> Config:
    """Load the config, or fall back to defaults only when none was asked for.

    Falling back silently when `-c` names a file that is not there meant a
    mistyped path produced a confident, complete, wrong answer: `coverage`
    reported the whole bundled list as though it were the user's own view.
    """
    p = _cfg_path(raw)
    if p.exists():
        return load_cfg(p)
    if raw:
        raise SystemExit(f"No config at {p}. Check the path, or run "
                         f"`job-radar setup -c {p}` to create it.")
    return Config()


def _append_sources(cfg_path: Path, new: list[Source]) -> int:   # used by discover --add
    """Append to `sources.extra` in place, keeping the file as written.

    Round-tripping through yaml.safe_dump rewrote the whole file and deleted
    every comment in it, including the one line that documents what
    `sources.extra` is -- so `--add` erased the explanation of `--add`.
    """
    import yaml
    text = cfg_path.read_text() if cfg_path.exists() else ""
    raw = yaml.safe_load(text) or {}
    have = {s.get("url") for s in ((raw.get("sources") or {}).get("extra") or [])
            if isinstance(s, dict)}
    add = [s for s in new if s.url not in have]
    if not add:
        return 0

    block = "".join(
        f"    - company: {s.company}\n      url: {s.url}\n"
        f"      platform: {s.platform}\n" for s in add)

    lines = text.splitlines(keepends=True)
    out, done = [], False
    for i, line in enumerate(lines):
        # An existing empty `extra: []` becomes a list; an existing list is
        # appended to at the end of its block.
        if not done and re.match(r"^\s{2}extra:\s*\[\s*\]\s*$", line):
            out.append("  extra:\n"); out.append(block); done = True
            continue
        if not done and re.match(r"^\s{2}extra:\s*$", line):
            out.append(line)
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or not lines[j].strip()):
                # A lone `[]` under the key is an empty-list placeholder, not
                # a member of the list. Copying it through and then appending
                # entries put a sequence after a scalar and broke the file.
                if lines[j].strip() not in ("[]", ""):
                    out.append(lines[j])
                j += 1
            out.append(block); done = True
            lines[i + 1:j] = []
            continue
        out.append(line)
    if not done:
        out.append("\nsources:\n  extra:\n" + block)

    # Never hand back a file no command can load. `--add` is often the first
    # thing someone runs after `setup`, and a config broken here takes every
    # other command down with it.
    result = "".join(out)
    try:
        yaml.safe_load(result)
    except yaml.YAMLError as e:
        raise SystemExit(
            f"Refusing to write {cfg_path}: the result would not parse "
            f"({str(e).splitlines()[0]}). Add this by hand under "
            f"sources.extra:\n{block}")
    cfg_path.write_text(result)
    return len(add)


# ---------------------------------------------------------------- validate
def cmd_validate(args) -> int:
    cfg = _cfg_or_default(args.config)
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
    cfg = _cfg_or_default(args.config)
    srcs = src_mod.load_file(args.file) if args.file else src_mod.load(cfg)
    cov = src_mod.coverage(srcs)
    _say(f"{cov['total']} sources\n")
    for label, key in (("By sector", "by_sector"), ("By country", "by_country"),
                       ("By platform", "by_platform")):
        _say(label)
        for k, v in cov[key].items():
            _say(f"  {v:>5}  {k}")
        _say()

    # The command is documented as "where the list is thin", but it used to
    # be a static dump that never looked at the config. A hospitality manager
    # ran it, saw a healthy-looking 307, and had no way to learn there were
    # zero hospitality employers on it.
    if not args.file:
        if cfg.sectors:
            bundled = len(src_mod.load_file(src_mod.BUNDLED)) if cfg.use_bundled_sources else 0
            if bundled:
                _say(f"Your `sectors` setting narrows the bundled list to "
                     f"{cov['total']} of {bundled} sources.")
        keyword = sum(n for k, n in cov["by_platform"].items()
                      if k in ("linkedin", "nhs"))
        if keyword:
            _say(f"{keyword} of these are keyword searches rather than "
                 f"employer boards: they return leads with no description "
                 f"and usually no salary, and they include agencies.")
        untagged = cov["by_country"].get("untagged", 0)
        if cfg.source_countries and untagged:
            _say(f"`sources.countries` only removes sources that carry a "
                 f"country tag. {untagged} here carry none and are always "
                 f"fetched.")
        _say("Nothing in your field? `job-radar discover <employer> --add` "
             "adds their board. Adding twenty employers does more for you "
             "than any setting in the config.")
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

    # `job-radar list` prints a shortened uid, so accept what it printed.
    # Copying the visible id and being told "could not identify a role" is a
    # dead end with no next step in it.
    if _re.fullmatch(r"[0-9a-f]{6,15}", t):
        rows = con.execute("SELECT uid, company, title FROM roles "
                           "WHERE uid LIKE ?", (t + "%",)).fetchall()
        if len(rows) == 1:
            return rows[0]["uid"], ""
        if len(rows) > 1:
            return None, f"{len(rows)} roles start with {t!r}; give more of it"

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
        row = con.execute("SELECT company, title, description FROM roles "
                          "WHERE uid=?", (uid,)).fetchone()
        # Screening a posting with no body spends money to be told there is
        # nothing to read. For some users that is most of their results.
        if args.kind == "screen" and len((row["description"] or "").strip()) < 200:
            _say(f"{row['company']} - {row['title'][:56]}")
            _say("  This posting has no description, so there is nothing to "
                 "screen against your dealbreakers.")
            _say("  Open the advert and screen it by hand, or use --force to "
                 "spend the tokens anyway.")
            if not args.force:
                return 1
        job_id = store.enqueue(con, uid, args.kind)
        _say(f"{row['company']} - {row['title'][:56]}")
        _say(f"  {args.kind}, job {job_id}. This spends tokens.")
    finally:
        con.close()

    # Without config_path this resolves a config from the working directory,
    # so a run with -c pointed elsewhere gets screened against whatever
    # config.yaml happens to be next to it. That is how a nurse's role came
    # back screened against the author's job titles.
    runner.run_job(job_id, db_path=args.db, base=args.docs,
                   config_path=args.config)

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
                if a["summary"]:
                    _say(f"  {a['summary']}")
                _say(f"  wrote {a['path']}{rating}")
                gates = json.loads(a["gates"] or "{}")
                # A screen has no gates to run. Printing "all passed" against
                # an empty dict read as a clean bill of health on a posting
                # that had not been read at all.
                if gates:
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
        q = ("SELECT r.*, COALESCE(s.status,'new') status, "
             "COALESCE(s.note,'') note FROM roles r "
             "LEFT JOIN role_state s ON s.uid=r.uid")
        params = []
        if args.status and args.status not in store.STATUSES:
            _say(f"status must be one of: {', '.join(store.STATUSES)}")
            return 1
        where = []
        if args.status:
            where.append("COALESCE(s.status,'new')=?")
            params.append(args.status)
        elif not args.all:
            where.append("COALESCE(s.status,'new') NOT IN "
                         "('rejected','withdrawn','skipped','closed')")
        if args.new:
            # Roles first seen on the most recent scan date. This is the
            # question a daily user actually has, and until now the only
            # answer was a count printed by `scan` that scrolled away.
            where.append(store.NEW_SQL)
        elif not args.all:
            # Same window the dashboard uses, so the two views agree. Without
            # it `list` accumulated every role ever seen and reported roles
            # that had not been on a board for weeks.
            where.append(store.LIVE_SQL + " OR COALESCE(s.status,'new') <> 'new'"
                         " OR r.uid IN (SELECT DISTINCT uid FROM artifacts)")
        if where:
            q += " WHERE " + " AND ".join(f"({w})" for w in where)
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
                # These are TEXT columns holding JSON. Passing them straight
                # into json.dumps double-encoded them, so every consumer had
                # to parse a string inside the parsed document.
                for k in ("reasons", "flags"):
                    try:
                        d[k] = json.loads(d.get(k) or "[]")
                    except (json.JSONDecodeError, TypeError):
                        d[k] = []
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
                if a["summary"]:
                    mark += f" {a['summary'][:40]}"
                mark += f" [{len(bad)} gate(s) failed]" if bad else ""
                docs.append(f"{a['kind']}{mark}")
            status = "" if r["status"] == "new" else f"  [{r['status']}]"
            _say(f"{r['score']:>5.0f}  {r['title'][:52]:<52} {r['company'][:22]:<22}"
                 f"  {r['salary_label'] or 'unconfirmed':<20}{status}")
            _say(f"       {r['uid']}  {r['location'][:60]}")
            if r["note"]:
                _say(f"       note: {r['note']}")
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
                 open_browser=not args.no_browser, docs_base=args.docs,
                 config_path=args.config)


# ---------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    from .setup_wizard import run as wizard
    return wizard(_cfg_path(args.config),
                  non_interactive=args.defaults, cv=args.cv, titles=args.titles)


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
    g.add_argument("--force", action="store_true",
                   help="screen even when the posting has no description")
    g.set_defaults(func=cmd_generate)

    ls = sub.add_parser("list", help="the dashboard, as text")
    ls.add_argument("--status", default=None)
    ls.add_argument("--all", action="store_true",
                    help="include settled roles and ones no longer on a board")
    ls.add_argument("--new", action="store_true",
                    help="only roles first seen on the most recent scan")
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
    w.add_argument("--titles", default=None,
                   help="comma-separated job titles (required with --defaults)")
    w.set_defaults(func=cmd_setup)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        _say(str(e))
        return 1
    except ConfigError as e:
        # A config mistake is the user's to fix, and a traceback buries the
        # one line that tells them how.
        _say(f"Problem in your config: {e}")
        return 1
    except KeyboardInterrupt:
        _say("\nstopped")
        return 130


if __name__ == "__main__":
    sys.exit(main())
