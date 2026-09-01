"""A document that exists opens. It does not get made again.

Clicking CV on a role whose CV had already been written started a second
eight minute agent run and charged for it. The document was in the row, one
line below, as a link. A control that spends money has to be the one that
says it will, and the default action on something already made is to look at
it.

The same applies to the screening and the cover letter, and the screening is
the one that hides: it renders inline in a <details> and has no link in the
documents row at all, so anything deciding "does this exist" by reading the
rendered markup concludes it does not.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.output import interactive

SRC = Path(interactive.__file__).read_text(encoding="utf-8")
JS = interactive._JS


def _fill_acts():
    start = JS.index("function fillActs(")
    return JS[start:JS.index("\ndocument.addEventListener", start)]


class ExistingDocumentsOpen(unittest.TestCase):
    def test_the_eager_button_becomes_open_when_the_artifact_exists(self):
        b = SRC[SRC.index("    def b(kind, label"):SRC.index("    letter_btn = ")]
        self.assertIn("arts.get(kind)", b)
        self.assertIn("Open ", b)
        self.assertIn('data-redraft="1"', b)

    def test_all_three_kinds_get_the_same_treatment(self):
        # The cover letter and the screening cost money too.
        js = _fill_acts()
        for kind in ("screen", "cv", "cover_letter"):
            self.assertIn(f"pair('{kind}'", js, f"{kind} can still be redone by accident")

    def test_a_lazy_row_decides_from_the_artifacts_not_the_markup(self):
        # The screening has no link in the documents row, so reading the
        # markup would have offered to run it a second time.
        js = _fill_acts()
        self.assertIn("row.dataset[", js)
        self.assertNotIn(".docs a", js)

    def test_the_row_carries_the_address_of_what_exists(self):
        self.assertIn("data-open-", SRC)

    def test_redrafting_confirms_because_it_spends(self):
        handler = JS[JS.index("const gen=e.target.closest('[data-gen]')"):]
        handler = handler[:handler.index("poll();}")]
        self.assertIn("gen.dataset.redraft", handler)
        self.assertIn("confirm(", handler)

    def test_a_first_draft_does_not_confirm(self):
        # Only the case that can throw away work already paid for asks.
        handler = JS[JS.index("const gen=e.target.closest('[data-gen]')"):]
        handler = handler[:handler.index("poll();}")]
        confirm_at = handler.index("confirm(")
        guard_at = handler.index("if(gen.dataset.redraft)")
        self.assertLess(guard_at, confirm_at)


class ReadyIsVisibleOnTheRow(unittest.TestCase):
    def test_a_ready_document_is_named_on_the_title_line(self):
        # It was only in the documents row underneath, where it looked like
        # part of the metadata rather than a state of the role.
        self.assertIn('class="ready"', SRC)
        self.assertIn("{ready}", SRC)

    def test_every_kind_can_show_ready(self):
        block = SRC[SRC.index("    ready = "):SRC.index("    docs = []")]
        for kind in ("screen", "cv", "cover_letter"):
            self.assertIn(f'"{kind}"', block)


if __name__ == "__main__":
    unittest.main()
