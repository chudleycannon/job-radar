"""The .docx the tool hands you, which is the only artefact anyone else sees.

Everything here was visible the moment a document was opened, and none of it
was anything the writer asked for.
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.docx import markdown_to_docx

MD = """# Callum McDonald

Engineering manager

## Profile

I manage six engineers.

- **A bullet** with bold in it
"""


def _build() -> zipfile.ZipFile:
    out = Path(tempfile.mkdtemp()) / "cv.docx"
    markdown_to_docx(MD, out)
    return zipfile.ZipFile(out)


def test_headings_are_not_shouted():
    """`<w:caps/>` on Heading1 rendered every section as PROFILE, EXPERIENCE,
    SELECTED ACHIEVEMENTS, whatever the markdown said. On a CV already
    carrying 28 distinct acronyms that is a page of capitals, and it is the
    generator shouting rather than the writer."""
    styles = _build().read("word/styles.xml").decode("utf-8")
    assert "<w:caps/>" not in styles


def test_every_style_names_the_font_so_the_document_has_one():
    """Only docDefaults named a font, and only for ascii and hAnsi. A style
    that does not name one inherits from the theme, and this file ships no
    theme part, so a word processor falls back to its own default for those
    runs: headings in one face and body text in another, in a document that
    never asked for two. `cs` and `eastAsia` are set as well so a single
    non-Latin character does not switch face mid-line."""
    styles = _build().read("word/styles.xml").decode("utf-8")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        assert f'{attr}="Calibri"' in styles, f"{attr} unset, so the reader picks"
    # Title, Heading1, Heading2, Normal, ListParagraph and the doc default.
    assert styles.count('w:ascii="Calibri"') >= 6


def test_the_generator_has_no_opinion_about_colour():
    """Headings came out maroon. Nobody chose that, and a CV is not the place
    for the tool to have a house style."""
    styles = _build().read("word/styles.xml").decode("utf-8")
    assert "7B2D42" not in styles


def test_the_document_still_says_what_the_markdown_said():
    """The point of the file. Guards the rest of this from being satisfied by
    a generator that produces a beautifully styled empty page."""
    doc = _build().read("word/document.xml").decode("utf-8")
    for want in ("Callum McDonald", "Profile", "I manage six engineers",
                 "A bullet"):
        assert want in doc
    assert "<w:b/>" in doc, "bold was dropped"


MD_CV = """# Callum McDonald

Engineering Manager · CrowdStrike

07369 241441 · x@y.co.uk

## Experience

### Deloitte, London · 2015 - 2022

Seven years.

#### Vulnerability Lead · 2021 - 2022

Built the function.
"""


def _cv() -> str:
    out = Path(tempfile.mkdtemp()) / "cv.docx"
    markdown_to_docx(MD_CV, out)
    return zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")


def _cv_styles() -> str:
    out = Path(tempfile.mkdtemp()) / "cv.docx"
    markdown_to_docx(MD_CV, out)
    return zipfile.ZipFile(out).read("word/styles.xml").decode("utf-8")


def test_a_cv_has_three_levels_because_it_has_three():
    """Section, employer, role. The sub-roles under a long employer were bold
    body text, which put them at the same weight as the employer above them
    and flattened seven years into a list of equals."""
    doc = _cv()
    for style in ("Heading1", "Heading2", "Heading3"):
        assert f'w:val="{style}"' in doc, f"{style} never used"
    assert 'w:styleId="Heading3"' in _cv_styles()


def test_a_section_heading_is_separated_from_what_came_before_it():
    """Headings sat at close to body weight and the page read as one column of
    grey. A reader skimming for Experience had nothing to aim at."""
    styles = _cv_styles()
    assert "<w:pBdr>" in styles, "no rule under section headings"
    assert 'w:val="single"' in styles


def test_the_two_lines_under_the_name_are_not_body_text():
    """The strapline and the contact details were rendered as ordinary
    paragraphs, so the top of the CV had no shape at all."""
    doc = _cv()
    assert doc.count('w:val="Subtitle"') == 2
    assert 'w:styleId="Subtitle"' in _cv_styles()
    # And only the two: the first real paragraph stays a paragraph.
    assert "Seven years." in doc


def test_margins_leave_room_for_a_two_page_cv():
    """A 955 word CV came out at three pages on inch margins, and a third page
    holding only Education reads as padding."""
    doc = _cv()
    assert 'w:top="900"' in doc and 'w:left="1000"' in doc
