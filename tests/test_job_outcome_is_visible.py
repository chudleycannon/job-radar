"""When a job finishes, the reader must be able to tell whether it worked.

The board reloads to pick up the new document, and the reload threw away the
only sign anything had happened: the toast was gone, the scroll position was
gone, and the result was one row somewhere in four thousand. The job had
worked and there was no way to know without reading the process table.

Two separate answers are needed, and the row already carried the second one.
"Finished" is not what the reader wants; "82/100, all gates passed" is.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.output import interactive

JS = interactive._JS
SRC = Path(interactive.__file__).read_text(encoding="utf-8")


class TheOutcomeSurvivesTheReload(unittest.TestCase):
    def test_what_finished_is_remembered_across_the_reload(self):
        self.assertIn("'job-radar:finished'", JS)
        stash = JS.index("setItem('job-radar:finished'")
        reload_after = JS.index("location.reload()", stash)
        # Written before the reload that would otherwise lose it, not after.
        self.assertLess(stash, reload_after)

    def test_the_key_is_cleared_once_it_has_been_used(self):
        # Otherwise every later reload re-announces a job from hours ago.
        self.assertIn("removeItem('job-radar:finished')", JS)

    def test_the_announcement_reads_the_row_rather_than_a_second_copy(self):
        # A separately-computed outcome can disagree with what the row shows,
        # and then two places on one page say different things about one job.
        body = JS[JS.index("const announce=()=>{"):]
        body = body[:body.index("\n  // The toast is the control")]
        self.assertIn(".docs", body)

    def test_a_row_hidden_by_the_filter_says_so(self):
        # Pointing at something display:none does nothing and reads as the
        # click being ignored.
        body = JS[JS.index("const announce=()=>{"):]
        self.assertIn("row.hidden", body)

    def test_the_jump_filters_rather_than_scrolls(self):
        # scrollIntoView computes against `content-visibility` estimates for
        # every row above and lands in the wrong place on a board this tall.
        body = JS[JS.index("toast.addEventListener('click'"):]
        body = body[:body.index("\n  announce();")]
        self.assertIn("r.hidden = r!==pending", body)
        self.assertNotIn("pending.scrollIntoView", body)


class GatesAreNamedNotCounted(unittest.TestCase):
    def test_a_failed_gate_says_which_rule(self):
        # "1 gate(s) failed" is a number the reader cannot act on: the only
        # way to find out which rule was to open the rating file.
        self.assertIn("_GATE_NAMES", SRC)
        for key in ("unsourced_specifics", "no_em_dash", "natural_writing"):
            self.assertIn(key, SRC)

    def test_passing_every_gate_is_stated_rather_than_left_blank(self):
        # An empty space where a warning would go is not an answer. It reads
        # the same as a gate that never ran, which is the distinction this
        # codebase exists to keep.
        self.assertIn("all gates passed", SRC)

    def test_the_offending_tokens_are_offered(self):
        self.assertIn("unsourced_found", SRC)


if __name__ == "__main__":
    unittest.main()
