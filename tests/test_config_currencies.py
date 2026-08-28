"""A salary floor could only be written in three currencies out of forty-five.

`VALID_CURRENCIES` was the literal `{"GBP", "USD", "EUR"}` while the parser
could already stamp a figure INR, SGD, AED, CAD, AUD or any of the rest, from
the job's own country. So an Indian, Singaporean or Emirati reader could not
state a floor at all. The message was clear and offered no way forward, and
the workaround it pushed people towards -- write the floor in USD -- is
exactly what makes a mis-stamped currency dangerous: a USD floor against a
figure correctly labelled SGD is refused as a cross-currency comparison and
the role is kept, but a USD floor against a figure WRONGLY labelled USD
deletes it.

So the list is now built from the parser rather than written down beside it.
Two copies of the same fact drift, and this one drifts silently and in the
direction that costs roles: a currency the parser can produce but the config
refuses is a floor somebody cannot write, and a currency the config accepts
but the parser never produces is a floor that quietly stops filtering.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import config, salary  # noqa: E402


def test_the_two_lists_are_the_same_list():
    assert config.VALID_CURRENCIES == set(salary.KNOWN_CURRENCIES)


def test_every_currency_a_mark_in_an_advert_can_produce_is_a_floor_you_can_set():
    """The anti-drift half. Adding "kr" to the symbol table without widening
    the floor list would leave a Swedish figure the tool can read and a
    Swedish floor it refuses."""
    for mark, code in salary._SYMBOL_CUR.items():
        assert code in config.VALID_CURRENCIES, f"{mark} produces {code}"


def test_every_currency_a_country_can_produce_is_a_floor_you_can_set():
    for country, code in salary.CURRENCY_OF_COUNTRY.items():
        assert code in config.VALID_CURRENCIES, f"{country} produces {code}"


def test_the_currencies_the_error_used_to_refuse():
    for code in ("INR", "SGD", "AED", "CAD", "AUD", "NZD", "HKD", "BRL",
                 "CHF", "SEK", "PLN", "ZAR", "JPY"):
        assert config._currency(code, "salary.currency") == code


def test_a_floor_in_rupees_loads_and_then_filters():
    d = Path(tempfile.mkdtemp())
    p = d / "config.yaml"
    p.write_text("titles:\n  include: [engineering manager]\n"
                 "salary:\n  floor: 4000000\n  currency: INR\n"
                 "sources:\n  use_bundled: false\n", encoding="utf-8")
    cfg = config.load(p)
    assert cfg.salary_currency == "INR" and cfg.salary_floor == 4000000.0

    # And the floor does something, which is the point of being able to set it.
    low = salary.parse_text("INR 900,000")
    keep, why = salary.clears_floor(low, cfg.salary_floor, cfg.salary_currency)
    assert keep is False and "below floor" in why, why
    high = salary.parse_text("INR 5,000,000")
    assert salary.clears_floor(high, cfg.salary_floor, cfg.salary_currency)[0]


def test_something_that_is_not_a_currency_is_still_refused():
    """Widening the list is not the same as switching the check off. `currency:
    euro` uppercased to EURO, never matched EUR, and silently stopped the floor
    filtering anything at all."""
    for bad in ("XYZ", "pounds sterling", "bitcoin", "money"):
        try:
            config._currency(bad, "salary.currency")
        except config.ConfigError:
            continue
        raise AssertionError(f"{bad!r} was accepted as a currency")


def test_the_spelled_out_names_still_work():
    for text, want in (("euro", "EUR"), ("pounds", "GBP"), ("dollars", "USD"),
                       ("gbp", "GBP")):
        assert config._currency(text, "salary.currency") == want
