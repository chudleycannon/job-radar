"""Rank the whole board against your CV in one pass.

The screen is thorough and it costs. Reading two hundred roles that way is
twelve million tokens and several hours, so nobody does it, so the board stays
in the order the filters produced: title matched, in your country, posted this
week. That ordering says a role is *eligible*. It says nothing about whether
it is a good use of your afternoon.

This is the cheap layer underneath. One call reads a compact digest of every
role that has a description and returns a fit score and one line of reasoning
for each. Truncated to the opening of each posting, where the requirements
live, two hundred roles is about sixty thousand tokens: roughly one percent of
screening them individually. The screen then goes where it earns its money, on
the four or five at the top, rather than on whichever role you happened to
scroll to first.

Two rules it inherits from the screen, because a cheap answer that flatters is
worse than no answer:

  * It scores fit against the real CV, never against the title. A role that
    wants infrastructure ownership from someone who has never owned it is a
    poor fit however well the words line up.
  * It never invents. The reason has to point at something in the posting or
    the CV, and "the posting does not say" is a legitimate answer.

Nothing here runs on a schedule. It is a command, and it prints what it is
about to spend before it spends it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import store

MODEL = "claude-sonnet-5"

# Roles per call. Small enough that one bad response costs little and the
# model keeps attention on every row; large enough that the CV, which is the
# expensive constant, is sent a handful of times rather than two hundred.
#
# Twenty rather than forty because progress is only observable between calls.
# At forty the counter sat still for two minutes at a time and a live run was
# indistinguishable from a hung one. The cost of halving it is one more copy
# of the CV per extra call, about 1,500 tokens, which is worth paying to be
# able to see that something is happening.
BATCH = 20

# Characters of each posting to send. The opening carries the title, the team,
# the seniority and the must-haves; the rest is benefits, values and the
# equal-opportunities paragraph, which cost tokens and decide nothing.
JD_CHARS = 1200

PROMPT = """You are triaging a job board for one person, so they know which
roles are worth reading properly. You are not writing an application and you
are not screening in detail: something slower and more expensive does that,
and it will be pointed at whichever roles you rank highest.

THEIR CV:
{cv}

WHAT THEY ARE LOOKING FOR:
{wants}

Score each role below from 0 to 100 for how good a fit it is FOR THIS PERSON,
and give one sentence saying why. Judge against the record in the CV, not
against the job title: a role wanting five years of something they have never
done is a poor fit however well the title matches.

Scoring:
  85-100  they could do this today and their record shows it
  70-84   strong, with one gap that is a stretch rather than a barrier
  50-69   plausible, but a real gap or a level jump
  25-49   they would be an unusual hire for it
  0-24    wrong role, wrong level, or wrong field

Be honest downward. A list where everything scores 80 is useless, and a person
who applies to five roles you flattered has lost a week. Most roles on a board
like this are a 40 to 60, and saying so is the entire value here.

The reason must point at something concrete in the posting or the CV. If the
posting is too thin to judge, score it 50 and say the posting does not say.

ROLES:
{roles}

