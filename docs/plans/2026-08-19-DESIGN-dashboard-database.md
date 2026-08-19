# Design: a database-backed dashboard you can act from

19 August 2026

## The problem

The dashboard is a report. It tells you about 306 roles and then forgets
everything you do next. Acting on a role means leaving it: open a terminal,
run a command, hand-edit YAML, generate documents in a separate Claude
session, then lose track of which of those documents belongs to which role.

The goal is a dashboard you work *from*: screen a role, draft a CV, draft a
letter, apply, skip. State sticks. Documents are attached to the role that
produced them.

---

## 1. Architecture: queue-and-execute, not an LLM in the web app

**Decision.** The dashboard is a thin local layer over a database. Clicking a
generate button spawns headless `claude -p` as a background process; Claude
does the work using the existing skills and writes results back.

**Alternatives considered.**

- *Web app calls the Anthropic API directly.* Self-contained, no Claude Code
  dependency. Rejected: it would reimplement the writing rules as a prompt
  string and discard `natural-writing`, `rate-cv` and the plain-first
  discipline. The DeepL pack took six correction rounds to get right; that
  craft lives in skills, not in code.
- *Dashboard queues, user triggers manually in Claude Code.* Safer, but two
  steps and an easy one to forget.

**Trade-off.** Spawning a subprocess from a local server is more moving parts
than a static file, and it assumes the `claude` CLI is installed and
authenticated. In exchange, generation quality is whatever the skills give
you rather than whatever a hand-written prompt gives you.

**Constraint carried through.** Nothing generates unless a button is clicked.
No speculative or batch generation. Tokens are only spent on request.

---

## 2. Two front ends over one database

**Decision.** `job-radar scan` keeps writing a static, read-only HTML
snapshot. `job-radar serve` renders the same data with the buttons live, from
a small local server using only `http.server` and `sqlite3` from the standard
library.

**Alternatives considered.**

- *Static HTML plus browser localStorage.* No server. Rejected: the CLI and
  the dashboard would disagree until an export step ran, and clearing site
  data would lose the state.
- *Replace the static file entirely with the served app.* Simpler to build,
  but breaks the GitHub Actions path, the downloadable artifact, and every
  public user who never runs a server.

**Trade-off.** Two renderers to keep in step. Mitigated by both reading the
same tables and sharing the row template.

---

## 3. Status and documents are separate axes

**Decision.** One `status` column for pipeline position:
`new → interested → applied → interviewing → offer`, with `rejected`,
`withdrawn` and `skipped` as terminals. A separate `artifacts` table records
what has been produced.

"CV created, not submitted" is therefore not a status. It is
`status: interested` with a CV artifact attached, and the dashboard shows both
facts.

**Alternatives considered.**

- *One flat status list including document states* (`cv_drafted`,
  `pack_ready`). Simple to query, but it holds one truth at a time: moving to
  `applied` erases the fact that a CV exists, and every combination needs its
  own value.
- *Free-form tags.* Flexible, unstructured, and unable to answer "which roles
  have a CV but no application".

**Trade-off.** Two things to display per row and slightly more schema, in
exchange for a model that answers questions instead of a field that keeps
growing values.

---

## 4. Documents live on disk, outside the repo

**Decision.** One folder per role under a configurable base, defaulting to
`~/job-applications/`, outside the repository entirely:

```
~/job-applications/2026-08-19-chainguard-engineering-manager/
    CV.docx
    cover-letter.docx
    job-description.md
    meta.json
```

The database stores paths and metadata, never file content — including the
`rate-cv` score, so a row can read "CV 78/100" and be sorted by it.

**The JD snapshot is deliberate.** Postings are pulled the moment they are
filled. When an employer comes back three weeks later for an interview, the
description applied against is often gone — precisely when it is needed for
prep. Saving it at generation time costs nothing and cannot be recovered
afterwards.

**Alternatives considered.**

- *Documents as blobs in the database.* One file to back up. Rejected: a
  16.6GB SQLite was deleted this week for exactly this kind of bloat, and a
  .docx that exists only inside a table cannot be opened from Finder.
- *Inside the repo at `job-radar/applications/`.* Everything in one place,
  but the repo is public and two near-misses have already put personal data
  into it.

**Trade-off.** Moving or renaming a folder leaves a stale path. `serve`
checks paths on render and shows a link as broken rather than silently dead.

---

## 5. The actions on a row

