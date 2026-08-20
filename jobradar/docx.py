"""Write a .docx from Markdown, with no dependencies.

The tool asked for a .docx and handed back a .md. A teacher cannot attach
Markdown to a TES application and has no editor for it, so the deliverable
stopped one step short of being a deliverable.

A .docx is a zip of XML. This writes the minimum a word processor will open:
headings, bold, bullets and paragraphs. It is not a Word feature set, it is a
readable document you can edit and send.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_W}>
<w:docDefaults><w:rPrDefault><w:rPr>
  <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="21"/>
</w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr>
  <w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr>
  <w:spacing w:before="240" w:after="80"/></w:pPr>
  <w:rPr><w:b/><w:caps/><w:sz w:val="24"/><w:color w:val="7B2D42"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr>
  <w:spacing w:before="160" w:after="60"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>
  <w:pPr><w:ind w:left="360" w:hanging="180"/><w:spacing w:after="60"/></w:pPr></w:style>
</w:styles>"""

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _runs(text: str) -> str:
    """Inline markup: bold only, which is all these documents use."""
    out, pos = [], 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            out.append(f'<w:r><w:t xml:space="preserve">{escape(text[pos:m.start()])}</w:t></w:r>')
        out.append(f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(m.group(1))}</w:t></w:r>')
        pos = m.end()
    if pos < len(text):
        out.append(f'<w:r><w:t xml:space="preserve">{escape(text[pos:])}</w:t></w:r>')
    return "".join(out) or '<w:r><w:t/></w:r>'


def _para(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_runs(text)}</w:p>"


def markdown_to_docx(md: str, out_path: Path) -> Path:
    body = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            body.append(_para(line[4:], "Heading2"))
        elif line.startswith("## "):
            body.append(_para(line[3:], "Heading1"))
        elif line.startswith("# "):
            body.append(_para(line[2:], "Title"))
        elif re.match(r"^\s*[-*+]\s+", line):
            body.append(_para("• " + re.sub(r"^\s*[-*+]\s+", "", line), "ListParagraph"))
        elif set(line.strip()) <= {"-", "="} and len(line.strip()) > 2:
            continue                       # a horizontal rule
        else:
            body.append(_para(line))

    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document {_W}><w:body>{"".join(body)}'
           f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
           f'<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
           f'</w:sectPr></w:body></w:document>')

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", doc)
    return out_path
