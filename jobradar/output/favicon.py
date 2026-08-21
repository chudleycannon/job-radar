"""The dashboard's tab icon.

Drawn as an SVG rather than exported as a bitmap, for reasons that matter at
this size. A favicon is rendered at 16 and 32 CSS pixels, where a downscaled
PNG turns to mush and a generated illustration loses whatever made it good. An
SVG stays crisp at every size and on every display, weighs a few hundred bytes,
inlines as a data URI so the page needs no second request and the static export
still works from a file:// URL with no assets beside it, and it carries no
binary blob into the repository.

The mark is three arcs and a dot: a signal sweeping outward, with one return.
That is the whole product in one shape. It has to survive being sixteen pixels
across, so there is no text, no gradient, no fine detail, and the dot is
deliberately oversized relative to the arcs so it still reads as a blip rather
than as dirt on the screen.

Colours come from the dashboard's own palette (`--accent` steel blue, `--pay`
green) rather than being picked separately, and the tile is dark in both
themes: a browser tab strip is its own surface and an icon that flips with the
page theme reads as a different site.
"""

from __future__ import annotations

from urllib.parse import quote

# 32x32 viewBox: browsers rasterise from this, and a round number keeps the
# stroke on whole pixels at 16 and 32.
SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#18181b"/>
<g fill="none" stroke="#6ba3e8" stroke-linecap="round">
<path d="M11.5 25.5a13 13 0 0 1 0-19" stroke-width="3.6" opacity=".45"/>
<path d="M17.5 21.5a6.5 6.5 0 0 1 0-11" stroke-width="3.6"/>
</g>
<circle cx="24.5" cy="16" r="4.6" fill="#5fbf8d"/>
</svg>"""


def data_uri() -> str:
    """The icon as a data: URI, ready for a <link rel="icon"> href.

    Percent-encoded rather than base64: an SVG this small stays smaller and
    stays readable in the page source, and base64 would defeat the point of
    shipping something a person can edit.
    """
    # "#" must NOT be in the safe set. Left unencoded it starts a URL fragment,
    # so the browser truncated the whole icon at the first colour value and the
    # href arrived thirty bytes long. Quotes are encoded too, since the result
    # goes inside an HTML attribute.
    return "data:image/svg+xml," + quote(" ".join(SVG.split()), safe="/:=<>? ")


def link_tag() -> str:
    return f'<link rel="icon" href="{data_uri()}">'


def write(path) -> None:
    """Write the standalone file, for anything that wants a real asset."""
    from pathlib import Path
    Path(path).write_text(SVG)


def mark(size: int = 34) -> str:
    """The same mark, inline, for the page header.

    Reusing the favicon's geometry rather than drawing a second logo keeps the
    tab icon and the page header recognisably the same object. Rendered inline
    rather than referenced, so the static export still shows it when opened
    straight from a file.
    """
    return (SVG.replace("<svg ", f'<svg width="{size}" height="{size}" '
                        'class="mark" aria-hidden="true" ')
               .replace("\n", ""))