Return ONLY a JSON array, no prose and no code fence, one object per role,
using the exact id given:
[{{"id": "<id>", "fit": <0-100>, "why": "<one sentence>"}}]"""


def _digest(row, chars: int = JD_CHARS) -> str:
    d = " ".join((row["description"] or "").split())[:chars]
    loc = row["location"] or "not stated"
    pay = row["salary_label"] or "unconfirmed salary"
    return (f'--- id: {row["uid"][:8]}\n'
            f'{row["company"]} | {row["title"]} | {loc} | {pay}\n{d}\n')


def _wants(cfg) -> str:
    bits = [f"Titles: {', '.join(cfg.titles_include)}"]
    if cfg.countries:
        bits.append(f"Countries: {', '.join(cfg.countries)}")
    if cfg.relocate_to:
        bits.append(f"Would relocate to: {', '.join(cfg.relocate_to)}")
    if cfg.salary_floor:
        bits.append(f"Salary floor: {cfg.salary_floor:,.0f} {cfg.salary_currency} "
                    f"(a walk-away number, not a target)")
    if cfg.dealbreakers:
        bits.append("Would turn down a role for: "
                    + ", ".join(d.name for d in cfg.dealbreakers))
    return "\n".join(bits)


def _cv_text(cfg) -> str:
    from .runner import docx_to_text
    p = Path(cfg.cv_path).expanduser()
    if not p.exists():
        raise SystemExit(f"No CV at {p}. Set cv.path in your config.")
    text = docx_to_text(p) if p.suffix.lower() == ".docx" else p.read_text(encoding="utf-8", errors="ignore")
    # `docx_to_text` returns "" on anything it cannot open, including a
    # permission error, so a file that exists is not proof of a CV that can be
    # read. Ranking against an empty CV would still produce a full set of
    # confident-looking scores, judged against nothing. Fail instead.
    if len(text.strip()) < 200:
        raise SystemExit(
            f"Could not read a CV out of {p} (got {len(text.strip())} "
            f"characters). Scoring against an empty CV would give you numbers "
            f"with nothing behind them. Check the file opens, and that this "
            f"process can read it.")
    # The CV is the constant in every batch, so its length is multiplied by the
    # number of calls. Six thousand characters is a full two-page CV.
    return " ".join(text.split())[:6000]


def _call(prompt: str, timeout: int = 600) -> list[dict]:
    from .runner import claude_bin, _no_claude_msg, looks_like_limit, LimitReached
    exe = claude_bin()
    if not exe:
        raise SystemExit(_no_claude_msg())
    # stdin closed: there is no terminal behind the dashboard or the scheduled
    # jobs, so anything the CLI tried to read would block until the timeout.
    r = subprocess.run([exe, "-p", prompt, "--model", MODEL, "--allowedTools", ""],
                       capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, timeout=timeout)
    if r.returncode:
        # An exhausted limit is not a bad response. Returning [] for both meant
        # the loop carried on and fired every remaining batch, each failing the
        # same way, and reported "0 scored" with no reason attached.
        hit = looks_like_limit(r.stderr, r.stdout)
        if hit:
            raise LimitReached(hit)
        return []
    text = r.stdout.strip()
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < a:
        return []
    try:
        rows = json.loads(text[a:b + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in rows if isinstance(d, dict) and d.get("id")]


def candidates(con, refresh: bool = False) -> list:
    """Roles worth spending a triage token on.

    Only ones with a description: a LinkedIn listing carries a title, a company
    and nothing else, and scoring those would be scoring the title, which the
    filters already did for free.
    """
    store._ensure_columns(con)
    q = ("SELECT r.* FROM roles r LEFT JOIN role_state s ON s.uid=r.uid "
         "WHERE COALESCE(s.status,'new') NOT IN "
         "('rejected','withdrawn','skipped','closed') "
         f"AND {store.LIVE_SQL} AND LENGTH(TRIM(COALESCE(r.description,'')))>=200")
    if not refresh:
        q += " AND COALESCE(r.fit,-1) < 0"
    return con.execute(q + " ORDER BY r.score DESC").fetchall()


def rank(con, cfg, rows, on_batch=None, should_stop=None) -> int:
    """Score `rows` and write the results back. Returns how many were scored.

    `should_stop` is checked between batches, which is the only place it can
    be: a batch is one request and there is no way to interrupt it partway.
    Everything already scored is kept, so stopping costs the batch in flight
    and nothing else.
    """
    from .runner import LimitReached
    cv, wants = _cv_text(cfg), _wants(cfg)
    done = 0
    for i in range(0, len(rows), BATCH):
        if should_stop and should_stop():
            break
        chunk = rows[i:i + BATCH]
        by_id = {r["uid"][:8]: r["uid"] for r in chunk}
        try:
            out = _call(PROMPT.format(cv=cv, wants=wants,
                                      roles="\n".join(_digest(r) for r in chunk)))
        except LimitReached:
            # Stop where we are. Everything scored so far is already written,
            # and the roles not reached keep fit -1, so re-running later picks
            # up exactly where this left off rather than starting again.
            raise
        for d in out:
            uid = by_id.get(str(d.get("id", ""))[:8])
            if not uid:
                continue
            try:
                fit = max(0, min(100, int(float(d.get("fit", 0)))))
            except (TypeError, ValueError):
                continue
            con.execute("UPDATE roles SET fit=?, fit_why=? WHERE uid=?",
                        (fit, str(d.get("why", ""))[:400], uid))
            done += 1
        if on_batch:
            on_batch(min(i + BATCH, len(rows)), len(rows), done)
    return done


def unrankable(con) -> int:
    """Roles on the board that can never get a fit score.

    A LinkedIn listing carries a title, a company and a location. There is
    nothing to judge fit against, so ranking skips it. Reporting only
    "0 unranked" turned that into "everything is ranked", which was not true
    of what the person was looking at: a quarter of the board had no score and
    no explanation for why.
    """
    store._ensure_columns(con)
    return con.execute(
        "SELECT COUNT(*) c FROM roles r LEFT JOIN role_state s ON s.uid=r.uid "
        "WHERE COALESCE(s.status,'new') NOT IN "
        "('rejected','withdrawn','skipped','closed') "
        f"AND {store.LIVE_SQL} AND COALESCE(r.fit,-1) < 0 "
        "AND LENGTH(TRIM(COALESCE(r.description,''))) < 200").fetchone()["c"]


def estimate(rows) -> tuple[int, int]:
    """(batches, approximate input tokens). Honest enough to decide on."""
    chars = sum(min(len(r["description"] or ""), JD_CHARS) + 120 for r in rows)
    batches = max(1, (len(rows) + BATCH - 1) // BATCH)
    return batches, (chars + batches * 7000) // 4
