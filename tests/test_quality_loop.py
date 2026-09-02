"""Checking the draft, and sending it back when it is not good enough.

The prompt already handed the model the linter and asked it to run it.
Nothing read the answer, so a draft could come back with the linter never run,
or run and ignored, and the tool filed it as done. Getting one usable CV out
of this took six manual rounds, and two of the faults were ones a script had
already been written to catch.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import runner, store

CLEAN_CV = """# Dana Whitfield

Engineering Manager · Northwind Data

## Profile

I manage six engineers at Northwind, twelve across two teams at peak, running
the billing platform and the pipelines behind it. I am on the incident rota.

Over the last two years I moved the team onto an automated release path. It
took eighteen months and two false starts, and the second one cost us a
weekend. I still write code, so I can tell when the bar is slipping.

## Experience

### Engineering Manager · Northwind Data · 2022 - Present

- Cut release cycle time 90 percent, from 30 minutes per change to under four.
- Removed around 9,000 duplicate alerts in one change, after tracing them to a
  single misconfigured probe that nobody had looked at in a year.
- Hired four engineers. Two of them had never worked on billing before.

### Senior Engineer · Cassio Systems · 2018 - 2022

Four years on payments infrastructure. I owned the reconciliation service and
the on-call runbook that went with it.

## Skills

Python, Go, SQL, incident command, hiring, capacity planning

## Education

