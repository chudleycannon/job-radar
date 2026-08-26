"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import adapters, output, sources as src_mod
from .config import Config, ConfigError, load as load_cfg
from .discover import discover as run_discover, prunable as row_prunable, validate_source
from . import fetch as fetch_defaults
from .fetch import (HostLimiter, detect_throttling, fetch_all,
                    interleave_by_host, pace_this_thread,
                    pinned_to_one_page)
from .models import Source
from .screen import run as screen_run, _countries_in
from .state import State, atomic_write_text


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _load_sources(cfg: Config) -> list[Source]:
    """The configured sources, saying out loud which ones were unusable.

    A templated URL asking for a placeholder this tool cannot supply used to
    raise straight out of `sources.load`, so one bad line in `sources.extra`
    stopped every command that reads the list. It is now skipped, and skipping
    something silently is the other half of that bug, so it is named here.
    """
    problems: list = []
    srcs = src_mod.load(cfg, problems=problems)
    for company, why in problems[:10]:
        _say(f"  ! skipped {company}: {why}")
    if len(problems) > 10:
        _say(f"  ! and {len(problems) - 10} more source(s) with URLs that "
             f"cannot be filled in")
    return srcs


# ---------------------------------------------------------------- scan
def cmd_scan(args) -> int:
    cfg = load_cfg(args.config)
    srcs = _load_sources(cfg)
    # Whether the limit actually cut anything, rather than merely whether one
    # was asked for. `--limit 20000` against a 13,440-source config read every
    # one of them and still announced "only 20000 sources were read".
    all_srcs = len(srcs)
    if args.limit:
        srcs = srcs[: args.limit]
    truncated = len(srcs) < all_srcs
    if not srcs:
        _say("No sources. Run `job-radar setup` or check sources.use_bundled.")
        return 1

    state = State(Path(args.state) if args.state else None)
    _say(f"Fetching {len(srcs)} sources at concurrency {cfg.concurrency}...")
    # A config written before per-host pacing existed will still be carrying
    # the old advice to keep this number tiny, and nothing else would ever tell
    # its owner that the advice changed. At four workers against seventeen
    # thousand sources that setting is worth more than an hour a scan, and it
    # buys no politeness now that each host is paced on its own clock.
    if cfg.concurrency < fetch_defaults.DEFAULT_CONCURRENCY and len(srcs) > 500:
        _say(f"  ! concurrency is {cfg.concurrency}. Each host is now paced "
             f"separately, so this number only sets how many DIFFERENT boards "
             f"are read at once, and {len(srcs):,} sources at "
             f"{cfg.concurrency} is mostly waiting. "
             f"`fetch.concurrency: {fetch_defaults.DEFAULT_CONCURRENCY}` in "
             f"{cfg.path or 'your config'} is the new default.")

    done = {"n": 0}
    all_jobs: list = []
    counts: dict[str, int] = {}
    absorbed: set = set()
    ok = 0

    def absorb(res):
        """Turn one fetched source into jobs. Called at most once per result.

        Idempotent by identity, because it runs from two places: `tick`, while
        the fetch is still going, and a sweep afterwards for anything `tick`
        never saw. It must not be possible for a result to be counted twice --
        `ok` and `all_jobs` would both inflate, and inflating the posting count
        is the shape of bug this file keeps finding.
        """
        nonlocal ok
        if id(res) in absorbed or not res.ok:
            return
        absorbed.add(id(res))
        ok += 1
        jobs = adapters.parse(res.payload, res.source)
        for j in jobs:
            j.sector = j.sector or res.source.sector
            # The posting's own location beats the board's tag. A board is
            # tagged with where its vacancies usually are, which is a fair
            # default and a bad override: Homebase's board is tagged UK
            # because that is a UK retailer, and a genuine Toronto vacancy on
            # it was being stored as UK. The tag is only a fallback for a
            # posting that names nowhere, and it is only used when it names
            # exactly one country, since "multiple" is not one.
            if not j.country:
                here = _countries_in(j.location or "")
                tag = res.source.country or ""
                # One spelling, defined in sources.py and normalised as the
                # list is loaded. This used to accept both `multi` and
                # `multiple` because the shipped list held both, which meant
                # neither was the right one and the next consumer would handle
                # whichever it happened to meet.
                if tag in src_mod.NON_COUNTRY_TAGS:
                    tag = ""            # not a country, never store it as one
                if len(here) == 1:
                    j.country = here.pop()
                elif here:
                    # Several countries named. The board's tag is only usable
                    # if it is one of them: "London / New York" on a UK board
                    # really is partly UK, "Berlin / Paris" is not.
                    j.country = tag if tag in here else ""
                else:
                    j.country = tag
        counts[res.source.key] = len(jobs)
        all_jobs.extend(jobs)

    def tick(res):
        """Count the source, and parse it while the fetch is still running.

        The parsing used to be a second pass over `results` after `fetch_all`
        had returned. Moving it in here is worth about two minutes of wall
        clock on a full scan and changes nothing else, because `fetch_all`
        calls this from its own `as_completed` loop: same thread, same
        completion order, same one-call-per-result. The old second pass ran on
        that same thread too, just later.

        What it buys is overlap. The scan's floor is not this machine, it is
        apply.workable.com's pacing -- 2,094 boards at 0.7 requests a second
        is 50 minutes on its own, and measured across a 179-board sample only
        about five of sixteen workers are busy at any moment. So the thread
        running this callback spends most of the run blocked in
        `as_completed` with nothing to do, and parsing roughly 480,000
        postings at 262 microseconds each is ~126 seconds of work that now
        happens inside time that was already being spent waiting.

        Deliberately only the parsing. Screening cannot move here: it starts
        with `dedupe` across the whole set, so it has nothing to do until
        every source is in.
        """
        done["n"] += 1
        if done["n"] % 25 == 0:
            _say(f"  {done['n']}/{len(srcs)}")
        absorb(res)

    if len(cfg.titles_include) > 6:
        _say(f"  note: only the first 6 of your {len(cfg.titles_include)} titles "
             f"are used as search terms (Workday uses 3). Order matters.")
    # Reed is the one source that needs a credential, and without one it can
    # only 401. Say so here, once, by name: buried in the list of sources that
    # "could not be read" it looks like a broken board rather than a two
    # minute signup.
    if not cfg.reed_api_key and any(s.platform == "reed" for s in srcs):
        _say("  ! Reed is in your sources but there is no API key, so it will "
             "be skipped.")
        _say("    Free key: https://www.reed.co.uk/developers/jobseeker  "
             "Then set sources.reed_api_key or $REED_API_KEY.")
    # Same for Adzuna, which without credentials can only answer 400 with an
    # HTML error page. That reads as a broken source, not as a signup.
    if (not (cfg.adzuna_app_id and cfg.adzuna_app_key)
            and any(s.platform == "adzuna" for s in srcs)):
        _say("  ! Adzuna is in your sources but there are no credentials, so "
             "it will be skipped.")
        _say("    Free app_id and app_key: https://developer.adzuna.com/signup  "
             "Then set sources.adzuna_app_id and sources.adzuna_app_key, or "
             "$ADZUNA_APP_ID and $ADZUNA_APP_KEY.")

    # No silent truncation. A keyword platform is searched with the first
    # MAX_KEYWORD_TITLES titles, and a config with more than that was quietly
    # having the rest ignored: LinkedIn, NHS Jobs, Reed, Adzuna and the
    # Workable search all never looked for them.
    _skipped = src_mod.dropped_titles(cfg.titles_include)
    if _skipped:
        _say(f"  note: the keyword searches use your first "
             f"{src_mod.MAX_KEYWORD_TITLES} titles, so these are not searched "
             f"for there: {', '.join(_skipped)}")
        _say("  they are still matched against every employer board.")

    results = fetch_all(
        srcs, concurrency=cfg.concurrency, timeout=cfg.timeout,
        retries=cfg.retries, user_agent=cfg.user_agent,
        search_terms=cfg.titles_include,
        api_keys={"reed": cfg.reed_api_key,
                  "adzuna_app_id": cfg.adzuna_app_id,
                  "adzuna_app_key": cfg.adzuna_app_key}, on_result=tick,
    )

    # `tick` has already parsed everything `fetch_all` handed it, so on the
    # real path this loop finds nothing to do and costs one set lookup per
    # source. It is not dead code: it is what keeps the parsing an OPTIMISATION
    # rather than a contract. A `fetch_all` that returns its results without
    # calling `on_result` is a perfectly reasonable thing to write -- the
    # parameter is optional and several tests stub exactly that -- and without
    # this sweep such a caller would silently scan zero postings and report
    # "Nothing matched" on a full board. `absorb` is idempotent, so anything
    # already taken in is skipped rather than double counted.
    for res in results:
        absorb(res)

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
    # Name the host, not just the boards. "2,094 sources look throttled" is a
    # list of employers to squint at; "apply.workable.com has blocked you for
    # another 15h" is the actual fact, and it is the one that tells you the
    # boards are unknown today rather than empty. Measured live: Workable
    # answered 429 with Retry-After 57841 after a scan aimed every worker at
    # it, and 2,094 boards came back with nothing for the rest of the run.
    blocks: dict[str, int] = {}
    for res in results:
        if not res.throttled:
            continue
        host = urlparse(res.source.url).netloc
        m = re.search(r"(\d+)s", res.error or "")
        if m:
            blocks[host] = max(blocks.get(host, 0), int(m.group(1)))
    for host, secs in sorted(blocks.items(), key=lambda kv: -kv[1]):
        n = sum(1 for r in results
                if r.throttled and urlparse(r.source.url).netloc == host)
        # Formatted rather than hard-coded to hours: the breaker's own block
        # is five minutes, and "0h 5m" reads like a bug in the tool.
        gap = (f"{secs // 3600}h {secs % 3600 // 60}m" if secs >= 3600
               else f"{secs // 60}m {secs % 60}s" if secs >= 60
               else f"{secs}s")
        _say(f"  ! {host} is rate-limiting this connection, so the next "
             f"request there waits {gap}. "
             f"{n} source(s) there are UNKNOWN today, not empty. They are "
             f"left alone rather than recorded as having no jobs.")
    # The other silent failure, and the harder one to see. A throttled source
    # returns nothing and at least looks wrong; a source that returns exactly
    # one page looks perfectly healthy. Tesco's Avature board returned 10 of
    # "999+" and `validate` called it live.
    pinned = pinned_to_one_page(counts, srcs)
    if pinned:
        _say(f"  ! {len(pinned)} source(s) returned exactly one page of their "
             f"platform, which can mean paging stopped early rather than that "
             f"the board is that size: {', '.join(pinned[:6])}")

    # A heuristic above, a fact here. `pinned_to_one_page` guesses from the
    # count; this is the pager itself saying it stopped because it ran out of
    # allowance rather than because the board ran out of postings. Every paged
    # fetcher has a cap, because a broken stop condition with no cap behind it
    # is an infinite loop, and until now every one of them was silent about
    # hitting it: the first 200 of 1,055 came back looking exactly like a
    # complete answer.
    capped = sorted({r.source.company for r in results if r.truncated})
    if capped:
        _say(f"  ! {len(capped)} source(s) had more to give and were cut off at "
             f"the page limit, so these are incomplete: {', '.join(capped[:6])}")

    kept, dropped = screen_run(all_jobs, cfg)

    # The database is the source of truth for what you already did about a
    # role. It beats whatever the scanner thinks of it today.
    from . import store
    # A dry run touches no file it was not pointed at. It used to create the
    # database anyway, empty, purely because connecting creates it, which made
    # "this writes nothing" untrue in the one mode people use to check exactly
    # that before trusting the tool.
    con = store.connect(":memory:" if args.dry_run else args.db)
    # The legacy import follows the database, not the working directory.
    # `store.migrate(con)` resolved both of its sources against the cwd, so a
    # scan started in the repo with `--db /tmp/scratch.db` still read this
    # directory's state/seen.json and applications.local.yaml and copied 1,526
    # roles and a real application history into the scratch file. `--db` reads
    # as isolation and was not one, and the result is somebody's job search in
    # a temp directory they will not think to clear.
    #
    # A database that is the configured one keeps the old behaviour, because
    # that is the upgrade path this function exists for.
    own_db = not args.db or Path(args.db) == store.DEFAULT_PATH
    mig = ({"roles": 0, "statuses": 0} if args.dry_run else
           store.migrate(con,
                         state_path=str(state.path) if own_db else "",
                         apps_path=None if own_db else ""))
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
        #
        # The previous wording said "none are marked new yet", and then the
        # dashboard's New tab showed all of them, because the two count
        # different things: this line is per-RUN, and the tab is per-DATE.
        # Both are defensible on their own and the pair is a contradiction on
        # the one day a person has no idea which to believe. So this says what
        # the reader will actually see.
        _say(f"  {len(kept)} match your config. This is the first scan, so all "
             f"of them are new and the dashboard shows them that way; from the "
             f"next scan this line reports only what changed.")
    else:
        _say(f"  {len(kept)} match your config, {len(new)} new")
    if truncated and kept and not args.dry_run:
        # Boards 26..307 were never asked. Their roles enter the database on
        # the next full scan and are stamped new then, which is not what new
        # means.
        #
        # Outside the else, because it used to sit inside it and a FIRST run
        # took the branch above -- so `job-radar scan --limit 200`, which is
        # the quick look the wizard recommends by name, never once said it had
        # read a fraction of the list to the one person who most needed to
        # know. The number is what was really read, not what was asked for.
        _say(f"  (only {len(srcs):,} of your {all_srcs:,} sources were read; "
             f"roles on the rest will be marked new when a full scan first "
             f"sees them)")
    if not args.dry_run:
        # Collapse copies of the same job that arrived from different sources
        # on different runs, and repair links built with a path that 404s.
        # Both are about rows already stored, which scan-time dedupe cannot
        # reach.
        fixed = store.repair_smartrecruiters_urls(con)
        if fixed:
            _say(f"  repaired {fixed} broken apply link(s)")
        dupes = store.merge_duplicates(con, cfg)
        if dupes:
            _say(f"  merged {dupes} duplicate(s) into the employer's own listing")

    if not args.dry_run and not args.no_enrich:
        _enrich_step(con, cfg)

    if kept:
        _coverage_note(kept, srcs, cfg)
    _staleness_note(cfg)

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
    # A dry run prints "nothing was recorded", which was true of the database
    # and false of the filesystem: it still overwrote out/index.html and
    # out/roles.json, so `--limit 200 --dry-run` replaced a full dashboard
    # with a 200-source sample of one.
    if args.dry_run:
        _say("  (dry run, so out/ was left alone)")
    elif "html" in cfg.formats:
        written.append(output.html_out.write(
            outdir / "index.html", new=new, seen=seen, dropped=dropped,
            sources_ok=ok, sources_total=len(srcs), throttled=throttled,
            postings=len(all_jobs),
        ))
    if not args.dry_run and "json" in cfg.formats:
        written.append(output.write_json(outdir / "roles.json", new, seen, meta))
    if not args.dry_run and ("markdown" in cfg.formats or "md" in cfg.formats):
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
            if f.identity == "unreadable":
                # The board exists; the fetch failed. Saying "nothing found,
                # either it is rendered by JavaScript or the platform has no
                # adapter yet" about a board we located and named is three
                # false statements at once. Counted as a result so the exit
                # code is a success, but never handed to `--add`: an unread
                # board is unverified, and writing it into the config would
                # bank a guess we could not check.
                _say(f"  {f.platform:<16}    ?  jobs  [could not read]  {f.url}")
                _say(f"                   found, but {f.note}")
                _say(f"                   not added; try again later")
                results.append(f)
                continue
            mark = {"ok": "verified", "mismatch": "WRONG COMPANY?",
                    "unchecked": "unverified"}.get(f.identity, f.identity)
            _say(f"  {f.platform:<16} {f.live_jobs:>4} jobs  [{mark}]  {f.url}")
            if f.note:
                _say(f"                   {f.note}")
            results.append(f)

    good = [f for f in results if f.live_jobs > 0
            and f.identity not in ("mismatch", "unreadable")]
    if args.add and good:
        # The write path, not the read path. `--add` edits a config, so it is
        # subject to the same rule as `setup`: it must never land on a file
        # the repo distributes. The two were split when setup was found
        # writing the tracked config.yaml on a fresh clone, and this caller
        # was left on the old one, which is the half of that bug fb6cc68
        # already failed to fix once.
        cfg_path = _cfg_write_path(args.config)
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


