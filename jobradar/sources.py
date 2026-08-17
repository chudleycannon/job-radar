"""Loading and filtering the employer list."""

from __future__ import annotations

import json
from pathlib import Path

from . import adapters
from .config import Config
from .models import Source

BUNDLED = Path(__file__).parent.parent / "sources" / "sources.json"


def load_file(path: str | Path) -> list[Source]:
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text())
    items = raw.get("sources", raw) if isinstance(raw, dict) else raw
    out = []
    for d in items:
        # Tolerate the n8n export shape, which wraps each entry in {"json": {...}}
        if isinstance(d, dict) and "json" in d and isinstance(d["json"], dict):
            d = d["json"]
        if not isinstance(d, dict) or not d.get("url"):
            continue
        out.append(adapters.prepare(Source.from_dict(d)))
    return out


def load(cfg: Config) -> list[Source]:
    srcs: list[Source] = []
    if cfg.use_bundled_sources:
        srcs.extend(load_file(BUNDLED))
    for d in cfg.extra_sources:
        if isinstance(d, str):
            d = {"company": d, "url": d}
        srcs.append(adapters.prepare(Source.from_dict(d)))

    if cfg.sectors:
        want = {s.lower() for s in cfg.sectors}
        srcs = [s for s in srcs if not s.sector or s.sector.lower() in want]
    if cfg.source_countries:
        want = {c.upper() for c in cfg.source_countries}
        srcs = [s for s in srcs if not s.country or s.country.upper() in want]

    seen, uniq = set(), []
    for s in srcs:
        if s.key in seen:
            continue
        seen.add(s.key)
        uniq.append(s)
    return uniq


def save(sources: list[Source], path: str | Path, meta: dict | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "meta": meta or {},
        "sources": [s.to_dict() for s in
                    sorted(sources, key=lambda x: (x.platform, x.company.lower()))],
    }
    p.write_text(json.dumps(body, indent=1, ensure_ascii=False))


def coverage(sources: list[Source]) -> dict:
    """Where the list is thin. This is what says which sector to harvest next."""
    by_sector: dict[str, int] = {}
    by_country: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    for s in sources:
        by_sector[s.sector or "untagged"] = by_sector.get(s.sector or "untagged", 0) + 1
        by_country[s.country or "untagged"] = by_country.get(s.country or "untagged", 0) + 1
        by_platform[s.platform or "unknown"] = by_platform.get(s.platform or "unknown", 0) + 1
    return {
        "total": len(sources),
        "by_sector": dict(sorted(by_sector.items(), key=lambda x: -x[1])),
        "by_country": dict(sorted(by_country.items(), key=lambda x: -x[1])),
        "by_platform": dict(sorted(by_platform.items(), key=lambda x: -x[1])),
    }
