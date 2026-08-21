"""Just enough Markdown to read a screening in the page.

The documents this renders are written by the tool itself, so this does not
need to be a general Markdown implementation: it needs headings, bold, italic,
inline code, bullets and paragraphs, which is everything the screening prompt
produces. Anything it does not recognise falls through as ordinary text rather
than disappearing, because a screening you paid for should never be silently
truncated by a renderer that met a construct it did not like.

Everything is HTML-escaped before any markup is applied, so a job description
quoted inside a screening cannot inject anything into the dashboard.
"""

from __future__ import annotations

import html as _h
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.S)
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _inline(text: str) -> str:
    out = _h.escape(text)
    out = _CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    return out


def to_html(md: str) -> str:
    """Render a tool-written Markdown document to a small HTML fragment."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_list = False
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_para()
            close_list()
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para()
            close_list()
            # Headings inside a card must not compete with the page's own
            # hierarchy, so h1 in the document becomes h4 on the page.
            level = min(6, 3 + len(m.group(1)))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        if re.match(r"^[-*+]\s+", stripped):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(re.sub(r'^[-*+]\s+', '', stripped))}</li>")
            continue

        if re.match(r"^---+$", stripped):
            flush_para()
            close_list()
            out.append("<hr>")
            continue

        if in_list and raw.startswith(("  ", "\t")):
            # A wrapped bullet, not a new paragraph.
            if out and out[-1].endswith("</li>"):
                out[-1] = out[-1][:-5] + " " + _inline(stripped) + "</li>"
                continue

        close_list()
        para.append(stripped)

    flush_para()
    close_list()
    return "".join(out)
