"""Output formats."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Job
from ..state import atomic_write_text
from . import html as html_out


def write_json(path: Path, new: list[Job], seen: list[Job], meta: dict) -> Path:
    path = Path(path)
    # Atomic. The roles are in the database, so this file can be regenerated,
    # but only by another full scan over the network. Half a JSON file is a
    # parse error for anything reading it, and the old complete one is worth
    # more than a fresh broken one.
    return atomic_write_text(path, json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "new": [j.to_dict() for j in new],
        "matching": [j.to_dict() for j in seen],
    }, indent=1, ensure_ascii=False))


def write_markdown(path: Path, new: list[Job], seen: list[Job], meta: dict) -> Path:
    path = Path(path)

    def block(js: list[Job]) -> str:
        out = []
        for j in js:
            pay = j.salary.label()
            loc = j.location or "location not stated"
            out.append(f"- **[{j.title}]({j.url})** · {j.company} · {loc} · {pay} · score {j.score:.0f}")
            if j.flags:
                out.append(f"  - flags: {'; '.join(j.flags)}")
        return "\n".join(out) or "_nothing_"

    body = f"""# Job radar

{datetime.now().strftime('%d %b %Y, %H:%M')} · {meta.get('sources_ok', 0)} of {meta.get('sources_total', 0)} sources responded

## New since last run ({len(new)})

{block(new)}

## Everything else matching ({len(seen)})

{block(seen)}

---

Roles with a stated salary below your floor are hidden. Roles with no stated salary
are shown and marked, because most employers do not publish one.
"""
    return atomic_write_text(path, body)


__all__ = ["html_out", "write_json", "write_markdown"]
