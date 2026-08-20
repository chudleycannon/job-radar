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
    """Where this role's documents live.

    Keyed on the role, not on the day. Keying it on today's date meant a CV
    drafted on Monday and a letter drafted on Wednesday landed in different
    folders, so the letter could not read the CV it is required to be checked
    against -- and an absent check rendered identically to a passing one.
    """
    base = Path(base or os.environ.get("JOB_RADAR_DOCS") or DEFAULT_BASE)
    name = slug(row["company"], row["title"])
    if base.exists():
        existing = sorted(p for p in base.glob(f"*-{name}") if p.is_dir())
        if existing:
            return existing[0]
    return base / f"{date.today().isoformat()}-{name}"


def docx_to_text(src: Path) -> str:
    """Pull the text out of a .docx without any dependency.

    A .docx is a zip of XML. The generation subprocess is sandboxed and cannot
    shell out to a converter, so handing it a binary means handing it nothing.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(src) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(xml)
    lines = []
    for para in root.iter(f"{ns}p"):
        text = "".join(n.text or "" for n in para.iter(f"{ns}t"))
        lines.append(text.strip())
    out, blank = [], False
    for l in lines:                       # collapse runs of empty paragraphs
        if not l:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(l)
    return "\n".join(out).strip()


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
directory. Read `job-description.md` here for the posting, and `source-cv.txt`
here for my actual record. Both are in this directory. Do not assess a gap
without reading the CV: a gap named without checking is a guess wearing the
same confidence as a finding.

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
Base it strictly on my real record in `source-cv.txt` in this directory. Read
that file first; it is the plain-text extraction of my current CV. Never
invent or inflate anything. A requirement I cannot truthfully claim is reported as a gap, not
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


def run_job(job_id: int, db_path=None, base=None, cv_source=None,
            config_path=None) -> None:
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
        cfg_file = (Path(config_path) if config_path else
                    next((Path(n) for n in ("config.local.yaml", "config.yaml")
                          if Path(n).exists()), None))
        cfg = cfg_file.read_text()[:6000] if cfg_file else "(no config found)"

        # Same reason: copy the base CV in rather than referencing it. The
        # path comes from the config, which validates it exists on load, so a
        # CV that has been moved fails loudly instead of being invented.
        cv_cfg = ""
        try:
            from .config import load as _load
            cv_cfg = _load(config_path).cv_path
        except Exception:
            pass
        # Path("") is PosixPath("."), which exists, so the "no CV configured"
        # guard below never fired and shutil.copy2 raised IsADirectoryError.
        chosen = cv_source or cv_cfg or os.environ.get("JOB_RADAR_CV") or ""
        src = Path(chosen) if chosen else None
        if src is None or not src.exists() or src.is_dir():
            store.mark_job(con, job_id, "failed",
                           error="No CV configured. Set `cv.path` in your "
                                 "config, or run `job-radar setup`.")
            return

        # Every kind needs it. Screening was previously asked to separate hard
        # requirements from things the candidate has not done yet, without
        # being given the candidate.
        shutil.copy2(src, d / f"source-cv{src.suffix}")
        if src.suffix.lower() == ".docx":
            text = docx_to_text(src)
            if not text:
                store.mark_job(con, job_id, "failed",
                               error=f"could not read any text out of {src.name}")
                return
            (d / "source-cv.txt").write_text(text, encoding="utf-8")
        elif src.suffix.lower() in (".txt", ".md"):
            shutil.copy2(src, d / "source-cv.txt")

        prompt = build_prompt(job["kind"], cfg, str(src))

        try:
            skills = Path.home() / ".claude" / "skills"
            cmd = ["claude", "-p", prompt,
                   "--permission-mode", "acceptEdits",
                   "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep",
                   "Bash(python3:*)"]
            if skills.exists():
                cmd += ["--add-dir", str(skills)]
            proc = subprocess.run(cmd, cwd=str(d), capture_output=True,
                                  text=True, timeout=TIMEOUT)
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

        expected = {"cv": "CV.md", "cover_letter": "cover-letter.md",
                    "screen": "screening.md"}[job["kind"]]
        if not (d / expected).exists():
            store.mark_job(
                con, job_id, "failed",
                error=f"finished without writing {expected}. See the log.",
                log=out)
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
            # "Too senior for you today" and "wrong forever" are different
            # things, and skipped is a terminal state. Record the verdict and
            # let the person decide.
            store.set_status(con, uid, "interested",
                             note=f"screened: {verdict}. Read screening.md "
                                  f"before skipping.")

    elif kind == "cv":
        rating = None
        rp = d / "cv-rating.txt"
        if rp.exists():
            m = re.search(r"\d{1,3}", rp.read_text())
            if m:
                rating = float(m.group(0))
        # Convert to .docx: a document you cannot attach to an application is
        # not a finished document.
        gates = _gates(d, "CV.md")
        path = _to_docx(d, "CV.md", "CV.docx")
        store.add_artifact(con, uid, "cv", path, rating=rating, gates=gates)
        cur = con.execute("SELECT status FROM role_state WHERE uid=?", (uid,)).fetchone()
        if not cur or cur["status"] == "new":
            store.set_status(con, uid, "interested")

    elif kind == "cover_letter":
        gates = _gates(d, "cover-letter.md")
        path = _to_docx(d, "cover-letter.md", "cover-letter.docx")

        # Measured here, not read back from what the model said about itself.
        cv_f, letter_f = d / "CV.md", d / "cover-letter.md"
        summary = ""
        if cv_f.exists() and letter_f.exists():
            shared = shared_ngram(cv_f.read_text(errors="ignore"),
                                  letter_f.read_text(errors="ignore"))
            gates["no_overlap_with_cv"] = not shared
            summary = f'shares "{shared}" with the CV' if shared else ""
        else:
            # An unmeasurable gate is a failed gate. Leaving it absent made
            # "never checked" look exactly like "checked and clean".
            gates["no_overlap_with_cv"] = False
            summary = "overlap not checked: no CV.md alongside the letter"
        store.add_artifact(con, uid, "cover_letter", path, summary=summary, gates=gates)


def shared_ngram(a: str, b: str, n: int = 6) -> str:
    """The longest shared sequence of n or more words, or "" if there is none.

    Computed here rather than asked for. A gate that the model reports on
    itself is not a gate: the first version asked Claude to write the answer
    into a file and then guessed at the prose it produced, which read a clean
    result as a failure.
    """
    def toks(s: str) -> list[str]:
        return re.findall(r"[a-z0-9']+", s.lower())

    ta, tb = toks(a), toks(b)
    if len(ta) < n or len(tb) < n:
        return ""
    grams_b = {tuple(tb[i:i + n]) for i in range(len(tb) - n + 1)}
    best = ""
    for i in range(len(ta) - n + 1):
        if tuple(ta[i:i + n]) in grams_b:
            # extend the match as far as it goes, to report something useful
            k = n
            while (i + k < len(ta)
                   and tuple(ta[i:i + k + 1]) in
                   {tuple(tb[j:j + k + 1]) for j in range(len(tb) - k)}):
                k += 1
            phrase = " ".join(ta[i:i + k])
            if len(phrase) > len(best):
                best = phrase
    return best


def _to_docx(d: Path, md_name: str, docx_name: str):
    """Write a .docx alongside the Markdown, and hand back whichever exists."""
    md = d / md_name
    if not md.exists():
        return ""
    try:
        from .docx import markdown_to_docx
        return markdown_to_docx(md.read_text(errors="ignore"), d / docx_name)
    except Exception:
        return md          # the Markdown is still there and still usable


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
            # Read the verdict line, not any occurrence of the word FAIL.
            # detect.py prints "Fix the FAIL/WARN lines above" as standing
            # advice even on a clean run, so a substring test marks every
            # passing document as failed.
            m = re.search(r"->\s*(PASS|FAIL)", blob)
            if m:
                gates["natural_writing"] = m.group(1) == "PASS"
            else:
                gates["natural_writing"] = not re.search(
                    r"^\s*FAIL\b", blob, re.M)
            s = re.search(r"SLOP SCORE:\s*(\d+)", blob, re.I)
            if s:
                gates["slop_score"] = int(s.group(1))
        except Exception:
            gates["natural_writing"] = None
    return gates


def regate(con) -> int:
    """Recompute the gates on documents already produced.

    Needed because a gate can be wrong: the first overlap check asked the model
    what it thought and misread the answer, marking a clean letter as
    overlapping. Fixing the check should fix the rows, not only future runs.
    """
    n = 0
    for a in con.execute("SELECT * FROM artifacts WHERE kind IN ('cv','cover_letter')"):
        path = Path(a["path"] or "")
        if not path.exists():
            continue
        d = path.parent
        gates = _gates(d, path.name)
        summary = a["summary"] or ""
        if a["kind"] == "cover_letter":
            cv_f = d / "CV.md"
            if cv_f.exists():
                shared = shared_ngram(cv_f.read_text(errors="ignore"),
                                      path.read_text(errors="ignore"))
                gates["no_overlap_with_cv"] = not shared
                summary = f'shares "{shared}" with the CV' if shared else ""
        con.execute("UPDATE artifacts SET gates=?, summary=? WHERE id=?",
                    (json.dumps(gates), summary, a["id"]))
        n += 1
    return n


def spawn(job_id: int, db_path=None, base=None, config_path=None) -> None:
    """Run a job on a daemon thread so the click returns immediately."""
    threading.Thread(target=run_job, args=(job_id,),
                     kwargs={"db_path": db_path, "base": base,
                             "config_path": config_path},
                     daemon=True).start()
