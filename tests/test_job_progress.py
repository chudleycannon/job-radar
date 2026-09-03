"""A running job says how long it has been running, and how long that usually
takes.

The row showed a disabled button and nothing else for the whole of an eight
minute drafting run, so the only way to tell a working job from a dead one was
to read the process table. That is the frozen-tab failure again, except eight
minutes of it, and the reader's remedy is worse than waiting: they kill a job
that was fine.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.output import interactive


class TypicalIsMeasuredNotTyped(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")
        # jobs.uid is a foreign key onto roles, so the fixture needs the role
        # to exist before it can have a job.
        self.con.execute(
            "INSERT INTO roles (uid,company,title,url,platform,first_seen,last_seen) "
            "VALUES ('u','Acme','Engineering Manager','https://e.test/1',"
            "'greenhouse','2026-01-01','2026-01-01')")

    def _done(self, kind, start, end):
        self.con.execute(
            "INSERT INTO jobs (uid,kind,state,requested_at,started_at,finished_at) "
            "VALUES ('u',?,'done',?,?,?)", (kind, start, start, end))

    def test_one_sample_is_not_an_estimate(self):
        # An estimate built from a single run is a guess wearing a number, and
        # the caller says nothing rather than quoting it.
        self._done("cv", "2026-01-01T10:00:00", "2026-01-01T10:08:00")
        self.assertEqual(store.typical_seconds(self.con, "cv"), 0)

    def test_it_is_the_median_of_completed_runs(self):
        for mins in (6, 8, 40):
            self._done("cv", "2026-01-01T10:00:00", f"2026-01-01T10:{mins:02d}:00")
        # The median, so one pathological run does not move the number the
        # reader is judging "has this hung" against.
        self.assertEqual(store.typical_seconds(self.con, "cv"), 8 * 60)

    def test_a_kind_that_has_never_finished_reports_nothing(self):
        self.assertEqual(store.typical_seconds(self.con, "cover_letter"), 0)

    def test_unfinished_and_failed_runs_are_not_counted(self):
        self.con.execute(
            "INSERT INTO jobs (uid,kind,state,requested_at,started_at) "
            "VALUES ('u','cv','running','2026-01-01T10:00:00','2026-01-01T10:00:00')")
        self.con.execute(
            "INSERT INTO jobs (uid,kind,state,requested_at,started_at,finished_at) "
            "VALUES ('u','cv','failed','2026-01-01T10:00:00','2026-01-01T10:00:00',"
            "'2026-01-01T10:00:01')")
        self.assertEqual(store.typical_seconds(self.con, "cv"), 0)


class TheRowSaysWhatIsHappening(unittest.TestCase):
    JS = interactive._JS

    def test_elapsed_is_measured_against_the_server_clock(self):
        # The browser's clock is not the server's, and on a laptop that has
        # slept they can be minutes apart, which shows a job starting in the
        # future.
        self.assertIn("j.started_at", self.JS)
        self.assertIn("now", self.JS)
        body = self.JS[self.JS.index("function progressOn("):]
        body = body[:body.index("function progressOff(")]
        self.assertNotIn("Date.now()", body)

    def test_a_missing_typical_is_simply_left_out(self):
        body = self.JS[self.JS.index("function progressOn("):]
        body = body[:body.index("function progressOff(")]
        self.assertIn("if(usual)", body)

    def test_the_line_is_removed_when_the_job_ends(self):
        self.assertIn("function progressOff(", self.JS)
        self.assertIn("progressOff(row)", self.JS)

    def test_polling_starts_from_the_jobs_api_not_from_a_rendered_button(self):
        # Past EAGER_ROWS a row has no buttons until it is touched, so keying
        # this off a busy button meant a job on row 500 showed no progress at
        # all and the tab looked idle while it ran.
        tail = self.JS[self.JS.index("if(document.querySelector('.acts button.busy'))"):]
        self.assertIn("fetch('/api/jobs')", tail)


if __name__ == "__main__":
    unittest.main()
