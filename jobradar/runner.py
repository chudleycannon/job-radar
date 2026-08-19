"""Generation jobs: screen a role, draft a CV, draft a cover letter.

Work is done by headless `claude -p` rather than by an API call from here,
because the quality of these documents depends on skills that already exist --
`rate-cv`, `natural-writing`, `screen-role` -- and reimplementing their rules
as a prompt string would throw all of that away.

Nothing runs unless a button was clicked. There is no schedule, no watcher and
no speculative generation: every token spent is one somebody asked for.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from datetime import date
from pathlib import Path

from . import store

DEFAULT_BASE = Path.home() / "job-applications"
TIMEOUT = 900          # 15 minutes; a pack that takes longer has gone wrong
MAX_ATTEMPTS = 2

KINDS = {
    "screen": "Screen this role against my dealbreakers",
    "cv": "Draft a tailored CV",
    "cover_letter": "Draft a cover letter",
}


def slug(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:70] or "role"


def role_dir(row, base: Path | None = None) -> Path:
    base = Path(base or os.environ.get("JOB_RADAR_DOCS") or DEFAULT_BASE)
    return base / f"{date.today().isoformat()}-{slug(row['company'], row['title'])}"


def _write_jd(d: Path, row) -> Path:
    """Save the description at generation time.

    Postings are pulled the moment they are filled, and that is usually just
    before anyone calls you for an interview. This cannot be recovered later.
    """
    p = d / "job-description.md"
    p.write_text(
        f"# {row['title']}\n\n**{row['company']}**"
        f"{' · ' + row['location'] if row['location'] else ''}\n\n"
        f"- URL: {row['url']}\n- Salary: {row['salary_label'] or 'not stated'}\n"
        f"- Posted: {row['posted_at'] or 'unknown'}\n"
        f"- Captured: {date.today().isoformat()}\n\n---\n\n"
        f"{row['description'] or '_No description available from this source._'}\n",
        encoding="utf-8")
    return p


PROMPTS = {
"screen": """Use the screen-role skill.

Screen this job for me and write your verdict to `screening.md` in the current
directory. Read `job-description.md` here for the posting.

These are my dealbreakers and filters. They are the whole list; do not invent
any others, and do not assume anything not written here:

{config}

Lead with one line: APPLY, APPLY WITH CAVEATS, or SKIP, and why. Then the
fails with the sentence that triggered each, what the posting does not say
that I would need to ask, and genuine gaps separated into hard requirements
versus stated preferences versus things I simply have not done yet.

Be brief. Three lines naming the real blocker beat a page of balance.

Finally write a single line to `verdict.txt` containing only APPLY,
APPLY_WITH_CAVEATS or SKIP.""",

"cv": """Use the rate-cv and natural-writing skills.

Draft a CV tailored to the role in `job-description.md` in this directory.
Base it strictly on my real record in `source-cv` in this directory (read it
first) -- never invent or inflate anything. A requirement I cannot truthfully claim is reported as a gap, not
written around.

Write it to `CV.md` here.

Rules that are not negotiable:
- No em-dashes anywhere.
- Plain first. State the fact and stop. No triads with a payoff, no
  "not X but Y", no stock idioms, no aphorisms.
- Keep my headline exactly as it is in the source CV.

Then run `python3 ~/.claude/skills/natural-writing/scripts/detect.py CV.md`
and fix every warning, not only failures. Repeat until it is clean.

Then score it with rate-cv against this job description and write the numeric
score out of 100 on a single line in `cv-rating.txt`, with the gap list under
it.""",

"cover_letter": """Use the natural-writing skill.

Draft a cover letter for the role in `job-description.md` in this directory.

**Read `CV.md` in this directory first.** The letter must share no phrasing
with it at all. The CV carries the facts and the metrics; the letter carries
judgement, motivation and how I work. Check this properly: no sequence of six
or more words may appear in both. If any does, rewrite the letter.

Write it to `cover-letter.md` here.

Rules that are not negotiable:
- No em-dashes anywhere.
- Say plainly why this company and why this team, using something specific
  from the job description.
- Never claim experience that is not in the CV.
- Plain first. If a sentence would work as a LinkedIn caption, flatten it.

Then run `python3 ~/.claude/skills/natural-writing/scripts/detect.py
cover-letter.md` and fix every warning. Repeat until clean.

