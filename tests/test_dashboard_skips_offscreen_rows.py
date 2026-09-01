"""Rows the reader is not looking at must not cost a layout.

The board answers in under a second and then froze for seconds, because the
browser laid out and painted all 4,191 rows before it would show the first.
Measured in the page, forcing a full layout pass both ways: 26.3ms with
`content-visibility: auto` against 1,016ms without it.

Every row stays in the DOM. That is load-bearing rather than incidental: the
filters, the counts and the status writes all walk `.row`, and a virtualised
list that removed them would break all three. This changes what the browser
does with a row, not what the page holds.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.output import html


class OffscreenRowsAreSkipped(unittest.TestCase):
    def test_the_row_rule_skips_offscreen_work(self):
        self.assertIn("content-visibility:auto", html._CSS)

    def test_it_carries_an_intrinsic_size(self):
        # Without one the scrollbar jumps as rows come into view, because a
        # row that has not been laid out is assumed to have no height.
        self.assertRegex(html._CSS, r"contain-intrinsic-size:\s*auto \d+px")

    def test_a_filtered_row_is_still_hidden(self):
        # `content-visibility` skips rendering; it does not hide. The filters
        # rely on the explicit rule that beats the UA stylesheet, and that
        # rule has been lost once before.
        self.assertIn(".row[hidden]{display:none}", html._CSS)


if __name__ == "__main__":
    unittest.main()
