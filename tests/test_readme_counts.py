"""Numbers in the prose, checked against the thing they describe.

This repository has shipped `17,625`, `17,826`, `17,828`, `13 ATS APIs`,
`25 platforms` and `395 tests` in its own documentation while not one of them
was true, and four more wrong counts were found in the README on top of that.
Every one arrived the same way: a number that was right when it was typed, in
prose nothing recounts, next to data that grows every Sunday.

So the counts are derived here from the file and the registry, and the docs
are required to agree with them. A number that rots now fails the suite on the
run after it rots, which is the only mechanism that has ever worked on this.

The rule for adding to this: derive it, never copy it. A test that hardcodes
the same wrong number as the README passes and proves nothing.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters
from jobradar import sources as src_mod

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
CONFIG_MD = (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8")
SOURCES_MD = (ROOT / "docs" / "SOURCES.md").read_text(encoding="utf-8")
PLATFORMS_MD = (ROOT / "docs" / "PLATFORMS.md").read_text(encoding="utf-8")
DOCS = {"README.md": README, "docs/CONFIG.md": CONFIG_MD,
        "docs/SOURCES.md": SOURCES_MD, "docs/PLATFORMS.md": PLATFORMS_MD}

SRCS = src_mod.load_file(src_mod.BUNDLED)


def _n(x: int) -> str:
    return f"{x:,}"


def test_the_total_size_of_the_bundled_list_is_stated_correctly_everywhere():
    """`docs/CONFIG.md` and `docs/SOURCES.md` both said 17,810 against a file
    holding 17,811, and the README said 17,811 in the same week. Three numbers
    for one `len()`."""
    total = _n(len(SRCS))
    for name, text in DOCS.items():
        stated = set(re.findall(r"\b17,\d{3}\b", text))
        assert stated <= {total, _n(_boards())}, (
            f"{name} states {sorted(stated - {total, _n(_boards())})}, but the "
            f"list holds {total} entries of which {_n(_boards())} are boards")


def _boards() -> int:
    """Employer boards, counted the way `sources.save` counts them.

    Not "everything that is not a keyword template": a cross-employer sweep is
    neither a template nor a board, and counting it as one is what put a header
    saying 17,807 on a file whose own arithmetic said 17,808.
    """
    from jobradar.screen import directness
    return sum(1 for s in SRCS
               if not s.keyword_template and directness(s.platform) >= 2)


def test_the_readme_sector_split_matches_the_tags_in_the_file():
    """The tagged/untagged split is the number that decides whether a
    `sectors:` filter is worth setting, and it was one out in two files."""
    tagged = sum(1 for s in SRCS if s.sector)
    untagged = len(SRCS) - tagged
    for name in ("README.md", "docs/CONFIG.md", "docs/SOURCES.md"):
        text = DOCS[name]
        if "sector tag" not in text and "carry a tag" not in text:
            continue
        assert _n(tagged) in text, f"{name} does not state the {tagged} tagged sources"
        assert _n(untagged) in text, (
            f"{name} does not state the {untagged} untagged ones")


def test_the_sector_table_in_the_config_reference_counts_the_real_tags():
    """A table of eighteen counts is exactly the shape that goes stale one row
    at a time, and `untagged` had."""
    counts = collections.Counter(s.sector or "untagged" for s in SRCS)
    for tag, n in counts.items():
        row = re.search(r"^\| `" + re.escape(tag) + r"` \| ([\d,]+) \|$",
                        CONFIG_MD, re.M)
        assert row, f"docs/CONFIG.md has no row for the `{tag}` tag"
        assert row.group(1) == _n(n), (
            f"docs/CONFIG.md says {tag} is {row.group(1)}, the file says {_n(n)}")


def test_the_country_filter_is_documented_as_keeping_untagged_and_multi():
    """`sources.countries: [UK]` leaves 7,745 sources, not the 934 tagged UK,
    because untagged boards are kept and, since today, multi-tagged ones are
    too. The README explained exactly this rule for `sectors:` and said
    nothing at all about `sources.countries`, so the setting read as a
    country filter and is not one."""
    untagged = sum(1 for s in SRCS if not s.country)
    multi = sum(1 for s in SRCS
                if s.country.lower() in src_mod.NON_COUNTRY_TAGS)
    uk = sum(1 for s in SRCS if s.country.upper() == "UK")
    kept = sum(1 for s in SRCS
               if not s.country
               or s.country.lower() in src_mod.NON_COUNTRY_TAGS
               or s.country.upper() == "UK")
    assert kept == untagged + multi + uk       # the rule, stated as arithmetic

    for name in ("README.md", "docs/CONFIG.md"):
        text = DOCS[name]
        assert "sources.countries" in text or "| `countries` |" in text
        for label, value in (("untagged", untagged), ("multi-tagged", multi),
                             ("UK-tagged", uk), ("kept", kept)):
            assert _n(value) in text, (
                f"{name} does not state the {label} count, {_n(value)}")
        assert "multi" in text, f"{name} does not mention the `multi` tag"


def test_the_adapter_count_is_the_length_of_the_registry():
    """The README said 29 and `docs/PLATFORMS.md` said 27 about the same
    `REGISTRY`, on the same day."""
    n = str(len(adapters.REGISTRY))
    for name in ("README.md", "docs/PLATFORMS.md"):
        assert f"{n} adapters" in DOCS[name], (
            f"{name} does not say the code carries {n} adapters")


def test_the_platform_notes_count_their_own_rows():
    """"Twenty-three board platforms" heading a table nothing recounts."""
    rows = [l for l in PLATFORMS_MD.splitlines()
            if l.startswith("| **") and not l.startswith("| **Greenhouse 5.0")]
    assert len(rows) == 23, (
        f"the platform table holds {len(rows)} rows; the prose around it, and "
        f"the README's link to it, both say twenty-three")
    assert "Twenty-three board platforms" in PLATFORMS_MD
    assert "twenty-three platforms" in README


def test_the_platform_notes_admit_the_platforms_they_do_not_cover():
    """The file claimed the bundled list used 21 of its rows. It uses 19, and
    the four it uses with no row here include the third largest platform on
    the list. Saying "21" turned a coverage gap into an invisible one."""
    documented = set(re.findall(r"\*\*Workable's own boards\*\*|\*\*Personio\*\*",
                                PLATFORMS_MD))
    assert documented, "the gap paragraph naming the uncovered platforms is gone"
    used = collections.Counter(s.platform for s in SRCS)
    for platform in ("workable", "personio", "recruitee"):
        assert _n(used[platform]) in PLATFORMS_MD, (
            f"{platform} holds {used[platform]} sources and has no row; the "
            f"count has to be stated or the gap is invisible")


def test_the_discover_example_is_the_url_the_adapter_builds_today():
    """The README's worked example showed
    `.../job-board/primer.io` with no query string, and the Ashby adapter has
    appended `?includeCompensation=true` since compensation was added. The
    example was a transcript of a run nobody had repeated, and the job count
    beside it had moved too."""
    ashby = adapters.by_name("ashby")
    url = ashby.build("primer.io")
    assert "includeCompensation" in url, "the adapter changed, not the README"
    shown = re.search(r"https://api\.ashbyhq\.com/posting-api/job-board/\S+",
                      README)
    assert shown, "the discover example lost its URL"
    assert shown.group(0) == url, (
        f"README shows {shown.group(0)}, `discover` produces {url}")


def test_the_discover_example_does_not_promise_a_job_count_that_will_move():
    """The count was 39 in the README and 32 on the day it was checked, which
    is a live board doing what live boards do rather than a bug. It stays in
    the example because a real transcript is worth more than a redacted one,
    so the prose has to say it is a snapshot."""
    block = README[README.index("$ job-radar discover"):]
    block = block[:block.index("`[verified]`")]
    block = " ".join(block.split())      # the caveat wraps across two lines
    assert re.search(r"will not be \d+ when you try it", block), (
        "the example quotes a live job count with nothing saying it moves")


def test_work_modes_is_documented_where_somebody_would_go_looking():
    """`locations.work_modes` shipped with a config-reference row and no
    mention in the README at all, so the only place it existed for a reader
    was a comment in `config.example.yaml`."""
    from jobradar.config import VALID_WORK_MODES
    for name in ("README.md", "docs/CONFIG.md"):
        text = DOCS[name]
        assert "work_modes" in text, f"{name} never mentions work_modes"
        for mode in VALID_WORK_MODES:
            assert f"`{mode}`" in text or f"[{mode}]" in text, (
                f"{name} does not name the `{mode}` arrangement")
        assert "unstated" in text or "states no arrangement" in text, (
            f"{name} does not say what happens to a posting that states "
            f"no arrangement, which is half of them")