BTEC Level 3 Extended National Diploma in IT
"""

DRUM_ROLL = CLEAN_CV.replace(
    "I am on the incident rota.",
    "Not a trial: the platform takes real payments every day.")


def _skills(detect_report: str) -> Path:
    """A skills tree this test controls, holding a stub linter.

    natural-writing is vendored here now, but this test still should not depend
    on the real linter's current wording. The linter has its own tests; this
    one checks whether `_quality` reads its output and feeds failures back into
    the revision loop.

    What is under test is the wiring: does `_quality` read the linter's output,
    pull out the failing lines, and hand them back in a form a revision prompt
    can use. Whether the linter itself is right is the linter's own business.
    """
    root = Path(tempfile.mkdtemp())
    nw = root / "natural-writing" / "scripts"
    nw.mkdir(parents=True)
    (nw / "detect.py").write_text(
        "import sys\nprint(%r)\n" % detect_report, encoding="utf-8")
    # rate-cv ships in this repo, so the real one is used for the CV checks.
    repo = Path(__file__).resolve().parent.parent / "skills"
    if (repo / "rate-cv").exists():
        import shutil
        shutil.copytree(repo / "rate-cv", root / "rate-cv")
    return root


CLEAN_REPORT = "  SLOP SCORE: 6/100   (bar: <= 20, and no FAILs)   ->  PASS"
DIRTY_REPORT = (
    "  [x] negation-colon           FAIL   1 denial-then-colon drum-roll(s)\n"
    "  SLOP SCORE: 31/100   (bar: <= 20, and no FAILs)   ->  NEEDS WORK")


def _dir(text: str, name: str = "CV.md") -> Path:
    d = Path(tempfile.mkdtemp())
    (d / name).write_text(text, encoding="utf-8")
    (d / "source-cv.txt").write_text(text, encoding="utf-8")
    return d


def test_a_draft_that_fails_a_check_is_reported_with_the_reason():
    """Not a score. The failing line tells the model what to change;
    "31/100" leaves it guessing."""
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(DIRTY_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(_dir(DRUM_ROLL), "CV.md", "cv")
    assert ok is False
    assert any("negation-colon" in p for p in problems), problems
    assert any("slop score 31" in p for p in problems), problems


def test_a_clean_draft_passes_without_a_revision():
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(_dir(CLEAN_CV), "CV.md", "cv")
    assert ok is True, problems
    assert scores.get("slop", 99) <= 20


def test_generated_documents_record_approved_evidence_used():
    d = Path(tempfile.mkdtemp())
    (d / "CV.md").write_text(
        "Led major incident response for customer-facing services.\n",
        encoding="utf-8")
    con = store.connect(":memory:")
    store.add_candidate_evidence(
        con, "Incident command",
        "Led major incident response for customer-facing services.",
        category="incident_management", status="approved")
    runner._write_evidence_trace(con, d, "cv")
    trace = (d / "evidence-used.json").read_text(encoding="utf-8")
    assert "Incident command" in trace
    assert "incident_management" in trace


def test_generated_documents_do_not_record_search_preferences_as_evidence():
    d = Path(tempfile.mkdtemp())
    (d / "cover-letter.md").write_text(
        "Prefer remote-first employers. Cannot relocate outside the UK.\n",
        encoding="utf-8")
    con = store.connect(":memory:")
    store.add_candidate_evidence(
        con, "Remote preference", "Prefer remote-first employers.",
        category="preference", status="approved")
    store.add_candidate_evidence(
        con, "Relocation constraint", "Cannot relocate outside the UK.",
        category="constraint", status="approved")
    runner._write_evidence_trace(con, d, "cover_letter")
    trace = (d / "evidence-used.json").read_text(encoding="utf-8")
    assert "Remote preference" not in trace
    assert "Relocation constraint" not in trace


def test_the_cv_checks_catch_what_the_prose_check_cannot():
    """natural-writing has nothing to say about a missing Education section or
    a date range an applicant tracking system cannot parse. Both were
    regressions introduced by hand on a real CV."""
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        no_edu = CLEAN_CV.replace(
            "## Education\n\nBTEC Level 3 Extended National Diploma in IT\n", "")
        ok, problems, _ = runner._quality(_dir(no_edu), "CV.md", "cv")
        assert not ok and any("education" in p.lower() for p in problems), problems

        prose_dates = (CLEAN_CV.replace("2022 - Present", "2022 to present")
                       .replace("2018 - 2022", "2018 to 2022"))
        ok, problems, _ = runner._quality(_dir(prose_dates), "CV.md", "cv")
        assert not ok and any("date range" in p for p in problems), problems


def test_a_cv_over_the_hard_word_limit_is_sent_back():
    """The prompt can ask for two pages, but the checker has to enforce it
    when a model summarizes the whole master CV anyway."""
    long = CLEAN_CV + ("\n\nExtra relevant but repetitive evidence. " * 170)
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(_dir(long), "CV.md", "cv")
    assert ok is False
    assert scores["words"] > 800
    assert any("needs 550 to 750" in p for p in problems), problems


def test_a_cv_that_keeps_master_cv_sections_is_sent_back():
    draft = CLEAN_CV + (
        "\n\n## Core expertise and keywords\n\n"
        "- Engineering leadership, data platforms and operational resilience.\n"
        "\n\n## Additional notes for matching systems\n\n"
        "- Use this CV as a broad factual base.\n")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, _ = runner._quality(_dir(draft), "CV.md", "cv")
    assert ok is False
    assert any("master-CV section" in p for p in problems), problems


def test_local_recovery_rebuilds_a_master_cv_shaped_draft():
    source = """# Master CV for automated job matching
Email: dana@example.test | LinkedIn: linkedin.com/in/dana

## Selected achievements and evidence
- Broad source-only summary that must not be copied.

## Professional experience

## Engineering Manager - Northwind Data
- January 2022 - Present
- Acted as incident controller for billing outages, coordinating responders, service restoration, stakeholder updates and post-incident reviews.
- Hired four engineers and managed twelve across two teams during peak delivery.
- Supported audited change controls, risk reviews and operational governance for a customer-facing payment platform.

## Senior Engineer - Cassio Systems
- June 2018 - January 2022
- Owned the reconciliation service and the on-call runbook that supported it.
- Reduced duplicate alerts by 9,000 after tracing a misconfigured probe.