# After this long without a check, the bundled list is worth refreshing. Chosen
# to be quiet: the upstream job runs weekly, so anything under a month means
# the person is simply a few merges behind and nothing is likely broken yet.
# Upstream revalidates and extends the list weekly, so eight days means a
# missed cycle. Past that you are not looking at a slightly older list, you
# are looking at one that has started losing boards as employers migrate.
STALE_AFTER_DAYS = 8


def _enrich_step(con, cfg) -> None:
    """Fetch the full posting for roles whose source gave only a headline.

    Part of the scan rather than a separate command you have to know about.
    It is a read, it spends no tokens, and without it a quarter of the board
    is unscreenable, unrankable and invisible to the salary floor: dealbreakers
    have no text to match, so they pass by default, which is the worst way for
    a filter to fail.
    """
    from . import enrich
    rows = enrich.candidates(con)
    if not rows:
        return
    _say(f"  fetching {len(rows)} postings that arrived as headlines only...")
    # Left on `enrich.run`'s own default of `fetch.DEFAULT_CONCURRENCY`, and
    # NOT wired to `cfg.concurrency`, which looks like the obvious tidy-up and
    # is a regression. Measured: this pass runs at 8.1 postings a second at 16
    # workers and 13.1 at 32, and the last full scan enriched 958 postings, so
    # the whole step is about two minutes of a fifty-minute run -- there is
    # nothing here worth chasing. Meanwhile plenty of configs still carry the
    # `concurrency: 4` the old advice recommended, and honouring that number
    # here would take those runs from 16 workers down to 4 and turn two
    # minutes into eight.
    got, tried = enrich.run(con, cfg, rows)
    if got:
        dropped = _rescreen(con, cfg)
        _say(f"  filled in {got} of {tried}; they can now be screened, ranked "
             f"and compared to your salary floor")
        if dropped:
            _say(f"  {dropped} of them failed a rule once their text was "
                 f"readable and have been hidden")
    else:
        _say(f"  none of the {tried} could be fetched. They stay as listings.")


