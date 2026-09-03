"""Nothing in the package may use syntax newer than `requires-python`.

Python 3.12 lifted the rule that an f-string expression cannot contain a
backslash. Development here runs on 3.13, so a line written with one parses
perfectly, imports fine, passes every local test, and raises SyntaxError on
3.10 and 3.11.

That is what it did. Thirteen consecutive red CI runs, whose only visible
symptom was 26 test files reporting "could not import", while the local suite
reported 1,213 passing. The claim and the reality had been apart for three
hours.

`ast.parse(..., feature_version=(3, 10))` does NOT catch this: 3.13 tokenises
f-strings under the new rules whatever feature_version says. Checked, rather
than assumed. So this reads the source instead, and it is deliberately narrow:
it looks for one mistake it can find reliably rather than pretending to be a
3.10 parser.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "jobradar"

# An f-string prefix, then any {...} that has no nested brace in it. Single
# line only. A backslash inside one of those is the error.
_FSTRING = re.compile(r"\bf?[rbu]*f[rbu]*['\"]")
_FIELD = re.compile(r"\{([^{}]*)\}")


def _offences(text: str):
    for n, line in enumerate(text.splitlines(), 1):
        if not _FSTRING.search(line):
            continue
        for expr in _FIELD.findall(line):
            if "\\" in expr:
                yield n, line.strip()
                break


class SyntaxStaysInsideTheFloor(unittest.TestCase):
    def test_the_floor_is_still_what_this_test_assumes(self):
        # If the package ever requires 3.12, this whole test can go.
        #
        # Read with a regex, not `tomllib`, which is stdlib only from 3.11.
        # The first version of this test imported it and failed to import on
        # the very interpreter it exists to protect. The suite has to run on
        # the floor, which is the whole point.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m, "pyproject.toml declares no requires-python")
        self.assertEqual(m.group(1), ">=3.10")

    def test_no_f_string_expression_contains_a_backslash(self):
        bad = []
        for path in sorted(PKG.rglob("*.py")):
            for n, line in _offences(path.read_text(encoding="utf-8")):
                bad.append(f"{path.relative_to(ROOT)}:{n}  {line[:90]}")
        self.assertEqual(bad, [], "\n".join(
            ["a backslash in an f-string expression is a SyntaxError before "
             "3.12, and the local interpreter will not tell you:"] + bad))

    def test_the_check_finds_the_line_that_caused_this(self):
        # A test that cannot fail proves nothing. This is the line that shipped.
        line = '''        f'{"" if eager else " data-lazyacts=\\\\"1\\\\""}>' '''
        self.assertTrue(list(_offences(line)))

    def test_the_check_does_not_fire_on_an_ordinary_f_string(self):
        for ok in ("""    f'<div class="x">{name}</div>'""",
                   """    print(f"{a} and {b!r}")""",
                   """    s = "a\\\\nb"  # a backslash outside any f-string"""):
            self.assertEqual(list(_offences(ok)), [], ok)


if __name__ == "__main__":
    unittest.main()
