"""Config loading. Everything the user tunes lives in one YAML file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The fetcher owns these: it is what has to live with them.
from .fetch import DEFAULT_CONCURRENCY, MAX_CONCURRENCY

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
    # Countries where you would need a visa. A role there that says outright
    # it will not sponsor is one you cannot take, however good the fit.
    need_sponsorship: list[str] = field(default_factory=list)
    exclude_locations: list[str] = field(default_factory=list)

    salary_floor: float | None = None
    salary_currency: str = "GBP"

    dealbreakers: list[Dealbreaker] = field(default_factory=list)

    sectors: list[str] = field(default_factory=list)
    source_countries: list[str] = field(default_factory=list)
    use_bundled_sources: bool = True
    extra_sources: list[dict] = field(default_factory=list)
    # Reed's jobseeker API is the one source here that needs a credential.
    # Empty means the Reed source is skipped with a message rather than
    # fetched into a 401. See `_api_key` for where it can come from.
    reed_api_key: str = ""
    # Adzuna needs two, and both travel in the query string because Adzuna
    # offers no header authentication. Same rule as Reed: empty means the
    # source is skipped with a message rather than fetched into a 400.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    cv_path: str = ""
    formats: list[str] = field(default_factory=lambda: ["html", "json"])
    out_dir: Path = Path("out")

    concurrency: int = DEFAULT_CONCURRENCY
    timeout: int = 20
    retries: int = 2
    user_agent: str = (
        "job-radar/0.1 (+https://github.com/maccydee/job-radar) "
        "personal job search tool"
    )

    path: Path | None = None

    # ---- derived ----

    # How a profession writes a title and how you type it are rarely the same.
    # An accountant searching "fp&a" missed "Financial Planning & Analysis
    # Manager", "FP&A Manager" and "Head of Financial Planning and Analysis":
    # 35 real roles, a 76% difference in results, with nothing to indicate it.
    ABBREVIATIONS = {
        "fp&a": "financial planning and analysis",
        "hr": "human resources",
        "qa": "quality assurance",
        "pm": "project manager",
        "bd": "business development",
        "sre": "site reliability engineer",
        "ml": "machine learning",
        "bi": "business intelligence",
        "ux": "user experience",
        "cs": "customer success",
        "m&a": "mergers and acquisitions",
        "ap": "accounts payable",
        "ar": "accounts receivable",
    }

    @staticmethod
    def _title_variants(term: str) -> set[str]:
        """Every spelling of one title worth matching.

        Covers the "&" / "and" split and the common abbreviations, in both
        directions, so it does not matter which form you typed.
        """
        out = {" ".join(term.lower().split())}
        # Two passes, because expanding an abbreviation can introduce an "and"
        # that then needs its own "&" form: fp&a -> financial planning and
        # analysis -> financial planning & analysis.
        for _ in range(2):
            for v in list(out):
                for short, long in Config.ABBREVIATIONS.items():
                    if short in v:
                        out.add(v.replace(short, long))
                    if long in v:
                        out.add(v.replace(long, short))
            for v in list(out):
                for a, b in (("&", " and "), (" and ", " & ")):
                    if a in v:
                        out.add(" ".join(v.replace(a, b).split()))
        return {" ".join(v.split()) for v in out if v.strip()}

    def title_include_re(self):
        if not self.titles_include:
            return None
        variants: set[str] = set()
        for term in self.titles_include:
            variants |= self._title_variants(term)
        return re.compile(
            "|".join(rf"\b{re.escape(v)}\b" for v in sorted(variants, key=len, reverse=True)),
            re.I)

    @staticmethod
    def _bounded(term: str) -> str:
        """One exclusion term, escaped and held to whole words.

        \\b only works next to a word character, so the boundary is added on
        each end only when that end is alphanumeric: a term ending in ")"
        would never match if it were added unconditionally.
        """
        esc = re.escape(term)
        lead = r"\b" if term[:1].isalnum() else ""
        trail = r"\b" if term[-1:].isalnum() else ""
        return f"{lead}{esc}{trail}"

    def title_exclude_re(self):
        """Escaped, unlike the include list which is also escaped.

        Left unescaped, a genuine job title containing brackets became a
        regex: "healthcare assistant (bank)" then failed to match the real
        posting and matched a different one instead.
        """
        if not self.titles_exclude:
            return None
        return re.compile("|".join(self._bounded(t) for t in self.titles_exclude),
                          re.I)

    def location_exclude_re(self):
        """Whole words, for the same reason the title list is.

        This was a bare substring match, and a substring of a place name is
        usually a different place: `exclude: [Bath]` silently dropped a role
        in Bathgate, 400 miles away, and the only trace was one more in the
        "location excluded" count. "Not London" is the most load-bearing line
        a UK user writes and the failure it produced looked exactly like it
        working.
        """
        if not self.exclude_locations:
            return None
        return re.compile("|".join(self._bounded(x) for x in self.exclude_locations),
                          re.I)


class ConfigError(ValueError):
    """A config problem worth stopping for, phrased for the person who wrote it."""


def _num(v, key: str):
    """Accept what a person actually types for money.

    `floor: £70,000` and `floor: 70,000` both parse as strings in YAML, were
    stored unconverted, and then raised TypeError deep inside the salary
    comparison partway through a scan.
    """
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "").replace("£", "").replace("$", "").replace("€", "")
    s = re.sub(r"(?i)\s*(per annum|pa|k)$", lambda m: "000" if m.group(1).lower() == "k" else "", s)
    try:
        return float(s)
    except ValueError:
        raise ConfigError(
            f"{key}: {v!r} is not a number. Write it plainly, like 70000.")


def _bool(v, key: str) -> bool:
    """`remote_ok: "no"` used to mean yes, because bool("no") is True."""
    if isinstance(v, bool):
        return v
    if v is None:
        return True
    s = str(v).strip().lower()
    if s in ("true", "yes", "y", "1", "on"):
        return True
    if s in ("false", "no", "n", "0", "off"):
        return False
    raise ConfigError(f"{key}: {v!r} is not true or false.")


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _terms(values) -> list[str]:
    """Search terms, with the blank ones dropped.

    A YAML list with an empty entry in it -- a bare `- `, or `- ""` -- put an
    empty string in `titles.include`. That is not a title nobody matches, it
    is a title EVERYBODY matches: `title_include_re` joins the terms into one
    pattern, an empty term contributes an empty alternative, and an empty
    regex matches every string there is. So the title gate stopped dropping
    anything, `score` awarded every posting its 35 points for "title matches
    your targets", and the scan reported a very large number of matches and
    looked like it had worked.

    The `titles.include is empty` guard did not catch it, because the list was
    not empty. Stripping here is what makes that guard true again.
    """
    # `v is not None` before the string test, because a bare `- ` in YAML
    # parses as None and `str(None)` is the four-character title "None",
    # which is a term that matches a posting rather than one that is skipped.
    return [" ".join(str(v).split()) for v in _as_list(values)
            if v is not None and str(v).strip()]


VALID_FORMATS = {"html", "json", "markdown", "md"}

KNOWN_KEYS = {
    "titles": {"include", "exclude"},
    "locations": {"countries", "remote_ok", "relocate_to", "exclude",
                  "need_sponsorship"},
    "salary": {"floor", "currency"},
    "cv": {"path"},
    "sources": {"use_bundled", "countries", "extra", "reed_api_key",
                "adzuna_app_id", "adzuna_app_key"},
    "output": {"formats", "dir"},
    "fetch": {"concurrency", "timeout", "retries", "user_agent"},
}
TOP_LEVEL = set(KNOWN_KEYS) | {"dealbreakers", "sectors"}


# Every country the location filter can recognise, plus the names people
# actually type. `countries: [Portugal]` used to match nothing at all: the
# filter compares against internal codes, so a Lisbon user asking for
# Portugal, Spain, Netherlands and Germany got 112 UK roles, zero from any
# country they asked for, and no warning. Names are accepted and normalised
# rather than rejected, because typing your country's name is the reasonable
# thing to do.
COUNTRY_ALIASES = {
    "united kingdom": "UK", "great britain": "UK", "britain": "UK",
    "gb": "UK", "gbr": "UK", "england": "UK", "scotland": "UK",
    "wales": "UK", "northern ireland": "UK",
    "united states": "US", "usa": "US", "america": "US",
    "ireland": "IE", "eire": "IE", "germany": "DE", "deutschland": "DE",
    "france": "FR", "spain": "ES", "espana": "ES", "netherlands": "NL",
    "holland": "NL", "canada": "CA", "australia": "AU", "new zealand": "NZ",
    "uae": "AE", "united arab emirates": "AE", "singapore": "SG",
    "hong kong": "HK", "india": "IN", "japan": "JP", "china": "CN",
    "poland": "PL", "portugal": "PT", "sweden": "SE", "switzerland": "CH",
    "israel": "IL", "brazil": "BR", "mexico": "MX", "south africa": "ZA",
    "indonesia": "ID", "thailand": "TH", "malaysia": "MY",
    "philippines": "PH", "italy": "IT", "italia": "IT", "belgium": "BE",
    "austria": "AT", "denmark": "DK", "norway": "NO", "finland": "FI",
    "czech republic": "CZ", "czechia": "CZ", "romania": "RO",
    "turkey": "TR", "argentina": "AR", "vietnam": "VN", "south korea": "KR",
}

# Codes the pipeline uses, taken from the filter itself so the two cannot
# drift. Imported lazily to keep config.py free of a screen.py dependency.
def _known_country_codes() -> set[str]:
    from .screen import _COUNTRY_MARKERS
    return set(_COUNTRY_MARKERS)


def _countries(values, where: str) -> list[str]:
    """Normalise a country list, and refuse anything the filter cannot use.

    Silence here is the worst possible behaviour: an unrecognised entry does
    not loosen the filter, it removes that country from it entirely, and the
    results still look like a working scan.
    """
    known = _known_country_codes()
    out = []
    for v in values:
        t = str(v).strip()
        if not t:
            continue
        code = COUNTRY_ALIASES.get(t.lower(), t.upper())
        if code not in known:
            near = [c for c in sorted(known) if c.startswith(t[:1].upper())]
            hint = f" Did you mean {', '.join(near[:4])}?" if near else ""
            raise ConfigError(
                f"{where}: {t!r} is not a country this tool recognises.{hint} "
                f"Use a two-letter code (or UK). Valid: {', '.join(sorted(known))}")
        if code not in out:
            out.append(code)
    return out


# Only currencies the salary parser can actually produce. `currency: euro`
# uppercased to EURO, never matched EUR, and silently switched the floor off
# on every euro role.
VALID_CURRENCIES = {"GBP", "USD", "EUR"}
_CURRENCY_ALIASES = {"POUND": "GBP", "POUNDS": "GBP", "STERLING": "GBP",
                     "EURO": "EUR", "EUROS": "EUR", "DOLLAR": "USD",
                     "DOLLARS": "USD", "US$": "USD", "USDOLLAR": "USD"}


def _currency(v, where: str) -> str:
    t = str(v or "GBP").strip().upper()
    t = _CURRENCY_ALIASES.get(t, t)
    if t not in VALID_CURRENCIES:
        raise ConfigError(
            f"{where}: {v!r} is not a currency this tool compares. "
            f"Valid: {', '.join(sorted(VALID_CURRENCIES))}. Salaries in any "
            f"other currency are shown and never compared to your floor.")
    return t


def _api_key(value, env_var: str) -> str:
    """A credential, from the config file or from the environment.

    Both, because this repo has two kinds of user and neither route serves the
    other. Locally the key belongs in config.local.yaml, which is gitignored;
    in GitHub Actions there is no local file at all and the key arrives as a
    secret in the environment. Reading only one of the two strands the other.

    The file wins when it has a value, so a stale export in a shell cannot
    quietly override the key someone just wrote down. Empty means no key, and
    the source that needs one is skipped and says so, rather than being
    fetched into a 401 that reads like a broken board.
    """
    v = str(value or "").strip()
    return v or os.environ.get(env_var, "").strip()


def _sectors(values) -> list[str]:
    """Refuse a sector tag that is not in the bundled list.

    `sectors: [hospitality]` is the obvious thing for a restaurant manager to
    write. It is not a tag, so it matched nothing, switched off 299 of 307
    sources, and still printed a normal-looking scan.
    """
    from .sources import BUNDLED
    import json as _json
    try:
        raw = _json.loads(BUNDLED.read_text(encoding="utf-8"))
        items = raw.get("sources", raw) if isinstance(raw, dict) else raw
        known = {(d.get("sector") or "").lower() for d in items if isinstance(d, dict)}
        known.discard("")
    except (OSError, ValueError):
        return [str(v).strip().lower() for v in values if str(v).strip()]
    out = []
    for v in values:
        t = str(v).strip().lower()
        if not t:
            continue
        if t not in known:
            raise ConfigError(
                f"sectors: {v!r} is not a tag in the bundled source list, so "
                f"it would match no employers at all. Valid: "
                f"{', '.join(sorted(known))}. Leave `sectors` empty to watch "
                f"every employer, and use `job-radar discover <employer> "
                f"--add` to add your own.")
        if t not in out:
            out.append(t)
    return out


def _dealbreakers(rows) -> list[Dealbreaker]:
    """Refuse quietly-broken entries rather than dropping them.

    A missing `pattern`, a typo'd key, or a regex that does not compile used to
    vanish without a word, so a dealbreaker you thought was protecting you was
    simply absent.
    """
    out = []
    for i, d in enumerate(rows or []):
        if not isinstance(d, dict):
            raise ConfigError(f"dealbreakers[{i}]: expected a name and a pattern.")
        name = d.get("name") or f"dealbreakers[{i}]"
        unknown = set(d) - {"name", "pattern", "hard"}
        if unknown:
            raise ConfigError(
                f"dealbreakers '{name}': unknown key(s) {sorted(unknown)}. "
                f"Did you mean 'pattern'?")
        if not d.get("pattern"):
            raise ConfigError(f"dealbreakers '{name}': no pattern, so it would "
                              f"never match anything.")
        try:
            re.compile(d["pattern"], re.I)
        except re.error as e:
            raise ConfigError(f"dealbreakers '{name}': the pattern is not a "
                              f"valid regular expression ({e}).")
        out.append(Dealbreaker(name=name, pattern=d["pattern"],
                               hard=_bool(d.get("hard", True),
                                          f"dealbreakers '{name}'.hard")))
    return out


def _check_keys(raw: dict) -> None:
    """Catch `sector:` for `sectors:` at load, not by wondering why nothing
    filtered."""
    unknown = set(raw) - TOP_LEVEL
    if unknown:
        raise ConfigError(f"unknown setting(s) {sorted(unknown)}. "
                          f"Valid: {sorted(TOP_LEVEL)}")
    for section, allowed in KNOWN_KEYS.items():
        block = raw.get(section)
        if isinstance(block, dict):
            extra = set(block) - allowed
            if extra:
                raise ConfigError(f"{section}: unknown key(s) {sorted(extra)}. "
                                  f"Valid: {sorted(allowed)}")


def load(path: str | os.PathLike | None = None) -> Config:
    p = resolve(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No config at {p}. Run `job-radar setup` to create one, "
            f"or copy config.example.yaml."
        )
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _check_keys(raw)

    titles = raw.get("titles") or {}
    loc = raw.get("locations") or {}
    sal = raw.get("salary") or {}
    src = raw.get("sources") or {}
    out = raw.get("output") or {}
    fet = raw.get("fetch") or {}

    cfg = Config(
        titles_include=_terms(titles.get("include")),
        titles_exclude=_terms(titles.get("exclude")),
        countries=_countries(_as_list(loc.get("countries")), "locations.countries"),
        remote_ok=_bool(loc.get("remote_ok", True), "locations.remote_ok"),
        relocate_to=_countries(_as_list(loc.get("relocate_to")), "locations.relocate_to"),
        need_sponsorship=_countries(_as_list(loc.get("need_sponsorship")),
                                    "locations.need_sponsorship"),
        exclude_locations=_as_list(loc.get("exclude")),
        salary_floor=_num(sal.get("floor"), "salary.floor"),
        salary_currency=_currency(sal.get("currency"), "salary.currency"),
        dealbreakers=_dealbreakers(raw.get("dealbreakers")),
        sectors=_sectors(_as_list(raw.get("sectors"))),
        source_countries=_countries(_as_list(src.get("countries")), "sources.countries"),
        use_bundled_sources=_bool(src.get("use_bundled", True), "sources.use_bundled"),
        extra_sources=_as_list(src.get("extra")),
        reed_api_key=_api_key(src.get("reed_api_key"), "REED_API_KEY"),
        adzuna_app_id=_api_key(src.get("adzuna_app_id"), "ADZUNA_APP_ID"),
        adzuna_app_key=_api_key(src.get("adzuna_app_key"), "ADZUNA_APP_KEY"),
        cv_path=str((raw.get("cv") or {}).get("path") or ""),
        formats=_as_list(out.get("formats")) or ["html", "json"],
        out_dir=Path(out.get("dir") or "out"),
        concurrency=int(fet.get("concurrency", DEFAULT_CONCURRENCY)),
        timeout=int(fet.get("timeout", 20)),
        retries=int(fet.get("retries", 2)),
        # Read, not just accepted. `user_agent` was in KNOWN_KEYS, so setting
        # it passed validation and told the user nothing was wrong, and then
        # the dataclass default won anyway: a config asking to identify itself
        # as something else was silently overruled. Accepting a setting and
        # ignoring it is worse than rejecting it, because the rejection is at
        # least visible.
        user_agent=str(fet.get("user_agent") or Config.user_agent),
        path=p,
    )

    if not cfg.titles_include:
        raise ConfigError(
            "titles.include is empty. It is required: with no titles every "
            "posting matches, and the keyword-driven sources (NHS Jobs, "
            "LinkedIn) have nothing to search for.")

    bad = [f for f in cfg.formats if f not in VALID_FORMATS]
    if bad:
        raise ConfigError(f"output.formats: {bad} is not a format. "
                          f"Valid: html, json, markdown.")

    cfg.out_dir = Path(str(cfg.out_dir)).expanduser()

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

    # This used to be capped at 12 because it was the only thing standing
    # between the tool and a burst against somebody's job board. It is not any
    # more: `fetch` paces each host on its own clock, so this number decides
    # how many DIFFERENT hosts are in flight, not how hard any one of them is
    # hit. On a list where roughly 7,748 hosts hold a single board each, a low
    # cap here bought no politeness at all and cost most of an hour.
    #
    # There is still a ceiling, because the sockets, file descriptors and DNS
    # lookups are the user's own and a four-figure number here just exhausts
    # them. 64 is high enough that the per-host limits are what bounds a scan
    # and low enough to stay inside a default macOS descriptor limit.
    if cfg.concurrency > MAX_CONCURRENCY:
        print(f"  ! concurrency {cfg.concurrency} capped to {MAX_CONCURRENCY} "
              f"(per-host pacing is what keeps this polite, but the sockets "
              f"are still yours)")
        cfg.concurrency = MAX_CONCURRENCY
    if cfg.concurrency < 1:
        print(f"  ! concurrency {cfg.concurrency} raised to 1")
        cfg.concurrency = 1
    return cfg
