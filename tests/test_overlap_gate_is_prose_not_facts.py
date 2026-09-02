"""The overlap gate must fire on repeated writing, not on a repeated figure.

Hansen Technologies' cover letter failed `no_overlap_with_cv` on
"1 325 engineer hours a year". That is one statistic: the tokeniser drops the
comma in "1,325" and the hyphen in "engineer-hours", so a single figure plus
three ordinary words reaches the six-token threshold. The two documents shared
no phrasing at all.

Worse than a false positive, it put two gates in direct conflict.
`unsourced_specifics` requires every figure in a draft to appear in the real
CV; this one then reported the figure appearing in both as overlap. A draft
could not satisfy both at once, and the honest one failed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.runner import shared_ngram

CV = ("Led a two-year move from manual, lab-dependent authoring to an "
      "AI-assisted workflow with continuous validation. Time to produce a "
      "detection fell from 20 to 30 minutes to under four, giving back "
      "650 to 1,325 engineer-hours a year.")


class AFactIsNotAPhrase(unittest.TestCase):
    def test_the_letter_that_found_this(self):
        letter = ("My team writes vulnerability detection content. One piece "
                  "used to take twenty to thirty minutes by hand and now takes "
                  "under four, worth between 650 and 1,325 engineer-hours a "
                  "year to the team.")
        self.assertEqual(shared_ngram(CV, letter), "")

    def test_a_shared_percentage_does_not_count(self):
        a = "We cut model cost 35 to 50 percent a year on tuned workflows."
        b = "Costs came down 35 to 50 percent a year across the board."
        self.assertEqual(shared_ngram(a, b), "")

    def test_a_shared_date_range_does_not_count(self):
        a = "Between 2022 and 2026 the team grew from 6 to 12 engineers."
        b = "From 2022 and 2026 headcount went 6 to 12 engineers overall."
        self.assertEqual(shared_ngram(a, b), "")


class RealOverlapIsStillCaught(unittest.TestCase):
    """A gate that cannot fail proves nothing."""

    def test_a_lifted_sentence(self):
        lifted = ("In my current role I led a two-year move from manual, "
                  "lab-dependent authoring to an AI-assisted workflow with "
                  "continuous validation, which went well.")
        found = shared_ngram(CV, lifted)
        self.assertIn("lab dependent authoring", found)

    def test_a_lifted_clause_of_ordinary_words(self):
        a = "I coach team leads into owning their own workstreams every quarter."
        b = "She coach team leads into owning their own workstreams every year."
        self.assertIn("team leads into owning their own workstreams", shared_ngram(a, b))

    def test_two_unrelated_documents_share_nothing(self):
        self.assertEqual(shared_ngram(CV, "A completely different letter."), "")

    def test_an_empty_document_is_not_an_overlap(self):
        self.assertEqual(shared_ngram(CV, ""), "")
        self.assertEqual(shared_ngram("", CV), "")


class AnAddressIsNotPhrasing(unittest.TestCase):
    """Both documents carry the same contact block on purpose.

    The tokeniser splits on punctuation, so a URL arrives as a row of
    ordinary-looking words: "linkedin.com/in/callum-mcdonald-b416b299" becomes
    six tokens, only one of which is filler. It cleared the prose check and the
    letter was failed for reusing its own header.
    """

    HEADER = ("Callum McDonald\n\nmcdonaldcallum@hotmail.co.uk \u00b7 07369 241441 "
              "\u00b7 linkedin.com/in/callum-mcdonald-b416b299 \u00b7 "
              "github.com/maccydee\n")

    def test_a_shared_contact_block_is_not_overlap(self):
        self.assertEqual(shared_ngram(self.HEADER, self.HEADER), "")

    def test_a_shared_email_alone_is_not_overlap(self):
        s = "mcdonaldcallum@hotmail.co.uk"
        self.assertEqual(shared_ngram(s, s), "")

    def test_a_shared_url_alone_is_not_overlap(self):
        s = "https://www.linkedin.com/in/callum-mcdonald-b416b299"
        self.assertEqual(shared_ngram(s, s), "")

    def test_prose_around_a_shared_address_is_still_caught(self):
        a = self.HEADER + "I coach team leads into owning their own workstreams."
        b = self.HEADER + "I coach team leads into owning their own workstreams."
        self.assertIn("team leads into owning their own workstreams",
                      shared_ngram(a, b))


if __name__ == "__main__":
    unittest.main()
