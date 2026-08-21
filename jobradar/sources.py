"""Loading and filtering the employer list."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote_plus

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


def expand_templates(srcs: list[Source], titles: list[str]) -> list[Source]:
    """Turn one templated source into one search per title you care about.

    Some platforms are searches, not employer boards: NHS Jobs and LinkedIn
    return whatever keyword you give them. Shipping those as fixed URLs shipped
    the author's own job titles, so a nurse running this got eight searches for
    "engineering manager" inside the NHS and zero results out of 24,719
    postings. The keywords have to come from the user.
    """
    out: list[Source] = []
    for s in srcs:
        if not s.keyword_template:
            out.append(s)
            continue
        if not titles:
            continue          # nothing to search for; a frozen guess is worse
        for title in titles[:6]:
            kw = quote_plus(title)
            out.append(Source(
                company=f"{s.company}: {title}", url=s.url.format(keyword=kw),
                platform=s.platform, sector=s.sector, country=s.country,
                domain=s.domain, method=s.method, body=s.body))
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

    srcs = expand_templates(srcs, cfg.titles_include)

    seen, uniq = set(), []
    for s in srcs:
        if s.key in seen:
            continue
        seen.add(s.key)
        uniq.append(s)
    return uniq


def save(sources: list[Source], path: str | Path, meta: dict | None = None) -> None:
    """Write the list, merging metadata rather than replacing it.

    Replacing it meant the weekly prune silently deleted the provenance note,
    the version and the harvest counts, and the release process then told you
    to bump a version that no longer existed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text())
            existing = prev.get("meta", {}) if isinstance(prev, dict) else {}
        except (json.JSONDecodeError, OSError):
            existing = {}
    body = {
        "meta": {**existing, **(meta or {})},
        "sources": [s.to_dict() for s in
                    sorted(sources, key=lambda x: (x.platform, x.company.lower()))],
    }
    p.write_text(json.dumps(body, indent=1, ensure_ascii=False))


def age_days(path=None) -> int | None:
    """How long since the bundled list was last checked against reality.

    The list is data and it rots: boards migrate between applicant tracking
    systems, tokens get renamed, companies are acquired. Revalidating found 23
    dead boards in one pass, 19 of which had simply moved ATS and were hiding
    762 live roles.

    The weekly job fixes that upstream. It cannot fix anyone's copy, which is
    the part nothing said out loud: a clone freezes its source list on the day
    it was cloned, and a fork only ever prunes its own, because the crawler
    that finds new employers is not in this repository. Either way the fix is
    a pull, and someone has to be told that.
    """
    from datetime import date
    try:
        meta = json.loads(Path(path or BUNDLED).read_text()).get("meta", {})
    except (OSError, ValueError, AttributeError):
        return None
    stamp = meta.get("checked") or meta.get("validated")
    if not stamp:
        return None
    try:
        return (date.today() - date.fromisoformat(str(stamp)[:10])).days
    except ValueError:
        return None


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