def _rescreen(con, cfg) -> int:
    """Re-apply the filters to roles whose description has just arrived.

    Screening happens during the scan, against whatever text the source
    returned, which for LinkedIn, Workday and SmartRecruiters is nothing. So
    the filters ran on an empty string: a hard dealbreaker cannot match text
    that is not there, and a salary floor cannot compare a figure that was
    never parsed. Enrichment then fetched the text and nothing looked at it,
    which is the worst of both -- the tool had the sentence that disqualifies
    the role and showed the role anyway.
    """
    from . import screen as screen_mod, store
    from .models import Job, Salary
    import json as _json

    dropped = 0
    rows = con.execute(
        "SELECT r.* FROM roles r LEFT JOIN role_state s ON s.uid=r.uid "
        "WHERE COALESCE(s.status,'new')='new' "
        f"AND {store.LIVE_SQL} "
        "AND LENGTH(TRIM(COALESCE(r.description,'')))>=200").fetchall()
    for r in rows:
        job = Job(company=r["company"], title=r["title"], url=r["url"],
                  location=r["location"] or "", platform=r["platform"] or "",
                  description=r["description"] or "",
                  salary=Salary(min=r["salary_min"], max=r["salary_max"],
                                currency=r["salary_currency"],
                                period=r["salary_period"] or "year",
                                confirmed=bool(r["salary_confirmed"]),
                                raw=r["salary_label"]))
        keep, _hits = screen_mod.screen(job, cfg)
        if keep:
            keep, _why = screen_mod.apply_salary(job, cfg)
        if not keep:
            # Settle it rather than delete it: a role you were shown and then
            # told was wrong is worth being able to look back at, and deleting
            # it would make it "new" again on the next scan.
            store.set_status(con, r["uid"], "closed",
                             "hidden after its full description was read")
            dropped += 1
            continue
        # The old flags were written against an empty description and now
        # claim things that are no longer true.
        flags = [f for f in _json.loads(r["flags"] or "[]")
                 if "not screened" not in f]
        flags += [f for f in job.flags if f not in flags]
        con.execute("UPDATE roles SET flags=? WHERE uid=?",
                    (_json.dumps(flags), r["uid"]))
    return dropped


