"""Render a .docx to PDF, when this machine can.

A PDF is what a person sends. It is what an application form asks for, it
looks the same on every machine, and it cannot be edited by accident on the
way. The .docx stays because it is the editable original and some applicant
tracking systems still parse it more reliably, but the PDF is the artefact.

There is no PDF library here and there is not going to be. The tool installs
on `requests` and `PyYAML`, and adding a rendering engine to turn a document
somebody reads once into a second copy of itself is a bad trade. Instead this
asks the machine whether it already has LibreOffice, which is the one thing
that renders a .docx faithfully without a licence, and does nothing if it does
not. A missing renderer is not an error: the .docx is still there and still
opens.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# The command if it is on PATH, then the place macOS actually puts it, because
# the app bundle does not add itself to PATH.
_CANDIDATES = (
    "soffice", "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/lib/libreoffice/program/soffice",
)

TIMEOUT = 120


def renderer() -> str | None:
    """The LibreOffice binary, or None if this machine has no renderer."""
    for c in _CANDIDATES:
        found = shutil.which(c) if "/" not in c else (c if Path(c).exists() else None)
        if found:
            return found
    return None


def docx_to_pdf(src: Path, out: Path | None = None) -> Path | None:
    """Render `src` to PDF next to it. Returns the path, or None.

    Rendered into a temporary directory and then moved, for two reasons.
    LibreOffice names the output itself, after the input, so pointing it at
    the destination directly gives no control over the filename. And it writes
    the file progressively, so a run killed part way through would otherwise
    leave a truncated PDF sitting where a good one used to be. See
    state.atomic_write_bytes for the same argument made about every other
    write in this package.
    """
    src = Path(src)
    if not src.exists():
        return None
    soffice = renderer()
    if soffice is None:
        return None
    out = Path(out) if out else src.with_suffix(".pdf")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf",
                 str(src), "--outdir", tmp],
                capture_output=True, text=True, timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                # A user profile of its own. LibreOffice refuses a second
                # headless run while a desktop copy holds the default
                # profile, which on somebody's own laptop is most of the time.
                env={**os.environ, "HOME": tmp},
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        made = Path(tmp) / (src.stem + ".pdf")
        if not made.exists() or made.stat().st_size == 0:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        from .state import atomic_write_bytes
        atomic_write_bytes(out, made.read_bytes())
    return out
