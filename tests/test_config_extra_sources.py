"""`sources.extra` was the one config block nothing validated.

Every other block is checked, and this one was handed straight to
`Source.from_dict`. Five things were live at once:

  * `company:` typed `compny:` raised `KeyError: 'company'` out of
    `sources.load`, which is EVERY command that reads a source list. The
    traceback named no entry, no key and no file, so the only way to find it
    was to read the config and spot the typo by eye.
  * a bare `- hello` became an employer called hello with a board at the url
    hello. It can never answer, and a board that cannot answer counts in the
    run summary exactly like an employer with no vacancies.
  * `platform: not-a-real-platform` was accepted and then ignored, so the
    board was read with whichever parser its URL happened to look like.
  * `url: just some text` was accepted and fetched as written.
  * `country: Mars` was quietly rewritten to `unknown` by
    `sources.normalise_country_tag`, while the identical word under
    `locations.countries` is refused outright. Two blocks disagreeing about
    the same mistake, and the silent one is the one that loses roles.

The country is normalised here as well as refused, so `country: Germany`
reaches `sources.load` as DE instead of being turned into `unknown` on the way
past, which would have dropped the board from `sources.countries: [DE]`.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import config  # noqa: E402
from jobradar.models import Source  # noqa: E402

HEAD = ("titles:\n  include: [engineering manager]\n"
        "sources:\n  use_bundled: false\n  extra:\n")


def _load(entries: str):
    p = Path(tempfile.mkdtemp()) / "config.yaml"
    p.write_text(HEAD + entries, encoding="utf-8")
    return config.load(p)


def _refused(entries: str) -> str:
    try:
        _load(entries)
    except config.ConfigError as e:
        return str(e)
    except Exception as e:
        raise AssertionError(
            f"raised {type(e).__name__} rather than ConfigError: {e}")
    raise AssertionError("accepted an entry that cannot work")


def test_a_typod_key_names_the_key_and_the_entry():
    why = _refused("    - compny: Acme\n      url: https://acme.example/jobs\n")
    assert "compny" in why, why
    assert "sources.extra" in why, why


def test_a_missing_company_is_refused_here_rather_than_as_a_keyerror_later():
    why = _refused("    - url: https://acme.example/jobs\n")
    assert "company" in why, why


def test_a_bare_word_does_not_become_an_employer():
    why = _refused("    - hello\n")
    assert "hello" in why, why
    assert "url" in why.lower(), why


def test_a_bare_url_is_still_accepted_because_sources_load_supports_it():
    cfg = _load("    - https://boards.greenhouse.io/one\n")
    assert cfg.extra_sources == [{"company": "https://boards.greenhouse.io/one",
                                  "url": "https://boards.greenhouse.io/one"}]


def test_a_url_that_is_not_a_url_is_refused():
    why = _refused("    - company: Acme\n      url: just some text\n")
    assert "Acme" in why and "url" in why, why


def test_a_platform_with_no_adapter_is_refused_and_the_real_ones_are_listed():
    why = _refused("    - company: Acme\n      url: https://acme.example/jobs\n"
                   "      platform: not-a-real-platform\n")
    assert "not-a-real-platform" in why, why
    assert "greenhouse" in why and "workday" in why, why


def test_a_platform_that_does_exist_is_kept():
    cfg = _load("    - company: Seamflow\n"
                "      url: https://api.ashbyhq.com/posting-api/job-board/x\n"
                "      platform: ashby\n")
    assert cfg.extra_sources[0]["platform"] == "ashby"


def test_a_country_that_is_not_one_is_refused_here_too():
    """`locations.countries: [Mars]` errors. This silently wrote `unknown`."""
    why = _refused("    - company: Acme\n      url: https://acme.example/jobs\n"
                   "      country: Mars\n")
    assert "Mars" in why, why


def test_a_country_name_is_normalised_rather_than_thrown_away():
    cfg = _load("    - company: Acme\n      url: https://acme.example/jobs\n"
                "      country: Germany\n")
    assert cfg.extra_sources[0]["country"] == "DE", cfg.extra_sources


def test_the_tags_that_mean_we_cannot_say_are_kept_as_themselves():
    """`multi` and `unknown` are not countries and must not be refused as
    though they were: a multinational board is kept by every country filter,
    which is the behaviour `sources.load` relies on."""
    for written, want in (("multi", "multi"), ("multiple", "multi"),
                          ("global", "multi"), ("unknown", "unknown")):
        cfg = _load(f"    - company: Acme\n      url: https://a.example/j\n"
                    f"      country: {written}\n")
        assert cfg.extra_sources[0]["country"] == want, cfg.extra_sources


def test_a_sector_tag_that_is_not_one_is_refused():
    """The same shape `sectors:` already guards against, from the other side.
    A board tagged with a word that is not in the bundled list is dropped by
    every `sectors:` filter and kept by none of them, so the tag can only ever
    lose the employer you went to the trouble of adding."""
    why = _refused("    - company: Acme\n      url: https://acme.example/jobs\n"
                   "      sector: myown\n")
    assert "myown" in why, why


def test_a_sector_tag_that_is_one_is_kept():
    cfg = _load("    - company: Acme\n      url: https://acme.example/jobs\n"
                "      sector: technology\n")
    assert cfg.extra_sources[0]["sector"] == "technology"


def test_a_keyword_template_with_no_placeholder_is_refused():
    """`expand_templates` fills `{keyword}` in and makes one search per title.
    With no placeholder it makes the same request up to twelve times and
    de-duplicates them back to one afterwards."""
    why = _refused("    - company: Acme\n      url: https://acme.example/jobs\n"
                   "      keyword_template: true\n")
    assert "keyword" in why, why


def test_a_keyword_template_with_a_placeholder_is_fine():
    cfg = _load("    - company: Search\n"
                "      url: https://acme.example/s?q={keyword}\n"
                "      keyword_template: true\n")
    assert cfg.extra_sources[0]["keyword_template"] is True


def test_the_shapes_the_tool_writes_itself_are_all_accepted():
    """`job-radar discover --add` writes `Source.to_dict()` into this block and
    the setup wizard writes company, url and platform. A validator that
    refuses what the tool's own commands produce is a validator that breaks
    `--add`."""
    written = Source(company="Nandos", url="https://x.example/jobs",
                     platform="workday", sector="hospitality", country="UK",
                     domain="nandos.com").to_dict()
    got = config._extra_sources([written], "sources.extra")
    assert got[0]["company"] == "Nandos" and got[0]["country"] == "UK"

    plain = Source(company="Acme", url="https://acme.example/jobs",
                   platform="greenhouse").to_dict()
    assert config._extra_sources([plain], "sources.extra")[0]["platform"] == "greenhouse"


def test_an_empty_block_is_not_an_error():
    for text in ("  extra: []\n", "  extra:\n"):
        p = Path(tempfile.mkdtemp()) / "config.yaml"
        p.write_text("titles:\n  include: [engineering manager]\n"
                     "sources:\n  use_bundled: false\n" + text,
                     encoding="utf-8")
        assert config.load(p).extra_sources == []
