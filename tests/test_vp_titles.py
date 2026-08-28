"""An abbreviation worked in one half of the title matcher and not the other.

There are two paths. A regex built from every spelling of the configured
term, which tolerates filler words, and a loose matcher that allows the words
to be reordered or interrupted. The regex was handed the EXPANDED spellings
and the loose matcher was handed the RAW configured terms, so an abbreviation
only ever worked where the regex could reach.

    titles.include: [vp engineering]

    VP of Engineering                             matched
    Vice President, Engineering - Authentication  MISSED
    Vice President of Engineering                 MISSED

That second spelling is what 22 of 165 leadership postings in one sample
used, and `vp` was not in the abbreviation table at all, so neither path
could have expanded it even if both had tried.

Two smaller faults fixed alongside. The table was applied with a bare `in`,
so it replaced inside other words: "ar" sits in "marketing", and "marketing
manager" expanded to "maccounts receivableketing manager"; "pm" in
"shipment" gave "shiproject managerent coordinator". Nothing matched those,
which is the only reason nobody noticed, and they were junk in the compiled
pattern. They are matched on word boundaries now.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config  # noqa: E402
from jobradar.models import Job     # noqa: E402
from jobradar.screen import match   # noqa: E402


def _matches(title, terms=("vp engineering",)):
    cfg = Config()
    cfg.titles_include = list(terms)
    job = Job(company="Acme", title=title, url="https://x/1", platform="ashby",
              location="London", description="We are hiring.")
    return match(job, cfg)[0]


def test_every_way_an_employer_writes_a_vp_title_matches():
    for title in ("VP of Engineering",
                  "VP Engineering",
                  "Vice President, Engineering - Authentication",
                  "Vice President of Engineering",
                  "Senior Vice President Engineering",
                  "Engineering Vice President"):
        assert _matches(title), title


def test_a_different_department_still_does_not_match():
    """The half that makes this a fix rather than a widening."""
    for title in ("VP Marketing", "Vice President, Finance",
                  "Vice President of People"):
        assert not _matches(title), title


def test_the_long_form_in_the_config_finds_the_short_form_in_the_advert():
    """Both directions, since somebody may type either."""
    assert _matches("VP Engineering", terms=("vice president engineering",))
    assert _matches("VP of Engineering", terms=("vice president engineering",))


def test_an_abbreviation_is_not_expanded_inside_another_word():
    """"ar" is inside "marketing". A bare `in` test replaced it there and put
    "maccounts receivableketing manager" into the compiled pattern."""
    for term, junk in (("marketing manager", "receivable"),
                       ("shipment coordinator", "project manager"),
                       ("apprentice engineer", "accounts payable")):
        variants = Config._title_variants(term)
        assert not any(junk in v for v in variants), (term, variants)


def test_the_ordinary_titles_still_expand_both_ways():
    v = Config._title_variants("hr manager")
    assert "human resources manager" in v and "hr manager" in v


def test_both_halves_of_the_matcher_see_the_same_spellings():
    """The actual bug: one path had the expansions and the other did not."""
    cfg = Config()
    cfg.titles_include = ["vp engineering"]
    expanded = set(cfg.title_terms_expanded())
    assert "vice president engineering" in expanded
    assert "vp engineering" in expanded
