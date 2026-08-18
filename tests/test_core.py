"""Tests for the parts that are easy to get quietly wrong."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config, Dealbreaker
from jobradar.models import Job, Salary, Source
from jobradar.salary import clears_floor, from_ashby, from_greenhouse, parse_text
from jobradar.screen import dedupe, match, run as screen_run


def _cfg(**kw) -> Config:
    base = dict(
        titles_include=["engineering manager"],
        titles_exclude=["product manager"],
        countries=["UK"],
        relocate_to=["US"],
        salary_floor=100000,
        salary_currency="GBP",
    )
    base.update(kw)
    return Config(**base)


# ----------------------------------------------------------------- salary
def test_parses_ranges_and_k_notation():
    s = parse_text("£120,000 - £150,000 per annum")
    assert s.confirmed and s.min == 120000 and s.max == 150000 and s.currency == "GBP"
    s = parse_text("$189.6K – $290.4K • Offers Equity")
    assert s.confirmed and s.min == 189600 and s.max == 290400


def test_competitive_is_not_a_salary():
    assert not parse_text("Competitive salary and great benefits").confirmed
    assert not parse_text("Salary: DOE").confirmed
    assert not parse_text("").confirmed


def test_day_rate_is_annualised_before_comparison():
    """A day rate must not be compared to an annual floor as a bare number.

    Without annualising, "£600 per day" reads as 600 and is dropped by any
    sane floor. With it, £600/day is £132,000 a year, which genuinely is below
    a £140k floor, and £700/day is £154,000, which clears £100k.
    """
    s = parse_text("£600 per day")
    assert s.period == "day" and s.max == 600
    assert s.annualised() == 132000
    assert clears_floor(s, 140000, "GBP")[0] is False

    better = parse_text("£700 per day")
    assert better.annualised() == 154000
    assert clears_floor(better, 100000, "GBP")[0] is True

    # An hourly rate goes through the same conversion.
    hourly = parse_text("$95 per hour")
    assert hourly.period == "hour" and hourly.annualised() == 95 * 220 * 8


def test_ashby_and_greenhouse_shapes():
    a = from_ashby({"scrapeableCompensationSalarySummary": "$189.6K - $290.4K"})
    assert a.confirmed and a.max == 290400
    g = from_greenhouse([{"min_cents": 12000000, "max_cents": 15000000,
                          "currency_type": "GBP"}])
    assert g.confirmed and g.min == 120000 and g.max == 150000


def test_the_salary_rule():
    """Stated and too low is dropped. Unstated is always kept."""
    low = Salary(min=80000, max=90000, currency="GBP", confirmed=True, raw="£80k-£90k")
    assert clears_floor(low, 100000, "GBP")[0] is False

    # Top of the band clears, so it survives.
    spanning = Salary(min=100000, max=150000, currency="GBP", confirmed=True)
    assert clears_floor(spanning, 100000, "GBP")[0] is True

    assert clears_floor(Salary(), 100000, "GBP")[0] is True

    # Currencies are not silently converted.
    usd = Salary(min=90000, max=95000, currency="USD", confirmed=True)
    keep, why = clears_floor(usd, 100000, "GBP")
    assert keep is True and "not compared" in why


# ----------------------------------------------------------------- location
def _job(title="Engineering Manager", location="London", **kw) -> Job:
    return Job(company="Acme", title=title, url="https://x/1", platform="test",
               location=location, **kw)


def test_remote_still_respects_the_country():
    """'Remote' alone means unspecified. 'Remote - US' names a country, and a
    US-remote role is not open to someone in the UK."""
    cfg = _cfg(relocate_to=[])
    assert match(_job(location="Remote"), cfg)[0] is True
    assert match(_job(location="Remote - US", remote=True), cfg)[0] is False
    assert match(_job(location="Remote - UK", remote=True), cfg)[0] is True
    # This is the bug that let Hong Kong roles through: remote=True was
    # treated as location-agnostic.
    assert match(_job(location="Hong Kong", remote=True), cfg)[0] is False


def test_multi_location_postings_survive_on_one_good_location():
    cfg = _cfg()
    ok, _ = match(_job(location="Hong Kong / United Kingdom / Singapore"), cfg)
    assert ok is True


def test_title_gate():
    cfg = _cfg()
    assert match(_job(title="Product Manager"), cfg)[0] is False
    assert match(_job(title="Senior Data Analyst"), cfg)[0] is False


# ----------------------------------------------------------------- dedupe
def test_same_role_in_many_locations_collapses():
    jobs = [_job(location=c) for c in ("London", "Berlin", "Paris", "Tokyo")]
    for i, j in enumerate(jobs):
        j.url = f"https://x/{i}"
    out = dedupe(jobs, _cfg())
    assert len(out) == 1
    assert "posted in 4 locations" in out[0].flags
    # The location you could actually take is shown first.
    assert out[0].location.startswith("London")


# ----------------------------------------------------------------- pipeline
def test_end_to_end_screen():
    cfg = _cfg(dealbreakers=[Dealbreaker("coding round", r"take.?home|live coding")])
    jobs = [
        _job(title="Engineering Manager", location="London",
             description="You will lead a team."),
        _job(title="Engineering Manager", location="London",
             description="There is a take home exercise."),
        _job(title="Engineering Manager", location="Paris"),
        _job(title="Product Manager", location="London"),
    ]
    for i, j in enumerate(jobs):
        j.url = f"https://x/{i}"
        j.title = f"{j.title} {i}"   # keep dedupe out of it
    kept, dropped = screen_run(jobs, cfg)
    assert len(kept) == 1
    assert "dealbreaker: coding round" in dropped
    assert kept[0].score > 0 and kept[0].reasons


def test_uid_is_stable_across_query_strings():
    a = _job(); a.url = "https://x/jobs/1?utm_source=feed"
    b = _job(); b.url = "https://x/jobs/1"
    assert a.uid == b.uid


def test_source_key_keeps_the_query_string():
    """LinkedIn sources differ only by query; stripping it collapsed six
    distinct searches into one."""
    a = Source(company="li", url="https://x/api?keywords=a", platform="linkedin")
    b = Source(company="li", url="https://x/api?keywords=b", platform="linkedin")
    assert a.key != b.key


def test_uk_postcodes_count_as_uk():
    """Employers hiring nationally list towns, not cities. NHS Jobs gives
    "Dorchester DT1 2JY"; a city-only regex read every such role as an
    unrecognised country and dropped it."""
    from jobradar.screen import _country_of
    assert _country_of("Dorchester DT1 2JY") == "UK"
    assert _country_of("Coventry CV2 2DX") == "UK"
    assert _country_of("Ipswich IP4 5SW") == "UK"
    # and must not swallow other countries' formats
    assert _country_of("Austin, TX 78701") == "US"
    assert _country_of("Hong Kong") == "HK"



def test_wizard_config_is_valid_yaml_with_regex_dealbreakers():
    """The default config the wizard writes must actually load.

    Dealbreakers are regexes. YAML processes backslash escapes inside
    double-quoted scalars, so a pattern containing \\w was a parse error the
    moment the file was read back: `setup --defaults` then `scan` crashed for
    every new user. Single-quoted YAML takes the string literally.
    """
    import tempfile, yaml
    from jobradar.setup_wizard import write_config, DEFAULTS, COMMON_DEALBREAKERS
    from jobradar.config import load as load_cfg

    answers = dict(DEFAULTS)
    answers["dealbreakers"] = dict(COMMON_DEALBREAKERS)   # every pattern, \w and all
    d = Path(tempfile.mkdtemp()) / "config.yaml"
    write_config(d, answers)

    raw = yaml.safe_load(d.read_text())
    assert len(raw["dealbreakers"]) == len(COMMON_DEALBREAKERS)
    cfg = load_cfg(d)
    # and the patterns must still compile after the round trip
    for db in cfg.dealbreakers:
        db.compiled()
    assert len(cfg.dealbreakers) == len(COMMON_DEALBREAKERS)



def test_ambiguous_city_names_resolve_to_the_right_country():
    """City names are not unique across countries, and getting this wrong
    marked 59 of 296 American roles as British.

    "New York City" contains "york". There is a Cambridge in Massachusetts, a
    Birmingham in Alabama, a Manchester in New Hampshire, a Reading in
    Pennsylvania and a Newcastle in Australia. An explicit country name beats
    a US state code beats a city name.
    """
    from jobradar.screen import _country_of, _countries_in
    # the report that started it
    assert _country_of("San Francisco, CA | New York City, NY") == "US"
    assert _country_of("New York, New York, USA") == "US"
    # US cities that collide with UK ones
    for loc in ("Cambridge, MA", "Birmingham, AL", "Manchester, NH",
                "Reading, PA", "Bath, ME", "Boston, MA"):
        assert _country_of(loc) == "US", loc
    # explicit country wins over a colliding city name
    assert _country_of("Newcastle, Australia") == "AU"
    assert _country_of("Paris, TX") == "US"
    # and the genuine UK cases still work
    for loc in ("London", "York, England", "Manchester, UK", "Dorchester DT1 2JY"):
        assert _country_of(loc) == "UK", loc


def test_commas_bind_a_place_to_its_qualifier():
    """Splitting on commas severed "Cambridge, MA" from the state code that
    identifies it, so the fragment "Cambridge" read as UK. Only a pipe or a
    slash separates genuinely distinct locations."""
    from jobradar.screen import _countries_in
    assert _countries_in("Cambridge, MA") == {"US"}
    assert _countries_in("San Francisco, CA | New York City, NY") == {"US"}
    assert _countries_in("Boston, MA | London") == {"UK", "US"}
    assert _countries_in("Hong Kong / United Kingdom / Singapore") == {"HK", "SG", "UK"}


def test_us_role_is_dropped_for_a_uk_only_search():
    """The end-to-end version of the bug: a San Francisco posting scored 90
    and gave 'in UK' as a reason."""
    cfg = _cfg(countries=["UK"], relocate_to=[])
    j = _job(title="Engineering Manager",
             location="San Francisco, CA | New York City, NY")
    keep, why = match(j, cfg)
    assert keep is False
    assert "outside target countries" in why


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  pass  {name}")
        except Exception:
            bad += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
