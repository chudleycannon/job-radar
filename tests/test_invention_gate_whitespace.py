"""A figure in the source CV must match the same figure in the draft.

`_NUMBER` ends `\\s?[%kKmMbB]?`, so a number followed by a space keeps that
space in the match. The source side stored the match unstripped and the draft
side stripped its token, so the two never compared equal, and the gate
reported Callum's own phone number as a specific his CV had invented. Every
number in a source CV that happened to be followed by a space was unmatchable
the same way.

A gate that cries wolf is worse than no gate: the next real invention is read
as more noise. This one guards the promise the whole tool rests on, which is
that it never claims something the person cannot claim.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.runner import _invented


class NumbersMatchAcrossWhitespace(unittest.TestCase):
    def test_a_number_followed_by_a_space_is_not_invented(self):
        source = "Called on 07369 241441 or by email."
        draft = "07369 241441 | someone@example.test"
        self.assertEqual(_invented(draft, source), [])

    def test_a_number_at_the_end_of_the_source_still_matches(self):
        self.assertEqual(_invented("Team of 12 engineers.", "Team of 12"), [])

    def test_a_thousands_separator_still_matches(self):
        self.assertEqual(_invented("17,274 elements", "17274 elements checked"), [])

    def test_a_genuinely_new_figure_is_still_caught(self):
        # The gate has to keep working. This is the claim it exists for: a
        # figure in the draft with no counterpart anywhere in the source.
        found = _invented("Led 45 engineers and grew revenue 250%.",
                          "Led a team of engineers.")
        self.assertIn("45", found)
        self.assertTrue(any("250" in f for f in found))

    def test_a_new_scale_word_is_still_caught(self):
        # "run the newsletter" became "write the monthly newsletter": one
        # word, not in the source, and the sort of thing an interviewer asks
        # about.
        self.assertIn("monthly", _invented("Wrote the monthly newsletter.",
                                           "Wrote the newsletter."))

    def test_years_and_single_digits_are_structure_not_claims(self):
        self.assertEqual(_invented("From 2019 to 2024, 1 team.", "Worked there."), [])


if __name__ == "__main__":
    unittest.main()
