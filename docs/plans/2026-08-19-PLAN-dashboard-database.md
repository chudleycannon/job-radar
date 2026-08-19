# Plan: database-backed dashboard

Implements `2026-08-19-DESIGN-dashboard-database.md`.

Verified before starting: `claude -p` runs headless (v2.1.224) and can see
`rate-cv`, `natural-writing` and `screen-role`. Section 1 of the design holds.

## Order, and why

Each step leaves the tool working. Nothing is half-migrated at any point.

### 1. `jobradar/store.py` — the database
Schema per the design: `roles`, `role_state`, `artifacts`, `jobs`. Open/create
at `data/job-radar.db`, gitignored. Migration that imports `state/seen.json`
and `applications.local.yaml` when they exist and the DB is empty.
**Done when:** migration is idempotent and a test proves re-running changes nothing.

### 2. Wire `scan` to the database
`scan` upserts roles, preserves `first_seen`, and computes new-since-last-run
from the DB rather than the JSON. Still writes `out/index.html` and
`out/roles.json`, plus `state/seen.json` as a one-way export for Actions.
**Done when:** a scan run produces identical matching counts to today.

### 3. `jobradar/serve.py` — the local server
`http.server`, no dependencies. `GET /` renders the dashboard from the DB.
`POST /api/action` for status changes. `POST /api/generate` enqueues a job.
`GET /api/jobs` for polling. Binds 127.0.0.1 only.
**Done when:** clicking skip persists across a restart.

### 4. `jobradar/runner.py` — background generation
Spawns `claude -p` with a task-specific prompt, cwd pinned to the role's
folder, writes artifacts back. One job at a time. Records attempts and errors.
**Done when:** a screen job completes and its output lands in `artifacts`.

### 5. `jobradar/gates.py` — the checks
`detect.py` slop score, 6-gram overlap against sibling documents, em-dash
count, `rate-cv` score. Returns per-gate pass/fail for the row to display.
**Done when:** a document with a repeated 8-word phrase fails the overlap gate.

### 6. Dashboard buttons
Screen · CV · Cover letter · Apply · Skip. Cover letter disabled with a reason
until a CV artifact exists. Rows show status, gate results and document links.
Static `scan` output keeps the same layout with buttons omitted.
**Done when:** rendered, screenshotted at mobile and desktop, and looked at.

### 7. Tests and docs
Schema migration, gate behaviour, cover-letter blocking, action persistence.
README section. `applications.local.yaml` stays supported as an import path.

## Risks being managed

- **Generation writes files.** Runner refuses any path outside the configured
  base directory.
- **Runaway jobs.** One concurrent job, attempt cap, timeout, failures recorded
  not retried forever.
- **Migration is one-way.** Back up the existing files before first run.
