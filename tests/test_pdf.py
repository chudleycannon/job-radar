"""The PDF is the artefact a person sends. The .docx is the original.

Handing back the .docx meant every caller and every dashboard link offered the
file nobody sends.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import pdf as pdf_mod
from jobradar import runner

MD = """# Dana Whitfield

Engineering Manager

## Profile

I manage six engineers.
"""


def _folder() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "CV.md").write_text(MD, encoding="utf-8")
    return d


def test_a_machine_with_no_renderer_still_gets_a_document():
    """No PDF library here and there is not going to be: adding a rendering
    engine to turn a document somebody reads once into a second copy of itself
    is a bad trade. So a missing renderer has to be silent and harmless, not
    an error, or the tool stops working on a machine that was fine."""
    d = _folder()
    with mock.patch.object(pdf_mod, "renderer", lambda: None):
        out = runner._to_docx(d, "CV.md", "CV.docx")
    assert Path(out).suffix == ".docx"
    assert Path(out).exists()


def test_the_pdf_is_what_comes_back_when_one_can_be_made():
    d = _folder()
    fake = d / "CV.pdf"

    def render(src, out=None):
        (out or fake).write_bytes(b"%PDF-1.4\n")
        return out or fake

    with mock.patch.object(pdf_mod, "docx_to_pdf", render):
        out = runner._to_docx(d, "CV.md", "CV.docx")
    assert Path(out).suffix == ".pdf", "handed back the file nobody sends"
    # The editable original is still there. It is not an alternative to the
    # PDF, it is the thing you fix a typo in.
    assert (d / "CV.docx").exists()


def test_a_renderer_that_fails_does_not_lose_the_document():
    """LibreOffice refuses to start while a desktop copy holds the default
    profile, which on somebody's own laptop is most of the time. That must
    cost the PDF and nothing else."""
    d = _folder()
    with mock.patch.object(pdf_mod, "docx_to_pdf",
                           mock.Mock(side_effect=OSError("no profile"))):
        out = runner._to_docx(d, "CV.md", "CV.docx")
    assert Path(out).suffix == ".docx"
    assert Path(out).exists()


def test_a_half_written_pdf_never_replaces_a_good_one():
    """LibreOffice names its own output and writes it progressively, so it
    renders into a temporary directory and the result is moved into place.
    Same argument as every other write in this package."""
    src = Path(tempfile.mkdtemp()) / "CV.docx"
    src.write_bytes(b"not really a docx")
    out = src.with_suffix(".pdf")
    out.write_bytes(b"%PDF-1.4\nthe good copy\n")
    with mock.patch.object(pdf_mod, "renderer", lambda: "/nonexistent/soffice"):
        assert pdf_mod.docx_to_pdf(src) is None
    assert out.read_bytes() == b"%PDF-1.4\nthe good copy\n"


def test_the_renderer_is_looked_for_where_macos_actually_puts_it():
    """The app bundle does not add itself to PATH, so `shutil.which` alone
    finds nothing on a Mac that has LibreOffice installed."""
    import inspect
    src = inspect.getsource(pdf_mod)
    assert "/Applications/LibreOffice.app/Contents/MacOS/soffice" in src


def test_the_gates_read_the_markdown_whatever_the_row_points_at():
    """The em-dash gate once opened a .docx, read the compressed zip as text,
    and reported a pass on a document that broke the rule. Handing back a PDF
    would have reproduced that exactly, through a different file extension, a
    week later."""
    d = _folder()
    (d / "source-cv.txt").write_text("I manage six engineers.\n", encoding="utf-8")
    (d / "CV.md").write_text(MD.replace("I manage six engineers.",
                                        "I manage six engineers — really."),
                             encoding="utf-8")
    (d / "CV.pdf").write_bytes(b"%PDF-1.4\nnot the prose\n")
    gates = runner._gates(d, "CV.pdf")
    assert gates["no_em_dash"] is False, "read the container instead of the document"


def test_the_letter_is_compared_using_its_markdown_not_its_pdf():
    import inspect
    src = inspect.getsource(runner)
    i = src.index("letter_f = (path.with_suffix")
    assert '".pdf"' in src[i:i + 220], "a PDF letter would be diffed as bytes"
