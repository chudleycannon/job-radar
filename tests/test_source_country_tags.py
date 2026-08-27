"""A tag meaning "we cannot say" was read as "no".

`sources.countries: [NL]` kept a board only if it was untagged or tagged
exactly NL. A board tagged `multi` is a multinational, and that tag is the
absence of a single answer, not evidence the employer has nothing in the
Netherlands. So opting in to "watch boards in my country" dropped all 1,599
multi-tagged boards -- 17,817 sources down to 5,374 -- and those are exactly
the employers most likely to have a Dutch vacancy. Nothing said a word.

`NON_COUNTRY_TAGS` already existed in this module for precisely this idea and
was not consulted here. It is the same rule as the seed's `unplaced` and
`multiple` shards, arrived at separately and for the same reason.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import sources as S              # noqa: E402
from jobradar.config import Config             # noqa: E402


def _cfg(countries):
    c = Config()
    c.titles_include = ["engineer"]
    c.use_bundled_sources = False
    c.source_countries = list(countries)
    return c


def _srcs(*tags):
    out = []
    for i, tag in enumerate(tags):
        out.append(S.Source.from_dict(
            {"company": f"Co{i}", "url": f"https://boards-api.greenhouse.io/"
                                        f"v1/boards/co{i}/jobs",
             "platform": "greenhouse", "country": tag}))
    return out


def _kept(tags, want):
    cfg = _cfg(want)
    cfg.extra_sources = [
        {"company": f"Co{i}", "platform": "greenhouse", "country": t,
         "url": f"https://boards-api.greenhouse.io/v1/boards/co{i}/jobs"}
        for i, t in enumerate(tags)]
    return {s.company for s in S.load(cfg)}


def test_a_multinational_board_is_kept_for_any_country():
    kept = _kept(["NL", "multi", "US", ""], ["NL"])
    assert "Co1" in kept, "a multi-tagged board was dropped by a country filter"


def test_an_untagged_board_is_still_kept():
    assert "Co3" in _kept(["NL", "multi", "US", ""], ["NL"])


def test_a_board_tagged_for_somewhere_else_is_still_dropped():
    """The filter has to keep doing its job, or this is not a fix."""
    assert "Co2" not in _kept(["NL", "multi", "US", ""], ["NL"])


def test_the_matching_country_is_obviously_kept():
    assert "Co0" in _kept(["NL", "multi", "US", ""], ["NL"])


def test_a_board_tagged_unknown_is_kept_too():
    """"unknown" is the other tag that means nobody could say."""
    assert "Co1" in _kept(["NL", "unknown", "US"], ["NL"])


def test_no_country_filter_keeps_everything():
    assert _kept(["NL", "multi", "US", ""], []) == {"Co0", "Co1", "Co2", "Co3"}


def test_the_non_country_tags_are_the_ones_this_relies_on():
    assert "multi" in {t.lower() for t in S.NON_COUNTRY_TAGS}
    assert "unknown" in {t.lower() for t in S.NON_COUNTRY_TAGS}
