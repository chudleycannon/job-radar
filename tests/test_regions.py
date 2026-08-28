"""A region named instead of a country was read as no location at all.

"Remote - Europe", "Remote, EU" and "Remote - EMEA" resolved to nothing and
were dropped as "location not recognised". Bare "Remote" is kept, so adding
the qualifier that makes a role MORE relevant to a European reader is what
made it fail. For somebody who wants remote work in the EU, that is their
best category being binned.

The obvious fix is the wrong one. Adding these to the "no location given"
list would hand a Europe-only role to a reader in Texas, which is the same
mistake pointing the other way. A region is a set of countries, so it is
resolved to one and intersected with the reader's own countries exactly as a
named country is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config              # noqa: E402
from jobradar.models import Job                 # noqa: E402
from jobradar.screen import match, regions_in   # noqa: E402


def _keep(location, countries):
    cfg = Config()
    cfg.titles_include = ["engineer"]
    cfg.countries = list(countries)
    job = Job(company="Acme", title="Engineer", url="https://x/1",
              platform="ashby", location=location,
              description="We are hiring an engineer to build things.")
    return match(job, cfg)[0]


def test_a_european_remote_role_reaches_a_european_reader():
    for loc in ("Remote - Europe", "Remote, EU", "Remote (Europe)",
                "Remote - EMEA", "Remote - EEA"):
        assert _keep(loc, ["UK"]) is True, loc
        assert _keep(loc, ["DE"]) is True, loc


def test_it_does_not_reach_a_reader_somewhere_else():
    """The half that makes this a fix rather than a hole.

    Treating "Europe" as "no location given" would have handed these to a
    reader in Texas, which is the same mistake in the other direction.
    """
    for loc in ("Remote - Europe", "Remote, EU", "Remote (Europe)"):
        assert _keep(loc, ["US"]) is False, loc
        assert _keep(loc, ["AU"]) is False, loc


def test_a_posting_naming_two_regions_reaches_readers_in_both():
    assert _keep("Remote - EMEA, NAMER", ["UK"]) is True
    assert _keep("Remote - EMEA, NAMER", ["US"]) is True
    assert _keep("Remote - EMEA, NAMER", ["JP"]) is False


def test_emea_covers_the_middle_east_and_africa_as_well_as_europe():
    assert _keep("Remote - EMEA", ["AE"]) is True
    assert _keep("Remote - EMEA", ["ZA"]) is True
    assert _keep("Remote - Europe", ["AE"]) is False


def test_apac_and_latam_resolve_too():
    assert _keep("Remote - APAC", ["SG"]) is True
    assert _keep("Remote - APAC", ["UK"]) is False
    assert _keep("Remote - LATAM", ["BR"]) is True


def test_a_region_name_is_matched_whole_and_never_as_a_substring():
    """A bare `in` test would find "eu" in Deutschland and "nam" in Vietnam,
    which is how a location filter starts inventing continents."""
    for word in ("Deutschland", "Vietnam", "Neuchatel", "Europa Park Drive"):
        assert not regions_in(word), f"{word} was read as a region"


def test_a_real_country_still_wins_and_is_not_widened():
    """A country named outright must not be broadened into its region."""
    assert _keep("Remote - Germany", ["UK"]) is False
    assert _keep("Remote - Germany", ["DE"]) is True


def test_bare_remote_is_untouched():
    assert _keep("Remote", ["UK"]) is True
    assert _keep("Remote", ["US"]) is True


def test_trivandrum_is_in_india():
    """The English name most Indian adverts still use. It resolved to nothing,
    and an unresolved location drops the role."""
    from jobradar.screen import _country_of
    assert _country_of("Trivandrum") == "IN"
    assert _country_of("Thiruvananthapuram") == "IN"
    assert _keep("Trivandrum", ["IN"]) is True