**Decision.** Five buttons: **Screen · CV · Cover letter · Apply · Skip.**

**Screen comes first and matters most.** One click runs `/screen-role`
against the job description: dealbreakers, seniority reality, the hands-on
tell, genuine gaps. Seconds and pennies, against minutes and real cost for a
document. Six roles this month looked strong in a list and failed on reading —
a coding round at one, hands-on delivery at another. Screening before
generating means never spending a pack on a role that would have been
rejected after reading it.

**CV and cover letter are separate buttons**, not one "create pack". Many
applications need no cover letter, and generating one unasked wastes tokens.

**Cover letter is blocked until a CV exists.** Disabled with the reason
visible, not hidden. This is not only ordering: it guarantees the overlap gate
always has something to compare against. The CV carries the facts, the letter
carries judgement and motivation, and they should share nothing but the name
at the top.

**Deliberately omitted:** a button for application form answers. Those have
been produced ad hoc twice and that worked. Noted here so its absence is a
decision rather than an oversight.

---

## 6. Generation runs gates, then reviews

**Decision.** A generation job drafts, then runs mechanical gates, redrafts on
failure up to a limit, then takes one fresh-context adversarial read before
marking the artifact complete. The row shows which gates passed.

The gates are objective and scriptable:

- `natural-writing/scripts/detect.py`, no FAILs
- 6-gram overlap against every other document for that role, target zero
- em-dash count, zero
- `rate-cv` score against its rubric

**Why gates rather than a re-read.** The phrase-overlap defect survived three
consecutive application packs precisely because the check was a re-read. A
script catches it every time. Self-review is the weakest part of this design
and it is the last pass, not the only one.

**Trade-off.** Redraft loops cost tokens. Capped, and the cap is visible in
the job record so a role that burned three attempts is obvious.

---

## 7. One store, with one derived export

**Decision.** The database replaces `state/seen.json`, `applications.local.yaml`
and `out/roles.json` as sources of truth. A migration on first run imports the
existing seen-set and application history.

**Exception, and it is an export not a second source.** GitHub Actions runs on
a fresh machine and needs the seen-set to persist between runs. `scan` will
write `state/seen.json` as a one-directional export out of the database, in
the same way `roles.json` is written today. Nothing reads it back except a
fresh clone with no database.

The database itself is gitignored. It holds application history, notes and
document paths, and the repository is public.

**Alternatives considered.**

- *Database for state and documents only, files keep the rest.* Less migration
  risk. Rejected: "which roles have I seen" and "what did I do about them"
  would live in different places and could disagree. Two defects this week came
  from the same fact living in two places.

---

## Schema sketch

```sql
roles        -- one row per posting ever seen
  uid TEXT PRIMARY KEY, company, title, url, location, city, country,
  work_mode, sector, platform, salary_min, salary_max, salary_currency,
  salary_confirmed, salary_label, posted_at, description,
  score REAL, first_seen, last_seen

role_state   -- what you have decided
  uid TEXT PRIMARY KEY REFERENCES roles(uid),
  status TEXT, note TEXT, updated_at

artifacts    -- what has been produced
  id INTEGER PRIMARY KEY, uid REFERENCES roles(uid),
  kind TEXT,           -- cv | cover_letter | screen | jd_snapshot
  path TEXT, rating REAL, gates_json TEXT, created_at

jobs         -- generation runs
  id INTEGER PRIMARY KEY, uid REFERENCES roles(uid),
  kind TEXT, state TEXT,   -- pending | running | done | failed
  requested_at, finished_at, attempts INTEGER, error TEXT
```

---

## Risks

**The `claude` CLI is a hard dependency** for every generate button. It must
be installed and authenticated, and headless skill access needs verifying as
the first build step rather than assumed — if `claude -p` cannot reach
`~/.claude/skills/`, section 1 collapses and the fallback is manual triggering.

**Public users get less.** Anyone who never runs `serve` sees today's static
dashboard. That is acceptable — it is still the whole scanner — but the README
must not imply otherwise.

**A background subprocess that writes files** is the most dangerous thing here.
Generation must write only inside the configured base directory, and a failed
job must leave the row marked failed with the error rather than silently
producing nothing.

---

## Not doing

- Multi-user, auth, or hosting. This is a local tool.
- Editing documents in the browser.
- Automatic generation on a schedule. Every token spent is a click.
- Form-answer generation, for now.
