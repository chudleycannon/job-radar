"""Two ways the tool hid roles it had already found.

The config accepted 40 country codes, read from the table of country NAMES
the location logic recognises in text. But a role is also placed by its city,
and the city tables know 178 more between them, so `enrich` routinely
returned a country the config would then refuse.

The published seed made it countable: 162 country shards, of which 122 no
config could ask for, holding 20,752 roles. Greece has 3,396 and
`countries: [GR]` was an error message. Saudi Arabia 2,225, Colombia 1,821.

And `city_of` stripped "office" and its friends off the FRONT of a location,
which is where British boards put them, and left them on the END, which is
where American ones do. So "San Francisco Office", "New York Office", "NYC
Office" and "SF Office" stood in the dashboard's city filter as four separate
towns beside the plain "San Francisco" and "New York" they are the same place
as: 252, 207, 100 and 55 roles filed away from their own city on the
published US shard.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import ConfigError, _countries, _known_country_codes  # noqa: E402
from jobradar.screen import city_of  # noqa: E402


def test_a_country_the_placer_can_return_is_a_country_the_config_accepts():
    """The actual rule. If `enrich` can decide a role is in Greece, a reader
    must be able to ask for Greece."""
    for code in ("GR", "SA", "CO", "EG", "TW", "BG", "UA", "PK", "NO", "IS"):
        assert code in _known_country_codes(), code
        assert _countries([code], "locations.countries") == [code]


def test_the_config_still_refuses_something_that_is_not_a_country():
    for junk in ("ZZ", "QQ", "XX"):
        try:
            _countries([junk], "locations.countries")
        except ConfigError as exc:
            assert "not a country" in str(exc)
        else:
            raise AssertionError(f"{junk} was accepted")


def test_every_shard_in_the_published_index_can_be_asked_for():
    """Against the real index when it is on this machine. A shard nobody can
    request is a download nobody can use."""
    idx = Path(__file__).resolve().parent.parent / "seed-build" / "index.json"
    if not idx.exists():
        return
    shards = json.loads(idx.read_text(encoding="utf-8"))["shards"]
    known = _known_country_codes()
    stranded = sorted(k for k in shards
                      if k not in ("unplaced", "multiple") and k not in known)
    assert not stranded, f"published but unreachable: {stranded}"


def test_an_office_on_the_end_is_stripped_like_one_on_the_front():
    for loc, want in (("San Francisco Office", "San Francisco"),
                      ("New York Office", "New York"),
                      ("NYC Office", "NYC"),
                      ("SF Office", "SF"),
                      ("New York office", "New York"),
                      ("Austin HQ", "Austin"),
                      ("HQ - NYC", "NYC")):
        assert city_of(loc) == want, (loc, city_of(loc))


def test_a_country_pinned_to_the_front_of_its_own_city_is_removed():
    for loc, want in (("USA - Corona", "Corona"),
                      ("USA - New York", "New York"),
                      ("UK - Manchester", "Manchester")):
        assert city_of(loc) == want, (loc, city_of(loc))


def test_a_real_place_whose_name_contains_the_word_survives():
    """The over-strip direction. "Officeworks Plaza" is not an office."""
    for loc in ("Officeworks Plaza", "Office Manager House", "Corona",
                "San Francisco", "London"):
        assert city_of(loc) == loc, loc


def test_a_location_that_is_only_an_arrangement_is_still_not_a_city():
    for loc in ("US - Remote", "Remote", "Anywhere in the US"):
        assert city_of(loc) == "", loc