def _staleness_note(cfg) -> None:
    """Tell people their copy of the source list ages, and how to refresh it.

    Nothing said this anywhere. The weekly validation and growth jobs run
    upstream and open pull requests there; a clone freezes its list on the day
    it was cloned, and a fork only prunes its own, because the crawler that
    finds new employers deliberately does not ship in this repository. So a
    six-month-old checkout quietly loses boards as they migrate and never
    gains the ones that were added, while looking exactly as healthy as a
    fresh one.
    """
    if not cfg.use_bundled_sources:
        return
    days = src_mod.age_days()
    if days is None:
        return
    if days < STALE_AFTER_DAYS:
        return
    _say("")
    _say(f"  Your source list was last checked {days} days ago, and upstream "
         f"checks it weekly.")
    _say(f"  `git pull` gets you boards that have moved since and employers "
         f"added since. Without it this scan is quietly missing roles.")


def _daily_sync_nudge(cfg, db=None) -> None:
    """Say it once a day, on whatever command you happen to run.

    A warning attached only to `scan` reaches someone who scans. Someone who
    lives in `list` and `serve` never sees it, and their list is the one most
    likely to be old. Once a day is the honest frequency: often enough to
    matter within a week of a missed cycle, rare enough that it never becomes
    something to scroll past.
    """
    if not cfg.use_bundled_sources:
        return
    days = src_mod.age_days()
    if days is None or days < STALE_AFTER_DAYS:
        return
    from . import store
    from datetime import date
    con = store.connect(db)
    try:
        today = date.today().isoformat()
        if store.get_meta(con, "sync_nudge", "") == today:
            return
        store.set_meta(con, "sync_nudge", today)
    finally:
        con.close()
    _say(f"Your source list was last checked {days} days ago; upstream checks "
         f"it weekly. Run `git pull` to pick up boards that have moved and "
         f"employers added since.\n")


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
    if raw:
        return Path(str(raw).strip()).expanduser().resolve()
    # config.local.yaml is the personal one and is gitignored; config.yaml
    # ships. Defaulting to the latter meant `discover <employer> --add` wrote
    # somebody's own board into the file the repo distributes, which is either
    # committed by accident or silently lost on the next pull.
    local = Path("config.local.yaml")
    return (local if local.exists() else Path("config.yaml")).resolve()


