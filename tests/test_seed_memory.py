"""`seed load` built the whole shard set in memory before screening it.

A 22,701-role import held 325MB resident, about 14KB a role, so a US reader's
172,381 rows would have been around 2.1GB. That is the difference between
importing a seed and swapping, on the largest shard and therefore the most
likely reader.

The title gate throws away more than 99% of postings, so it runs as each row
is read and only survivors are kept. Measured after: 72MB for a UK reader's
41,038 rows and 155MB for a US reader's 172,381, with the same matches.

Only the title, deliberately. `screen.run` opens with `dedupe` across the
whole set, so filtering on anything that varies between duplicates of one
posting would change which copy survives. A duplicate of a title-matching
role matches the same title, so every one of them still reaches `dedupe`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config      # noqa: E402
from jobradar.models import Job         # noqa: E402
from jobradar.screen import match, title_gate  # noqa: E402


def _cfg(include=("engineering manager",), exclude=()):
    c = Config()
    c.titles_include = list(include)
    c.titles_exclude = list(exclude)
    return c


def _job(title):
    return Job(company="Acme", title=title, url="https://x/1", platform="ashby",
               location="London", description="d" * 300)


def test_the_gate_agrees_with_match_on_the_title():
    """It must be the same rule, or the optimisation changes the answer."""
    cfg = _cfg()
    gate = title_gate(cfg)
    titles = ["Engineering Manager", "Senior Engineering Manager",
              "Head of Engineering", "Manager, Engineering",
              "Product Designer", "Chef", "", "Engineering Manager (Remote)"]
    for t in titles:
        by_match = match(_job(t), cfg)[1] != "title does not match"
        assert gate(t) is by_match, t


def test_an_excluded_title_is_dropped_by_both():
    cfg = _cfg(exclude=("principal",))
    gate = title_gate(cfg)
    assert gate("Engineering Manager") is True
    assert gate("Principal Engineering Manager") is False


def test_the_abbreviation_expansion_reaches_the_gate():
    """It reads `title_terms_expanded`, not the raw configured terms, which
    is the bug the loose matcher had."""
    gate = title_gate(_cfg(include=("vp engineering",)))
    assert gate("Vice President, Engineering") is True
    assert gate("VP of Engineering") is True
    assert gate("VP Marketing") is False


def test_a_config_with_no_titles_keeps_everything():
    gate = title_gate(_cfg(include=()))
    assert gate("Anything At All") is True


def test_the_loader_filters_as_it_reads_rather_than_materialising_first():
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_seed_load)
    assert "for j in seed_mod.load(src, countries):" in src, \
        "seed load is building the whole shard set in memory again"
    assert "list(seed_mod.load(" not in src


def test_the_count_reported_is_what_was_read_not_what_survived():
    """"41,038 roles read" has to keep meaning read. Reporting the survivors
    there would quietly turn a 41,038-row shard into a 267-row one."""
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_seed_load)
    assert 'f"{read:,} roles read' in src
