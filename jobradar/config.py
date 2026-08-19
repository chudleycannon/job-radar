"""Config loading. Everything the user tunes lives in one YAML file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# config.local.yaml wins if present. That is how you keep your own settings off
# a public fork: config.yaml is committed so GitHub Actions can read it, and
# config.local.yaml is gitignored for anything you would rather not publish.
SEARCH_PATH = [Path("config.local.yaml"), Path("config.yaml")]
DEFAULT_PATH = SEARCH_PATH[-1]


def resolve(path=None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("JOB_RADAR_CONFIG")
    if env:
        return Path(env)
    for p in SEARCH_PATH:
        if p.exists():
            return p
    return DEFAULT_PATH


@dataclass
class Dealbreaker:
    name: str
    pattern: str
    hard: bool = True

    def compiled(self):
        return re.compile(self.pattern, re.I)


@dataclass
class Config:
    titles_include: list[str] = field(default_factory=list)
    titles_exclude: list[str] = field(default_factory=list)

    countries: list[str] = field(default_factory=list)
    remote_ok: bool = True
    relocate_to: list[str] = field(default_factory=list)
    exclude_locations: list[str] = field(default_factory=list)

    salary_floor: float | None = None
    salary_currency: str = "GBP"

    dealbreakers: list[Dealbreaker] = field(default_factory=list)

    sectors: list[str] = field(default_factory=list)
    source_countries: list[str] = field(default_factory=list)
    use_bundled_sources: bool = True
    extra_sources: list[dict] = field(default_factory=list)

    cv_path: str = ""
    formats: list[str] = field(default_factory=lambda: ["html", "json"])
    out_dir: Path = Path("out")

    concurrency: int = 4
    timeout: int = 20
    retries: int = 2
    user_agent: str = (
        "job-radar/0.1 (+https://github.com/maccydee/job-radar) "
        "personal job search tool"
    )

    path: Path | None = None

    # ---- derived ----

    def title_include_re(self):
        if not self.titles_include:
            return None
        return re.compile("|".join(rf"\b{re.escape(t)}\b" for t in self.titles_include), re.I)

    def title_exclude_re(self):
        if not self.titles_exclude:
            return None
        return re.compile("|".join(self.titles_exclude), re.I)

    def location_exclude_re(self):
        if not self.exclude_locations:
            return None
        return re.compile("|".join(re.escape(x) for x in self.exclude_locations), re.I)


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def load(path: str | os.PathLike | None = None) -> Config:
    p = resolve(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No config at {p}. Run `job-radar setup` to create one, "
            f"or copy config.example.yaml."
        )
    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}

    titles = raw.get("titles") or {}
    loc = raw.get("locations") or {}
    sal = raw.get("salary") or {}
    src = raw.get("sources") or {}
    out = raw.get("output") or {}
    fet = raw.get("fetch") or {}

    cfg = Config(
        titles_include=_as_list(titles.get("include")),
        titles_exclude=_as_list(titles.get("exclude")),
        countries=_as_list(loc.get("countries")),
        remote_ok=bool(loc.get("remote_ok", True)),
        relocate_to=_as_list(loc.get("relocate_to")),
        exclude_locations=_as_list(loc.get("exclude")),
        salary_floor=sal.get("floor"),
        salary_currency=(sal.get("currency") or "GBP").upper(),
        dealbreakers=[
            Dealbreaker(
                name=d.get("name") or "unnamed",
                pattern=d.get("pattern") or "",
                hard=bool(d.get("hard", True)),
            )
            for d in (raw.get("dealbreakers") or [])
            if d.get("pattern")
        ],
        sectors=_as_list(raw.get("sectors")),
        source_countries=_as_list(src.get("countries")),
        use_bundled_sources=bool(src.get("use_bundled", True)),
        extra_sources=_as_list(src.get("extra")),
        cv_path=str((raw.get("cv") or {}).get("path") or ""),
        formats=_as_list(out.get("formats")) or ["html", "json"],
        out_dir=Path(out.get("dir") or "out"),
        concurrency=int(fet.get("concurrency", 4)),
        timeout=int(fet.get("timeout", 20)),
        retries=int(fet.get("retries", 2)),
        path=p,
    )

    # Everything that writes a document needs the real CV to work from, and a
    # path that has silently stopped existing produces a fabricated CV rather
    # than an error. So it is checked here, on load, not at generation time.
    if cfg.cv_path:
        cv = Path(cfg.cv_path).expanduser()
        if not cv.exists():
            raise FileNotFoundError(
                f"Your CV is configured as {cv} but there is no file there.\n"
                f"Fix `cv.path` in {p}, or run `job-radar setup` again."
            )
        cfg.cv_path = str(cv.resolve())

    # A concurrency of 40 against other people's job boards is how a useful
    # tool becomes an abusive one. Cap it and say so.
    if cfg.concurrency > 12:
        print(f"  ! concurrency {cfg.concurrency} capped to 12 (be polite to other people's servers)")
        cfg.concurrency = 12
    return cfg
