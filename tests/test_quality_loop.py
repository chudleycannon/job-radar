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
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import runner

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

    natural-writing is not in this repo and is not on a CI runner, so a test
    that calls the real one is testing whether the developer happens to have
    it installed. That exact mistake was fixed once today in test_core.py and
    made again here, in the tests written to check that documents get checked.

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


def test_the_loop_is_capped():
    """Every revision is another call. A loop with no ceiling can spend real
    money taking a slop score from 6 to 4."""
    assert runner.MAX_REVISIONS == 2
    assert isinstance(runner.MAX_REVISIONS, int)


def test_cv_signals_leaves_no_working_file_behind():
    """It writes an extracted-text file next to the document, which would
    otherwise end up in the folder the user opens."""
    d = _dir(CLEAN_CV)
    runner._quality(d, "CV.md", "cv")
    assert not (d / "CV.md.extracted.txt").exists()
