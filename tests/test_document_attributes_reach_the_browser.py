"""The attribute a row writes must be the key the script reads.

`cover_letter` produced `data-open-cover_letter`, which the browser exposes as
`dataset.openCover_letter`, while the script asked for `openCoverLetter`. The
two never met. On every lazy row the cover letter showed a plain draft button
instead of Download and Open, so the only thing that button could do was pay
to write a second one.

Nothing failed. There was no error, and the row looked exactly like a role
with no letter yet, which is why it survived a browser check that confirmed
the CV and the screening were fine.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.output import interactive

SRC = Path(interactive.__file__).read_text(encoding="utf-8")
JS = interactive._JS


def _dataset_key(attr: str) -> str:
    """What the browser calls `data-foo-bar`: the DOMStringMap rule."""
    body = attr[len("data-"):]
    out, up = "", False
    for ch in body:
        if ch == "-":
            up = True
            continue
        out += ch.upper() if up else ch
        up = False
    return out


class AttributesSurviveTheBrowser(unittest.TestCase):
    def test_every_kind_has_a_suffix(self):
        self.assertEqual(set(interactive.DOC_ATTR),
                         {"screen", "cv", "cover_letter"})

    def test_no_suffix_contains_an_underscore(self):
        # An underscore is left alone by the dataset rule while a dash becomes
        # a capital, so any suffix with one produces a key nothing predicts.
        for kind, suffix in interactive.DOC_ATTR.items():
            self.assertNotIn("_", suffix, kind)
            self.assertTrue(suffix.islower(), suffix)

    def test_the_attribute_maps_to_the_key_the_script_reads(self):
        for suffix in interactive.DOC_ATTR.values():
            key = _dataset_key(f"data-open-{suffix}")
            wanted = "open" + suffix[0].upper() + suffix[1:]
            self.assertEqual(key, wanted)

    def test_the_script_is_given_the_suffix_rather_than_deriving_it(self):
        # Deriving it from the kind is what lost the cover letter.
        fill = JS[JS.index("function fillActs("):]
        fill = fill[:fill.index("\ndocument.addEventListener")]
        self.assertNotIn("replace(/_(.)/g", fill)
        for suffix in interactive.DOC_ATTR.values():
            self.assertIn(f"'{suffix}'", fill, f"{suffix} is never passed to pair()")

    def test_all_three_kinds_are_offered_on_a_lazy_row(self):
        fill = JS[JS.index("function fillActs("):]
        fill = fill[:fill.index("\ndocument.addEventListener")]
        for kind in interactive.DOC_ATTR:
            self.assertIn(f"pair('{kind}'", fill)

    def test_the_row_writes_the_attribute_from_the_same_map(self):
        self.assertIn("DOC_ATTR.items()", SRC)
        self.assertIn("data-open-{suffix}", SRC)


if __name__ == "__main__":
    unittest.main()
