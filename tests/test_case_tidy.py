"""The first word of a line is capitalised, and only when it should be.

A CV whose skills read "**Leadership:** managing team leads" looks like
somebody stopped mid-sentence. The source CV had it and the drafts reproduced
it faithfully, which is the point: the model copies the shape of what it is
given, so this is not something to ask for politely in a prompt. Done after
the draft it is right every time and costs nothing.

Over-capitalising is the more embarrassing failure of the two, and a previous
pass over this CV title-cased things that were not titles. So the rule fires
only on a complete run of lower-case letters at the start of a line.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.runner import tidy_case


class Capitalises(unittest.TestCase):
    def test_after_a_bold_label(self):
        self.assertEqual(tidy_case("**Leadership:** managing team leads"),
                         "**Leadership:** Managing team leads")

    def test_the_start_of_a_bullet(self):
        self.assertEqual(tidy_case("- led the rollout"), "- Led the rollout")

    def test_the_start_of_a_numbered_item(self):
        self.assertEqual(tidy_case("1. led the rollout"), "1. Led the rollout")

    def test_a_plain_line(self):
        self.assertEqual(tidy_case("built the platform."), "Built the platform.")


class LeavesAlone(unittest.TestCase):
    def test_a_name_with_a_digit_in_it(self):
        # "n8n" -> "N8n" was the first thing this got wrong.
        self.assertEqual(tidy_case("**Tooling:** n8n, Docker"),
                         "**Tooling:** n8n, Docker")

    def test_a_name_with_an_internal_capital(self):
        # "macOS" -> "MacOS" was the second.
        for s in ("macOS and Linux", "iOS builds", "gRPC services", "eBay work"):
            self.assertEqual(tidy_case(s), s)

    def test_commands_that_are_conventionally_lower_case(self):
        self.assertEqual(tidy_case("**Shell:** npm and git"),
                         "**Shell:** npm and git")

    def test_a_line_that_is_already_capitalised(self):
        s = "Led the team through a two-year shift."
        self.assertEqual(tidy_case(s), s)

    def test_nothing_inside_a_line_moves(self):
        s = "- Built the skill library and the mcp platform we work through"
        self.assertEqual(tidy_case(s), s)

    def test_a_whole_document_keeps_its_shape(self):
        doc = "## Skills\n\n**AI:** large language model pipelines\n\n- led it\n"
        self.assertEqual(
            tidy_case(doc),
            "## Skills\n\n**AI:** Large language model pipelines\n\n- Led it\n")


    def test_an_email_address_is_not_a_first_word(self):
        # The contact line at the top of every letter went out reading
        # "Mcdonaldcallum@hotmail.co.uk". The old guard skipped a word only
        # when a letter or digit followed it, and "@" is neither.
        s = "mcdonaldcallum@hotmail.co.uk"
        self.assertEqual(tidy_case(s), s)

    def test_a_domain_is_not_a_first_word(self):
        for s in ("linkedin.com/in/callum-mcdonald-b416b299",
                  "github.com/maccydee",
                  "canalholidays.co.uk"):
            self.assertEqual(tidy_case(s), s)

    def test_a_whole_contact_line(self):
        s = ("mcdonaldcallum@hotmail.co.uk \u00b7 07369 241441 \u00b7 "
             "linkedin.com/in/callum-mcdonald-b416b299")
        self.assertEqual(tidy_case(s), s)

    def test_a_one_word_line_ending_in_a_full_stop_still_capitalises(self):
        # Only a dot that starts a domain disqualifies the word.
        self.assertEqual(tidy_case("done."), "Done.")


if __name__ == "__main__":
    unittest.main()
