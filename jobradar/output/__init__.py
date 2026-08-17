"""Output formats."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Job
from . import html as html_out


def write_json(path: Path, new: list[Job], seen: list[Job], meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "new": [j.to_dict() for j in new],
        "matching": [j.to_dict() for j in seen],
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def write_markdown(path: Path, new: list[Job], seen: list[Job], meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def block(js: list[Job]) -> str:
        out = []
        for j in js:
            pay = j.salary.raw if j.salary.confirmed else "unconfirmed salary"
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
    path.write_text(body, encoding="utf-8")
    return path


__all__ = ["html_out", "write_json", "write_markdown"]
