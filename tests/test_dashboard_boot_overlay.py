"""The board says it is working while the browser builds it.

The server answers in under a second and the browser then spends most of
another one parsing several thousand rows. A tab that shows nothing for that
long reads as frozen, and a reader who thinks it has hung reloads, which costs
them the wait twice.

The failure to guard against is the overlay outliving the script that was
supposed to remove it. A page that says "Loading" for ever is a worse answer
than one that looks broken, because the reader waits instead of reloading.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.output import interactive

SRC = Path(interactive.__file__).read_text(encoding="utf-8")


class BootOverlay(unittest.TestCase):
    def test_it_is_the_first_thing_in_the_body(self):
        # It has to be paintable before the browser has reached the list,
        # which is the thing it is covering the wait for.
        body = SRC[SRC.index("</head><body>"):]
        boot = body.index('id="boot"')
        lst = body.index('class="list"')
        self.assertLess(boot, lst, "the overlay is written after the list")

    def test_it_is_cleared_by_the_script_that_finishes_the_board(self):
        # At the end of the init pass, which is the point the board is
        # actually usable, not merely present.
        js = interactive._JS
        self.assertIn("getElementById('boot')", js)
        tail = js[js.index("loadView();"):]
        self.assertIn("boot.remove()", tail)

    def test_it_clears_itself_if_the_script_never_runs(self):
        # Two independent fallbacks in an inline script, so a syntax error or
        # a failed load in the main script cannot strand the reader.
        inline = SRC[SRC.index("</head><body>"):SRC.index('<div class="wrap">')]
        self.assertIn("DOMContentLoaded", inline)
        self.assertRegex(inline, r"setTimeout\(.*?,\s*8000\)")
        self.assertEqual(inline.count("b.remove()"), 2)

    def test_it_states_a_real_count(self):
        # "Loading..." tells the reader nothing about how long. The row count
        # is the reason it is slow, so it is what the overlay says.
        self.assertIn("Loading {total} roles", SRC)

    def test_reduced_motion_gets_motion_that_is_not_a_spin(self):
        # A spinner that cannot spin is a still ring, which reads as broken.
        css = interactive._EXTRA_CSS
        block = css[css.index("prefers-reduced-motion"):]
        self.assertIn("bootfade", block)
        self.assertNotIn("bootspin", block[:block.index("}\n")])


if __name__ == "__main__":
    unittest.main()