def _cfg_write_path(raw) -> Path:
    """Where `setup` and `discover --add` are allowed to write.

    Reading and writing want different answers. Reading should find whichever
    config exists. Writing must never land on a file the repo distributes: on
    a fresh clone neither personal config exists, so the read path fell
    through to `config.yaml`, and `job-radar setup` reported "Wrote
    config.yaml" while `git status` reported `M config.yaml`, 22 insertions
    and 43 deletions against a tracked file. Every later `git pull`
    conflicted, and on a public fork it is the user's own CV path that gets
    committed.

    Upstream no longer tracks `config.yaml`, so writing it on a fresh clone is
    now correct and creates an untracked file. That is deliberately still the
    default rather than `config.local.yaml`, because the GitHub Actions path
    documented in the README needs a config a runner can see, and a runner
    only sees what was committed.
    """
    return _cfg_path(raw)


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
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
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
    # Atomic, for the same reason as the check above: this rewrites a config
    # the user wrote by hand, and half of one is not recoverable from anything
    # this tool holds.
    atomic_write_text(cfg_path, result)
    return len(add)


# ---------------------------------------------------------------- validate
def cmd_validate(args) -> int:
    cfg = _cfg_or_default(args.config)
    srcs = src_mod.load_file(args.file) if args.file else _load_sources(cfg)
    if args.limit:
        srcs = srcs[: args.limit]
    _say(f"Validating {len(srcs)} sources...")

    rows, dead, mismatch, unread = [], [], [], []
    from concurrent.futures import ThreadPoolExecutor

    # Paced per host, and the six-worker cap is gone with it. The cap was the
    # only brake this command had, and it was the wrong one: it slowed the
    # ~7,777 hosts holding a single board each without doing anything about the
    # four thousand consecutive Greenhouse boards. Unpaced, a burst here reads
    # as a dead board, and `--prune` then deletes a live employer.
    limiter = HostLimiter()

    def paced(src):
        pace_this_thread(limiter)
        # A URL with a placeholder nothing can fill in raises inside
        # `validate_source`, and that exception comes back out of `ex.map`
        # below and ends the run: one odd source in `sources.extra` meant the
        # thousands after it were never checked and nothing said so. Report it
        # as its own row instead. "unreachable" is the honest verdict -- it
        # was never read -- and unreachable rows are never pruned, which is
        # right, because a URL this tool cannot build is not evidence that the
        # employer stopped hiring.
        bad = src_mod.url_template_error(src)
        if bad:
            return {"company": src.company, "url": src.url,
                    "platform": src.platform, "live_jobs": 0,
                    "verdict": "unreachable", "transport": None,
                    "prunable": False,
                    "note": f"could not be read: {bad}"}
        return validate_source(src)

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as ex:
        for i, row in enumerate(ex.map(paced, interleave_by_host(srcs)), 1):
            rows.append(row)
            if row["verdict"] == "dead":
                dead.append(row)
            elif row["verdict"] == "unreachable":
                unread.append(row)
            elif row["verdict"] == "mismatch":
                mismatch.append(row)
            if i % 25 == 0:
                _say(f"  {i}/{len(srcs)}")

    _say(f"\n  live: {len(rows) - len(dead) - len(unread)}   "
         f"dead: {len(dead)}   unreachable: {len(unread)}   "
         f"identity mismatch: {len(mismatch)}")
    for r in dead[:40]:
        _say(f"  DEAD      {r['company']} <- {r['url']}")
    for r in unread[:20]:
        _say(f"  UNREAD    {r['company']}: {r['note']}")
    if unread:
        _say(f"  {len(unread)} could not be read and are left alone. They are "
             f"not dead; try again later.")
    for r in mismatch[:40]:
        _say(f"  MISMATCH  {r['company']}: {r['note']}")

    if args.report:
        # Atomic. This is the only durable trace of a validation run that took
        # hours of network, and it is what `--prune` is argued from. Killed
        # part way through, the run's own results are gone either way, but the
        # previous report is not, and half a JSON file is not a report.
        atomic_write_text(Path(args.report), json.dumps({
            "checked": datetime.now().isoformat(timespec="seconds"),
            "total": len(rows), "dead": dead, "mismatch": mismatch, "rows": rows,
        }, indent=1))
        _say(f"  wrote {args.report}")

    if args.prune and not args.file:
        _say("  --prune needs --file: it rewrites a source list, and there is "
             "no file to rewrite without one.")
    if args.prune and args.file:
        # A prune has to be able to tell "these boards are gone" from "this
        # machine has no network". A failed fetch is now its own verdict and
        # is never pruned, which is the real fix. This threshold stays as the
        # second line: if a platform starts answering 200 with an empty array
        # instead of an error, its whole tenancy looks dead at once and no
        # per-request check would catch it. This runs unattended every Sunday
        # in Actions, on runners the README itself says get throttled sooner
        # than a laptop.
        share = len(dead) / max(1, len(rows))
        if share > 0.25 and len(dead) > 5:
            _say(f"\n  REFUSING TO PRUNE: {len(dead)} of {len(rows)} sources "
                 f"({share:.0%}) came back empty.")
            _say("  That is a platform or rate-limit problem, not that many "
                 "boards dying at once. Nothing was changed.")
            _say("  Re-run when the connection is good, or use --force-prune "
                 "if the list really has collapsed.")
            if not args.force_prune:
                return 1
        # Deleted on the row's own `prunable` flag, not on its verdict.
        # `discover` sets that flag, and it is stricter than "the verdict says
        # dead": a row that never reached HTTP, or that came back with a TLS
        # alert, can never be deletable however the verdict reads. Arguing
        # from the verdict here happened to agree with the flag today, which
        # is not the same as being right, and the day they disagree the cost
        # is a live employer deleted from the shipped list.
        prunable_rows = [r for r in rows if row_prunable(r)]
        held = len([r for r in dead if not row_prunable(r)])
        if held:
            _say(f"  {held} source(s) read as dead but are not deletable "
                 f"(nothing reached the board), so they are kept.")
        dead_urls = {r["url"] for r in prunable_rows}
        keep = [s for s in srcs if s.url not in dead_urls]
        src_mod.save(keep, args.file, meta={"pruned": len(srcs) - len(keep),
                                            "checked": datetime.now().date().isoformat()})
        _say(f"  pruned {len(srcs) - len(keep)} dead sources from {args.file}")
    return 0


