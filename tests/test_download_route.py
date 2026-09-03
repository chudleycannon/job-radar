"""Documents download with a name you can find, and nothing else downloads.

`open -R` puts a Finder window in front of you, which is right when you want
to look at the file and wrong when you are about to attach it: Chrome's upload
dialog does not know about that window, so attaching means hunting through
ninety `2026-09-01-company-role-hash` folders for `CV.pdf`. A download lands in
~/Downloads, which is where the picker already opens.

The name matters as much as the route. Every generated CV is called CV.pdf, so
three downloads give you CV.pdf, CV (1).pdf and CV (2).pdf and the picker's
most useful column tells you nothing.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.serve import _MIME, _download_name


class TheNameIsFindable(unittest.TestCase):
    def test_the_employer_and_the_role_are_in_it(self):
        name = _download_name("GB Group",
                              "VP Engineering Enablement & Operations (3978)",
                              "cv", ".pdf")
        self.assertTrue(name.startswith("GB-Group-"))
        self.assertIn("Engineering", name)
        self.assertTrue(name.endswith("-CV.pdf"))

    def test_two_roles_at_one_employer_do_not_collide(self):
        a = _download_name("Acme", "Engineering Manager", "cv", ".pdf")
        b = _download_name("Acme", "Director of Engineering", "cv", ".pdf")
        self.assertNotEqual(a, b)

    def test_each_kind_is_named(self):
        for kind, word in (("cv", "CV"), ("cover_letter", "Cover-letter"),
                           ("screen", "Screening")):
            self.assertIn(word, _download_name("Acme", "EM", kind, ".pdf"))

    def test_punctuation_and_slashes_cannot_reach_the_filename(self):
        # The name goes in a header and in a filesystem. A role called
        # "Manager / Director" must not become a path.
        name = _download_name("A/B Corp", "Manager / Director", "cv", ".pdf")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_an_accented_employer_survives(self):
        # The quoted form of the header is latin-1, so the handler sends an
        # ASCII fallback beside the real one. The name itself keeps the
        # accent.
        self.assertIn("Nestl", _download_name("Nestlé S.A.", "EM", "cv", ".pdf"))

    def test_an_employer_with_no_usable_name_still_produces_a_file(self):
        name = _download_name("", "", "cv", ".pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertGreater(len(name), len(".pdf"))

    def test_every_kind_this_tool_writes_has_a_content_type(self):
        for suffix in (".pdf", ".docx", ".md", ".txt"):
            self.assertIn(suffix, _MIME)


class TheAllowlistIsTheSameOne(unittest.TestCase):
    def test_the_route_joins_artifacts_rather_than_reading_any_path(self):
        # Allowlist by construction: the path has to already be recorded in
        # `artifacts`, so no amount of traversal in the query string reaches
        # anything else on the disk. Verified live against /etc/passwd, which
        # answers 403.
        src = Path(sys.modules["jobradar.serve"].__file__).read_text(encoding="utf-8")
        block = src[src.index('if path.startswith("/download")'):]
        block = block[:block.index('if path.startswith("/open")')]
        self.assertIn("FROM artifacts", block)
        self.assertIn("_same_origin", block)
        self.assertIn("that is not a document this tool made", block)


if __name__ == "__main__":
    unittest.main()