Then write `overlap.txt` containing the longest phrase shared with the CV, or
the word NONE.""",
}


def build_prompt(kind: str, cfg_path: str, cv_source: str) -> str:
    return PROMPTS[kind].format(config=cfg_path, cv_source=cv_source)


def run_job(job_id: int, db_path=None, base=None, cv_source=None) -> None:
    """Execute one queued job. Called on a background thread."""
    con = store.connect(db_path)
    try:
        job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["state"] not in ("pending", "running"):
            return
        row = con.execute("SELECT * FROM roles WHERE uid=?", (job["uid"],)).fetchone()
        if row is None:
            store.mark_job(con, job_id, "failed", error="role not found")
            return

        store.mark_job(con, job_id, "running")
        d = role_dir(row, base)
        d.mkdir(parents=True, exist_ok=True)
        _write_jd(d, row)

        if not shutil.which("claude"):
            store.mark_job(con, job_id, "failed",
                           error="the `claude` CLI is not on PATH")
            return

        # Inline the filters rather than pointing at a path: the subprocess is
        # pinned to this folder and cannot read outside it, and a screen that
        # silently skipped the dealbreakers is worse than no screen.
        cfg_file = next((Path(n) for n in ("config.local.yaml", "config.yaml")
                         if Path(n).exists()), None)
        cfg = cfg_file.read_text()[:6000] if cfg_file else "(no config found)"

        # Same reason: copy the base CV in rather than referencing it.
        src = Path(cv_source or os.environ.get("JOB_RADAR_CV")
                   or Path.home() / "Downloads" / "Callum_McDonald_CV.docx")
        if job["kind"] == "cv":
            if not src.exists():
                store.mark_job(con, job_id, "failed",
                               error=f"base CV not found at {src}. "
                                     f"Set JOB_RADAR_CV to its path.")
                return
            shutil.copy2(src, d / f"source-cv{src.suffix}")

        prompt = build_prompt(job["kind"], cfg, str(src))

        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--permission-mode", "acceptEdits"],
                cwd=str(d), capture_output=True, text=True, timeout=TIMEOUT)
            out = (proc.stdout or "")[-4000:]
            if proc.returncode != 0:
                store.mark_job(con, job_id, "failed",
                               error=(proc.stderr or "claude exited non-zero")[:400],
                               log=out)
                return
        except subprocess.TimeoutExpired:
            store.mark_job(con, job_id, "failed",
                           error=f"timed out after {TIMEOUT}s")
            return

        _record(con, job, d, out)
        store.mark_job(con, job_id, "done", log=out)
    except Exception as e:                      # never leave a job stuck running
        store.mark_job(con, job_id, "failed", error=f"{type(e).__name__}: {e}"[:400])
    finally:
        con.close()


def _record(con, job, d: Path, log: str) -> None:
    """Turn whatever Claude produced into artifact rows."""
    uid, kind = job["uid"], job["kind"]
    store.add_artifact(con, uid, "jd_snapshot", d / "job-description.md")

    if kind == "screen":
        verdict = ""
        vp = d / "verdict.txt"
        if vp.exists():
            verdict = vp.read_text().strip().split("\n")[0][:40]
        body = (d / "screening.md")
        store.add_artifact(con, uid, "screen", body if body.exists() else "",
                           summary=verdict)
        if verdict.upper().startswith("SKIP"):
            store.set_status(con, uid, "skipped", note="screened out")

    elif kind == "cv":
        rating = None
        rp = d / "cv-rating.txt"
        if rp.exists():
            m = re.search(r"\d{1,3}", rp.read_text())
            if m:
                rating = float(m.group(0))
        path = next((d / n for n in ("CV.md", "CV.docx") if (d / n).exists()), "")
        store.add_artifact(con, uid, "cv", path, rating=rating,
                           gates=_gates(d, "CV.md"))
        cur = con.execute("SELECT status FROM role_state WHERE uid=?", (uid,)).fetchone()
        if not cur or cur["status"] == "new":
            store.set_status(con, uid, "interested")

    elif kind == "cover_letter":
        overlap = ""
        op = d / "overlap.txt"
        if op.exists():
            overlap = op.read_text().strip()[:120]
        path = next((d / n for n in ("cover-letter.md", "cover-letter.docx")
                     if (d / n).exists()), "")
        gates = _gates(d, "cover-letter.md")
        gates["no_overlap_with_cv"] = overlap.upper().startswith("NONE") or not overlap
        store.add_artifact(con, uid, "cover_letter", path, summary=overlap, gates=gates)


def _gates(d: Path, name: str) -> dict:
    """Objective checks only. A re-read is not a gate.

    The phrase-overlap defect survived three consecutive packs because the
    check was somebody reading it again. A script catches it every time.
    """
    f = d / name
    if not f.exists():
        return {"written": False}
    text = f.read_text(errors="ignore")
    gates = {"written": True, "no_em_dash": "—" not in text}
    det = Path.home() / ".claude/skills/natural-writing/scripts/detect.py"
    if det.exists():
        try:
            r = subprocess.run(["python3", str(det), str(f)],
                               capture_output=True, text=True, timeout=120)
            blob = r.stdout + r.stderr
            gates["natural_writing"] = "FAIL" not in blob
            m = re.search(r"score[^0-9]{0,12}(\d+)", blob, re.I)
            if m:
                gates["slop_score"] = int(m.group(1))
        except Exception:
            gates["natural_writing"] = None
    return gates


def spawn(job_id: int, db_path=None, base=None) -> None:
    """Run a job on a daemon thread so the click returns immediately."""
    threading.Thread(target=run_job, args=(job_id,),
                     kwargs={"db_path": db_path, "base": base},
                     daemon=True).start()