# ---------------------------------------------------------------- coverage
def cmd_coverage(args) -> int:
    cfg = _cfg_or_default(args.config)
    srcs = src_mod.load_file(args.file) if args.file else _load_sources(cfg)
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
        # Derived from the list rather than named here. The hardcoded pair
        # missed `workable_search` the day it was added, so the line
        # undercounted and the reader was told fewer of their sources were
        # keyword searches than actually were. Same failure as `meta.boards`,
        # which drifted for exactly the same reason.
        kw_platforms = {s.platform for s in srcs if s.keyword_template}
        keyword = sum(n for k, n in cov["by_platform"].items()
                      if k in kw_platforms)
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
        # Before the queue row and before "this spends tokens". Without the
        # CLI the row was still created and instantly marked failed, so the
        # jobs table filled with attempts that were never possible and the
        # user read a cost warning for a run that could not start.
        if not runner.claude_bin():
            _say(runner._no_claude_msg())
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


# ---------------------------------------------------------------- enrich
def cmd_enrich(args) -> int:
    """Fill in descriptions for roles whose source only returned a headline."""
    from . import enrich, store
    cfg = _cfg_or_default(args.config)
    con = store.connect(args.db)
    try:
        rows = enrich.candidates(con, limit=args.limit)
        if not rows:
            _say("Nothing to fetch. Every role on the board already has its "
                 "description.")
            return 0
        serial = args.pause is not None
        workers = 1 if serial else (args.concurrency
                                    or fetch_defaults.DEFAULT_CONCURRENCY)
        how = (f"one at a time with a {args.pause}s pause" if serial
               else f"{workers} at a time, each host paced separately")
        _say(f"{len(rows)} roles to fetch, {how}. No tokens are spent.")
        if args.dry_run:
            return 0

        def progress(i, total, got):
            if i % 10 == 0 or i == total:
                _say(f"  {i}/{total}, {got} filled in")

        got, tried = enrich.run(con, cfg, rows, pause=args.pause or 0.0,
                                on_each=progress, concurrency=workers)
        _say(f"\nFilled in {got} of {tried}.")
        if got:
            _say("They can now be screened, ranked and compared to your "
                 "salary floor. `job-radar rank` picks them up.")
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------- rank
def cmd_rank(args) -> int:
    """Score every role against the CV in one batched pass.

    Deliberately a command rather than something the scan does. It spends
    tokens, and the rule everywhere else in this tool is that nothing is spent
    without being asked for. It says what it will cost before it starts.
    """
    from . import rank as rank_mod, store
    cfg = _cfg_or_default(args.config)
    con = store.connect(args.db)
    try:
        rows = rank_mod.candidates(con, refresh=args.refresh)
        if not rows:
            _say("Nothing to rank. Every role with a description already has a "
                 "fit score; use --refresh to score them again.")
            return 0
        if args.limit:
            rows = rows[: args.limit]
        batches, tokens = rank_mod.estimate(rows)
        _say(f"{len(rows)} roles to rank, in {batches} call(s), roughly "
             f"{tokens:,} input tokens.")
        _say(f"Screening these one at a time would be about "
             f"{len(rows) * 60_000:,}.")
        if args.dry_run:
            _say("dry run: nothing sent")
            return 0
        # After the dry-run branch, so estimating a spend still works on a
        # machine that could not make it, and before the real one, so the
        # missing-CLI message does not land underneath a cost estimate for a
        # run that was never going to start.
        from . import runner
        if not runner.claude_bin():
            _say(runner._no_claude_msg())
            return 1

        def progress(done, total, scored):
            _say(f"  {done}/{total} sent, {scored} scored")

        n = rank_mod.rank(con, cfg, rows, on_batch=progress)
        _say(f"\nScored {n} of {len(rows)}.")
        if n < len(rows):
            _say(f"  {len(rows) - n} came back unscored and keep fit -1; they "
                 f"are not ranked as bad, they are unranked. Run again to "
                 f"retry just those.")
        top = con.execute(
            "SELECT company,title,fit,fit_why FROM roles WHERE fit>=0 "
            "ORDER BY fit DESC, score DESC LIMIT ?", (args.top,)).fetchall()
        if top:
            _say("")
            for r in top:
                _say(f"  {r['fit']:>3}  {r['company'][:24]:<26} {r['title'][:44]}")
                if r["fit_why"]:
                    _say(f"       {r['fit_why'][:104]}")
        return 0
    finally:
        con.close()


