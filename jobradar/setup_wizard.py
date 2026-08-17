"""Interactive config builder.

Deliberately a plain CLI wizard rather than something clever, because the
people this tool is meant to widen to are exactly the ones who will not
hand-edit YAML. The `/job-radar setup` skill wraps this same writer with a
conversational layer; both end at `write_config`, so the two front doors
cannot drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

COMMON_DEALBREAKERS = {
    "coding round": r"take.?home|live coding|coding (?:test|assessment|challenge|exercise)|"
                    r"pair.?program\w* (?:interview|round)|technical assessment",
    "hands-on / player-coach": r"player.?coach|hands.on cod|writes? code|still cod|"
                               r"contribut\w+ to (?:the )?code|roll up your sleeves",
    "on-call": r"on.?call rota|24/7 on.?call|carry the pager",
    "shift work": r"shift (?:work|pattern)|night shift|rotating shifts",
    "travel heavy": r"travel (?:up to )?(?:5\d|[6-9]\d)%|frequent(?:ly)? travel|extensive travel",
    "pre-sales": r"pre.?sales|solutions? (?:architect|engineer)|forward deployed",
    "manages managers": r"managing managers|manager of managers|people managers report",
}

SECTORS = [
    "technology", "finance", "healthcare", "public-sector", "education",
    "retail", "manufacturing", "professional-services", "media", "transport",
]

_TITLE_HINT = re.compile(
    r"^\s*(?:[A-Z][\w/&.-]*\s+){0,4}"
    r"(manager|director|lead|head|engineer|analyst|architect|consultant|specialist|"
    r"officer|administrator|coordinator|designer|scientist|nurse|teacher|accountant)"
    r"(?:\s+[A-Za-z/&,.-]+){0,3}\s*$",
    re.I | re.M,
)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        v = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return v or default


def _ask_list(prompt: str, default: list[str] | None = None) -> list[str]:
    d = ", ".join(default or [])
    v = _ask(f"{prompt} (comma separated)", d)
    return [x.strip() for x in v.split(",") if x.strip()]


def _ask_yn(prompt: str, default: bool = True) -> bool:
    v = _ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return v.startswith("y")


def titles_from_cv(text: str, limit: int = 12) -> list[str]:
    """Pull plausible job titles out of pasted CV text.

    Crude on purpose: it reads lines that look like job titles and returns the
    common ones. It is a starting point the user edits, not a recommendation,
    and it says so. The skill layer does this properly with a model.
    """
    hits: dict[str, int] = {}
    for m in _TITLE_HINT.finditer(text):
        t = " ".join(m.group(0).split()).lower().strip(" ,.-")
        if 3 <= len(t) <= 45:
            hits[t] = hits.get(t, 0) + 1
    ranked = sorted(hits.items(), key=lambda x: (-x[1], len(x[0])))
    return [t for t, _ in ranked[:limit]]


def write_config(path: Path, answers: dict) -> Path:
    """The single place a config file is written. Keeps comments, because the
    file is meant to stay hand-editable after the wizard has run.
    """
    def ylist(items, indent="    "):
        if not items:
            return " []"
        return "\n" + "\n".join(f"{indent}- {_q(i)}" for i in items)

    def _q(v):
        s = str(v)
        return f'"{s}"' if re.search(r"[:#{}\[\],&*?|>'\"%@`]|^\s|\s$", s) else s

    dealbreakers = answers.get("dealbreakers") or {}
    db_block = "\n".join(
        f"  - name: {name}\n    pattern: {_q(pat)}\n    hard: true"
        for name, pat in dealbreakers.items()
    ) or "  []"

    extra = answers.get("extra_sources") or []
    extra_block = "\n".join(
        f"    - company: {_q(s.get('company'))}\n      url: {_q(s.get('url'))}"
        f"\n      platform: {s.get('platform','')}" for s in extra
    ) or "    []"

    body = f"""# job-radar config
# Everything the tool does is decided here. Edit freely; re-running
# `job-radar setup` updates this file rather than replacing it.

titles:
  # Roles you want. Matched against the posting title.
  include:{ylist(answers.get('titles_include'))}
  # Titles to never show you, even if they match above.
  exclude:{ylist(answers.get('titles_exclude'))}

locations:
  countries:{ylist(answers.get('countries'))}
  remote_ok: {str(answers.get('remote_ok', True)).lower()}
  # Places you would move to. Scored lower than home, but still shown.
  relocate_to:{ylist(answers.get('relocate_to'))}
  # Never show roles in these places.
  exclude:{ylist(answers.get('exclude_locations'))}

salary:
  # A role whose STATED pay is below this is hidden.
  # A role with NO stated pay is always shown, marked "unconfirmed salary",
  # because most employers still do not publish a figure and hiding those
  # would throw away most of the market.
  floor: {answers.get('salary_floor') or 'null'}
  currency: {answers.get('salary_currency', 'GBP')}

# Read against the job description. A `hard` match hides the role.
dealbreakers:
{db_block}

