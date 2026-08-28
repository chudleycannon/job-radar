"""YAML reads an unquoted NO as false, and Norway went missing.

PyYAML speaks YAML 1.1, where `NO`, `OFF` and `FALSE` are all the boolean
false, and `ON`, `YES` and `TRUE` are true. A bare `Y` is not, as it happens,
which is why the test below names the three that are. So

    locations:
      countries: [NO]

arrived as `[False]`, and a Norwegian typing the correct ISO code was told
"'False' is not a country this tool recognises. Did you mean FI, FR?". Norway
has 323 roles in the published seed and there was no way to ask for them.

Turned back rather than refused, because among ISO country codes the mapping
is not ambiguous: NO is the only one YAML eats. ON, OFF and YES are not
countries, so a true has no country it could have been, and that is worth
saying with the fix rather than guessing.
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import ConfigError, _countries  # noqa: E402


def _load(fragment):
    return yaml.safe_load(f"countries: {fragment}")["countries"]


def test_yaml_really_does_eat_norway():
    """If PyYAML ever stops doing this, the workaround can go."""
    assert _load("[NO]") == [False]


def test_an_unquoted_norway_still_means_norway():
    assert _countries(_load("[NO]"), "locations.countries") == ["NO"]
    assert _countries(_load("[no]"), "locations.countries") == ["NO"]


def test_it_survives_in_a_list_with_real_codes():
    assert _countries(_load("[NO, SE, DK]"), "locations.countries") == \
        ["NO", "SE", "DK"]


def test_quoting_it_works_too():
    assert _countries(_load('["NO"]'), "locations.countries") == ["NO"]


def test_a_boolean_true_is_refused_with_the_fix_in_the_message():
    """ON, YES and Y are not countries, so there is nothing to turn back
    into. Guessing would be inventing a country the reader never typed."""
    for fragment in ("[ON]", "[YES]", "[TRUE]"):
        try:
            _countries(_load(fragment), "locations.countries")
        except ConfigError as exc:
            assert "Quote it" in str(exc), str(exc)
        else:
            raise AssertionError(f"{fragment} was accepted as a country")


def test_every_country_list_in_the_config_gets_this():
    """`relocate_to`, `need_sponsorship` and `sources.countries` all run
    through the same function, so Norway works in all of them or none."""
    import inspect
    from jobradar import config
    src = inspect.getsource(config)
    for key in ("relocate_to", "need_sponsorship"):
        assert f'_countries(_as_list(loc.get("{key}"))' in src, key