# ------------------------------------------------------------ rescreen
def cmd_rescreen(args) -> int:
    """Re-apply the current config to roles already in the database.

    A scan filters what it fetched that day and never looks back, so every
    change to titles, locations, dealbreakers or the salary floor applies only
    to roles found afterwards. Tighten an exclude and the roles it was written
    for stay on the dashboard for ever; widen an include and nothing already
    stored is reconsidered. Measured on a real database after a day of config
    changes: 196 of 1,670 roles, 11.7%, no longer matched the config that was
    supposedly producing them.

    Reporting is the default and removal needs `--remove`, because this is the
    one command whose whole job is to delete rows somebody may have been
    relying on. A role you have touched is never removed whatever it matches:
    the status is a decision you made and outranks a filter.
    """
    from . import store
    from .models import Job, Salary
    from .screen import match, apply_salary, screen as screen_one

    cfg = _cfg_or_default(args.config)
    con = store.connect(args.db)
    try:
        # The salary columns and the real URL are selected because this has to
        # run the SAME filters a scan runs. It used to call `match` alone,
        # which is the title and location gate and nothing else, while the
        # sentence above this promised dealbreakers and the salary floor as
        # well. Add a hard dealbreaker matching every stored role, or raise
        # the floor past every stated figure, and this command answered "All
        # N roles still match your config" -- a wrong number that reads
        # exactly like a right one, on the command whose only job is to be
        # the second opinion.
        rows = con.execute(
            "SELECT r.uid, r.company, r.title, r.url, r.platform, r.location, "
            "r.description, r.salary_min, r.salary_max, r.salary_currency, "
            "r.salary_period, r.salary_confirmed, r.salary_label, "
            "COALESCE(s.status,'new') st "
            "FROM roles r LEFT JOIN role_state s ON s.uid=r.uid").fetchall()
        stale, kept_by_status = [], []
        for r in rows:
            j = Job(company=r["company"], title=r["title"],
                    url=r["url"] or "https://example.invalid/x",
                    platform=r["platform"],
                    location=r["location"] or "", description=r["description"] or "",
                    salary=Salary(min=r["salary_min"], max=r["salary_max"],
                                  currency=r["salary_currency"],
                                  period=r["salary_period"] or "year",
                                  confirmed=bool(r["salary_confirmed"]),
                                  raw=r["salary_label"]))
            ok, _ = match(j, cfg)
            if ok:
                ok, _ = apply_salary(j, cfg)
            if ok:
                # A posting with no description cannot fail a dealbreaker, and
                # `screen` keeps it for exactly that reason. Nothing is removed
                # on the strength of text nobody ever fetched.
                ok, _ = screen_one(j, cfg)
            if ok:
                continue
            (kept_by_status if r["st"] not in ("new", "") else stale).append(r)

        if not stale and not kept_by_status:
            _say(f"All {len(rows)} roles still match your config.")
            return 0
        _say(f"{len(stale) + len(kept_by_status)} of {len(rows)} roles no longer "
             f"match your config.")
        if kept_by_status:
            _say(f"  {len(kept_by_status)} of them you have already acted on, so "
                 f"they stay whatever happens.")
        for r in stale[: args.limit or 15]:
            _say(f"    {r['company'][:24]:<25} {r['title'][:52]}")
        if len(stale) > (args.limit or 15):
            _say(f"    ... and {len(stale) - (args.limit or 15)} more")

        if not args.remove:
            _say(f"\n  Nothing was removed. `job-radar rescreen --remove` deletes "
                 f"the {len(stale)} untouched ones.")
            return 0
        for r in stale:
            con.execute("DELETE FROM role_state WHERE uid=?", (r["uid"],))
            con.execute("DELETE FROM roles WHERE uid=?", (r["uid"],))
        con.commit()
        _say(f"\n  Removed {len(stale)}. Kept {len(kept_by_status)} you had acted on.")
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
        # A role with no link is seen-set history, not a listing. See
        # store.ACTIONABLE_SQL.
        where.append(store.ACTIONABLE_SQL)
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
    from .setup_wizard import NoInput
    try:
        return wizard(_cfg_write_path(args.config),
                      non_interactive=args.defaults, cv=args.cv,
                      titles=args.titles, scan=getattr(args, "scan", False))
    except NoInput:
        # stdin closed part-way through. The isatty guard in the wizard turns
        # most of these away at the door; this catches the rest, such as a pty
        # whose other end went away, so they end in a sentence rather than a
        # traceback or an unanswerable question asked forever.
        _say("\nInput ended before setup finished, so nothing was written.")
        return 1


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
    s.add_argument("--no-enrich", action="store_true",
                   help="skip fetching full postings for headline-only "
                        "sources; they stay unscreenable")
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
    v.add_argument("--force-prune", action="store_true",
                   help="prune even when most of the list came back empty, "
                        "which normally means the network is the problem")
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

    en = sub.add_parser("enrich",
                        help="fetch full postings for headline-only sources")
    en.add_argument("--limit", type=int, default=0)
    # Defaults to None, not to a number, so "the user asked for a pause" can
    # be told apart from "nobody said". A pause is now a request to go one at
    # a time: each host is paced on its own clock, so a blanket delay between
    # unrelated servers only costs time.
    en.add_argument("--pause", type=float, default=None,
                    help="seconds between requests, which also forces them to "
                         "run one at a time. Without it each host is paced "
                         "separately and different hosts run in parallel.")
    en.add_argument("--concurrency", type=int, default=None,
                    help="how many postings to fetch at once (ignored with --pause)")
    en.add_argument("--dry-run", action="store_true")
    en.add_argument("--db", default=None)
    en.set_defaults(func=cmd_enrich)

    rk = sub.add_parser("rank", help="score every role against your CV, cheaply")
    rk.add_argument("--refresh", action="store_true",
                    help="re-score roles that already have a fit")
    rk.add_argument("--limit", type=int, default=0)
    rk.add_argument("--top", type=int, default=12, help="how many to print")
    rk.add_argument("--dry-run", action="store_true",
                    help="show what it would cost and send nothing")
    rk.add_argument("--db", default=None)
    rk.set_defaults(func=cmd_rank)

    rs = sub.add_parser("rescreen",
                        help="re-apply your config to roles already stored")
    rs.add_argument("--remove", action="store_true",
                    help="delete the ones that no longer match and that you "
                         "have not acted on")
    rs.add_argument("--limit", type=int, default=0, help="how many to list")
    rs.add_argument("--db", default=None)
    rs.set_defaults(func=cmd_rescreen)

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
    w.add_argument("--scan", action="store_true",
                   help="with --defaults, run the first scan too. The "
                        "interactive wizard always does.")
    w.add_argument("--cv", default=None, help="path to your CV (required with --defaults)")
    w.add_argument("--titles", default=None,
                   help="comma-separated job titles (required with --defaults)")
    w.set_defaults(func=cmd_setup)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Before the command, so it is read rather than scrolled past at the
        # end of two hundred lines of scan output.
        if args.cmd in ("scan", "list", "serve", "rank", "coverage"):
            try:
                _daily_sync_nudge(_cfg_or_default(args.config),
                                  getattr(args, "db", None))
            except ConfigError:
                # A nudge must never stop the command, but a broken config is
                # not a failed nudge, it is the reason the command is about to
                # give a wrong answer. `list` never loads the config itself, so
                # swallowing this was the difference between "sectors:
                # [manufacturing] is not a sector that exists" and a confident
                # `0 role(s)`.
                raise
            except Exception:
                pass          # a nudge must never stop the command
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