## Education
Degree in Software Engineering
"""
    d = _dir(source)
    (d / "job-description.md").write_text(
        "Major Incident Manager. Own critical incidents, stakeholder "
        "communication, root cause review, governance and service restoration.",
        encoding="utf-8")
    (d / "cv-rating.txt").write_text("56/100\n", encoding="utf-8")

    assert runner._salvage_cv(d) is True
    text = (d / "CV.md").read_text(encoding="utf-8")
    assert text.startswith("# Ryan Begen\n"), text[:80]
    assert "Selected achievements and evidence" not in text
    assert "Additional notes for matching systems" not in text
    assert "Strongest evidence is" not in text
    assert "practical base" not in text
    assert "without inventing" not in text.lower()
    assert "not present in the source CV" not in text
    assert "incident controller" in text
    assert "2018 - 2022" in text
    assert "Local recovery rebuilt this CV" in (
        d / "cv-rating.txt").read_text(encoding="utf-8")


def test_a_cv_profile_that_leaks_prompt_instructions_is_sent_back():
    draft = CLEAN_CV.replace(
        "I am on the incident rota.",
        "I am on the incident rota without inventing ServiceNow experience not "
        "present in the source CV.")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, _ = runner._quality(_dir(draft), "CV.md", "cv")
    assert ok is False
    assert any("without inventing" in p for p in problems), problems
    assert any("candidate-facing CV" in p for p in problems), problems


def test_a_cv_profile_that_reads_like_screening_notes_is_sent_back():
    draft = CLEAN_CV.replace(
        "I am on the incident rota.",
        "Strongest evidence is in incident response for critical monitoring, "
        "metrics and data-platform services. Brings a practical base for "
        "major incident ownership and stakeholder reporting.")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, _ = runner._quality(_dir(draft), "CV.md", "cv")
    assert ok is False
    assert any("screening feedback" in p for p in problems), problems
    assert any("Strongest evidence is" in p for p in problems), problems


def test_skills_bullets_do_not_count_as_too_many_achievement_bullets():
    draft = CLEAN_CV.replace(
        "Python, Go, SQL, incident command, hiring, capacity planning",
        "\n".join(f"- Skill cluster {i}" for i in range(24)))
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, _ = runner._quality(_dir(draft), "CV.md", "cv")
    assert not any(p.startswith("selection:") for p in problems), problems


def test_a_cv_that_moves_metrics_between_employers_is_sent_back():
    source = """# Ryan Begen

## Professional experience

## Engineering Manager - Matillion
- January 2022 - December 2023
- Managed customer-impacting incidents and escalations, supporting investigation, resolution, stakeholder communication and operational improvements.

## Senior Software Engineer and Squad Lead - Skyscanner
- June 2014 - June 2020
- Improved OpenTSDB service availability from approximately 80% to 99.999% over three months and increased deployment frequency from 2-3 releases per month to 2-3 deployments per day.
- Improved internal customer satisfaction from 1.7 to 4.3 by prioritising reliability, usability, platform stability and developer experience.
"""
    draft = CLEAN_CV + """