# Which employers to watch. Empty means all of them.
sectors:{ylist(answers.get('sectors'), indent="  ")}

sources:
  use_bundled: {str(answers.get('use_bundled', True)).lower()}
  # Limit the bundled list to these countries. Empty means all.
  countries:{ylist(answers.get('source_countries'), indent="    ")}
  # Boards added by `job-radar discover --add`.
  extra:
{extra_block}

output:
  formats: [html, json]
  dir: out

fetch:
  # Other people's servers. Keep this low.
  concurrency: {answers.get('concurrency', 4)}
  timeout: 20
  retries: 2
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


DEFAULTS = {
    "titles_include": ["engineering manager", "head of engineering", "director of engineering"],
    "titles_exclude": ["product manager", "programme manager", "account manager", "sales"],
    "countries": ["UK"],
    "remote_ok": True,
    "relocate_to": [],
    "exclude_locations": [],
    "salary_floor": None,
    "salary_currency": "GBP",
    "dealbreakers": {"coding round": COMMON_DEALBREAKERS["coding round"]},
    "sectors": [],
    "source_countries": [],
    "use_bundled": True,
    "extra_sources": [],
    "concurrency": 4,
}


def run(path: Path, non_interactive: bool = False) -> int:
    if non_interactive:
        write_config(path, DEFAULTS)
        print(f"Wrote a default config to {path}. Edit it, then run `job-radar scan`.")
        return 0

    print("\njob-radar setup\n" + "-" * 40)
    print("Seven questions. Everything is editable afterwards.\n")
    a = dict(DEFAULTS)

    # 1. titles
    print("1. What roles are you looking for?")
    print("   Not sure? Paste your CV instead and press Ctrl-D on a blank line.")
    first = _ask("   Job titles (or type 'cv' to paste)", "")
    if first.lower() == "cv":
        print("   Paste your CV, then Ctrl-D:")
        try:
            text = "".join(iter(input, "\x00"))
        except EOFError:
            text = ""
        guessed = titles_from_cv(text)
        if guessed:
            print(f"   Found these titles in your CV: {', '.join(guessed)}")
            print("   These are a starting point, not advice. Edit them.")
            a["titles_include"] = _ask_list("   Use which", guessed[:6])
        else:
            a["titles_include"] = _ask_list("   Could not read any. Job titles",
                                            DEFAULTS["titles_include"])
    elif first:
        a["titles_include"] = [x.strip() for x in first.split(",") if x.strip()]
    a["titles_exclude"] = _ask_list("   Titles to never show", DEFAULTS["titles_exclude"])

    # 2. location
    print("\n2. Where?")
    a["countries"] = _ask_list("   Country codes you live in / can work in", ["UK"])
    a["remote_ok"] = _ask_yn("   Include fully remote roles", True)
    a["relocate_to"] = _ask_list("   Countries you would relocate to", [])
    a["exclude_locations"] = _ask_list("   Places to always exclude", [])

    # 3. salary
    print("\n3. Salary")
    print("   Roles with a stated figure below this are hidden.")
    print("   Roles with no stated figure are always shown and marked.")
    floor = _ask("   Minimum acceptable (blank for none)", "")
    a["salary_floor"] = int(re.sub(r"[^\d]", "", floor)) if re.search(r"\d", floor) else None
    a["salary_currency"] = _ask("   Currency", "GBP").upper()

    # 4. dealbreakers
    print("\n4. Dealbreakers. A match in the job description hides the role.")
    chosen = {}
    for name, pat in COMMON_DEALBREAKERS.items():
        if _ask_yn(f"   Hide roles mentioning {name}", name == "coding round"):
            chosen[name] = pat
    own = _ask_list("   Anything else (plain words are fine)", [])
    for w in own:
        chosen[w] = re.escape(w)
    a["dealbreakers"] = chosen

    # 5. sectors
    print("\n5. Sectors. Blank means all of them.")
    print(f"   Options: {', '.join(SECTORS)}")
    a["sectors"] = _ask_list("   Sectors", [])

    # 6. specific companies
    print("\n6. Any companies you specifically want watched?")
    print("   Give names or careers page URLs. I will find their job board.")
    wanted = _ask_list("   Companies", [])
    if wanted:
        from .discover import discover as run_discover
        for w in wanted:
            print(f"   looking for {w}...")
            found = [f for f in run_discover(w) if f.live_jobs > 0 and f.identity != "mismatch"]
            if found:
                f = found[0]
                print(f"     found {f.platform}, {f.live_jobs} live roles")
                a["extra_sources"].append(f.to_source().to_dict())
            else:
                print("     not found. Add the careers URL later with "
                      "`job-radar discover <url> --add`.")

    # 7. politeness
    print("\n7. Fetch settings")
    a["concurrency"] = int(_ask("   Parallel requests (be kind, 4 is plenty)", "4") or 4)

    write_config(path, a)
    print(f"\nWrote {path}")
    print("Run `job-radar scan` when you are ready.")
    return 0