### Engineering Manager - Matillion | 2022 - 2023
- Increased deployment frequency from 2-3 releases per month to 2-3 deployments per day through CI/CD, testing and workflow improvements.
- Improved internal customer satisfaction from 1.7 to 4.3 through reliability, usability and developer experience improvements.
"""
    d = _dir(draft)
    (d / "source-cv.txt").write_text(source, encoding="utf-8")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, _ = runner._quality(d, "CV.md", "cv")
    assert ok is False
    assert sum(p.startswith("attribution:") for p in problems) == 2, problems
    assert any("Matillion" in p and "Skyscanner" in p for p in problems), problems


def test_a_low_cv_rating_is_sent_back_for_better_tailoring():
    d = _dir(CLEAN_CV)
    (d / "cv-rating.txt").write_text(
        "56/100 · currency 4/8 · weak fit\n", encoding="utf-8")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(d, "CV.md", "cv")
    assert ok is False
    assert scores["cv_rating"] == 56
    assert any("needs 70 or more" in p for p in problems), problems


def test_a_good_cv_rating_overrides_coarse_all_bullet_quantification():
    d = _dir(CLEAN_CV)
    (d / "cv-rating.txt").write_text(
        "80/100 · currency 5/8 · qualified\n", encoding="utf-8")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(
            "  SLOP SCORE: 6/100   (bar: <= 20, and no FAILs)   ->  PASS\n"
            "  quantified: 7/30 = 23%  (target >=60%)")), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(d, "CV.md", "cv")
    assert scores["cv_rating"] == 80
    assert not any("only 23% of bullets" in p for p in problems), problems


def test_a_cover_letter_over_the_word_limit_is_sent_back():
    body = ("Dear Hiring Manager,\n\n"
            + ("I am applying because the role matches my experience. " * 70)
            + "\n\nYours sincerely,\nRyan Begen\n")
    with mock.patch.object(runner, "_BUNDLED_SKILLS", _skills(CLEAN_REPORT)), \
            mock.patch.object(runner, "_skill_roots",
                              lambda: [runner._BUNDLED_SKILLS]):
        ok, problems, scores = runner._quality(
            _dir(body, "cover-letter.md"), "cover-letter.md", "cover_letter")
    assert ok is False
    assert scores["words"] > 500
    assert any("needs 350 to 500" in p for p in problems), problems


def test_a_hard_quality_failure_is_not_published_as_a_cv_artifact():
    d = Path(tempfile.mkdtemp())
    db, docs = d / "j.db", d / "docs"
    cv = d / "source.md"
    cv.write_text("Ryan Begen. Acted as Incident Controller.\n", encoding="utf-8")
    cfg = d / "config.yaml"
    cfg.write_text(
        "titles:\n  include: [Major Incident Manager]\n"
        "locations:\n  countries: [UK]\n"
        "cv:\n  path: " + repr(str(cv)) + "\n"
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-5\n"
        "  anthropic_api_key: sk-ant-api03-test\n",
        encoding="utf-8")
    con = store.connect(db)
    try:
        con.execute(
            "INSERT INTO roles (uid,company,title,url,description,first_seen,last_seen) "
            "VALUES ('u','Standard Life','Major Incident Manager','https://x',?,"
            "date('now'),date('now'))",
            ("Major incident management and service restoration. " * 20,))
        job = store.enqueue(con, "u", "cv")
    finally:
        con.close()

    def write_draft(*_a, **_k):
        folder = docs / (
            f"{date.today().isoformat()}-standard-life-major-incident-manager-u")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "CV.md").write_text(CLEAN_CV, encoding="utf-8")
        (folder / "cv-rating.txt").write_text("68/100\n", encoding="utf-8")
        return "drafted"

    with mock.patch("jobradar.runner._run_api_prompt", write_draft), \
            mock.patch("jobradar.runner._run_api_revision", write_draft), \
            mock.patch("jobradar.runner._quality",
                       lambda *a, **k: (
                           False,
                           ["length: CV.md is 1890 words, needs 550 to 750"],
                           {"words": 1890})):
        runner.run_job(job, db_path=db, base=docs, config_path=cfg)

    con = store.connect(db)
    try:
        row = con.execute("SELECT state,error FROM jobs WHERE id=?", (job,)).fetchone()
        art = con.execute("SELECT 1 FROM artifacts WHERE uid='u' AND kind='cv'").fetchone()
    finally:
        con.close()
    assert row["state"] == "failed", dict(row)
    assert "hard quality checks" in row["error"], row["error"]
    assert art is None


def test_a_master_cv_copy_is_recovered_before_the_job_fails():
    d = Path(tempfile.mkdtemp())
    db, docs = d / "j.db", d / "docs"
    cv = d / "source.md"
    cv.write_text("Ryan Begen. Acted as Incident Controller.\n", encoding="utf-8")
    cfg = d / "config.yaml"
    cfg.write_text(
        "titles:\n  include: [Major Incident Manager]\n"
        "locations:\n  countries: [UK]\n"
        "cv:\n  path: " + repr(str(cv)) + "\n"
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-5\n"
        "  anthropic_api_key: sk-ant-api03-test\n",
        encoding="utf-8")
    con = store.connect(db)
    try:
        con.execute(
            "INSERT INTO roles (uid,company,title,url,description,first_seen,last_seen) "
            "VALUES ('u','Standard Life','Major Incident Manager','https://x',?,"
            "date('now'),date('now'))",
            ("Major incident management and service restoration. " * 20,))
        job = store.enqueue(con, "u", "cv")
    finally:
        con.close()

    folder = docs / (
        f"{date.today().isoformat()}-standard-life-major-incident-manager-u")

    def write_draft(*_a, **_k):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "CV.md").write_text(CLEAN_CV, encoding="utf-8")
        (folder / "cv-rating.txt").write_text("76/100\n", encoding="utf-8")
        return "drafted"

    def recover(path):
        (path / "CV.md").write_text(CLEAN_CV, encoding="utf-8")
        return True

    checks = iter([
        (False, ["length: CV.md is 1966 words, needs 550 to 750"],
         {"words": 1966}),
        (False, ["length: CV.md is 1966 words, needs 550 to 750"],
         {"words": 1966}),
        (False, ["natural-writing: slop score 24, needs 20 or under"],
         {"words": 620, "slop": 24}),
    ])

    with mock.patch("jobradar.runner._run_api_prompt", write_draft), \
            mock.patch("jobradar.runner._run_api_revision", write_draft), \
            mock.patch("jobradar.runner._salvage_cv", recover), \
            mock.patch("jobradar.runner._quality", lambda *a, **k: next(checks)):
        runner.run_job(job, db_path=db, base=docs, config_path=cfg)

    con = store.connect(db)
    try:
        row = con.execute("SELECT state,error,log FROM jobs WHERE id=?",
                          (job,)).fetchone()
        art = con.execute("SELECT 1 FROM artifacts WHERE uid='u' AND kind='cv'").fetchone()
    finally:
        con.close()
    assert row["state"] == "done", dict(row)
    assert not row["error"]
    assert "local recovery" in row["log"]
    assert art is not None


def test_an_uncheckable_document_is_a_failure_not_a_pass():
    """The rule this file already applies everywhere else. A document nothing
    checked must not look like one that was checked and cleared."""
    with mock.patch.object(runner, "_script", lambda *a: None):
        ok, problems, _ = runner._quality(_dir(CLEAN_CV), "CV.md", "cv")
    assert ok is False
    assert any("never checked" in p or "never scored" in p for p in problems), problems


def test_the_revision_prompt_says_what_not_to_do():
    """The first version of this loop watched a model shorten a CV until it
    passed by having almost nothing left in it, which is a better score and a
    worse document."""
    p = runner._revision_prompt("CV.md", ["natural-writing: colon-reveal FAIL"])
    assert "colon-reveal" in p
    assert "Do not remove content" in p
    assert "source-cv.txt" in p, "must not invent facts to fix a score"


def test_the_revision_prompt_can_delete_master_cv_sections():
    p = runner._revision_prompt(
        "CV.md",
        ["selection: remove the master-CV section 'core expertise and keywords'"])
    lower = p.lower()
    assert "delete any master-cv-only section" in lower
    assert "matching-system" in lower
    assert "keep the same structure and the same headings" not in lower


def test_the_revision_prompt_fixes_bad_attribution_and_low_rating():
    p = runner._revision_prompt(
        "CV.md",
        ["attribution: achievement under Matillion appears to come from Skyscanner",
         "cv-rating: score 56/100, needs 70 or more before publishing"])
    lower = p.lower()
    assert "move the achievement back under the employer" in lower
    assert "low cv rating" in lower
    assert "opening half-page" in lower


def test_a_shorter_overlong_cv_counts_as_revision_progress():
    before = [
        "length: CV.md is 1890 words, needs 550 to 750",
        "selection: remove the master-CV section 'core expertise and keywords'",
    ]
    after = [
        "length: CV.md is 1463 words, needs 550 to 750",
        "selection: remove the master-CV section 'core expertise and keywords'",
    ]
    assert runner._quality_improved(
        before, {"words": 1890}, after, {"words": 1463})


def test_the_loop_is_capped():
    """Every revision is another call. A loop with no ceiling can spend real
    money taking a slop score from 6 to 4."""
    assert runner.MAX_REVISIONS == 2


def test_cv_signals_leaves_no_working_file_behind():
    """It writes an extracted-text file next to the document, which would
    otherwise end up in the folder the user opens."""
    d = _dir(CLEAN_CV)
    runner._quality(d, "CV.md", "cv")
    assert not (d / "CV.md.extracted.txt").exists()
