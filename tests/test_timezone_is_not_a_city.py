"""A time zone in the location field is a working-hours requirement, not a place.

Junction advertise an Engineering Manager at "GMT / BST (UK, Portugal,
Ireland), United Kingdom (Remote)". `city_of` takes the first comma part and
then splits on the slash, so the role was stored in the city "GMT", which
lands in the dashboard's town filter looking exactly like somewhere you could
live. Remote-first employers write the zone where a city goes often enough
that this is a category, not one board's quirk.

Same family as Workday's "2 Locations" and Amazon's "Virtual": a value that
means something other than a place, sitting where a place goes.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.screen import city_of


class ATimeZoneIsNotACity(unittest.TestCase):
    def test_the_posting_that_found_this(self):
        self.assertEqual(
            city_of("GMT / BST (UK, Portugal, Ireland), United Kingdom (Remote)"),
            "")

    def test_the_zones_employers_actually_advertise(self):
        for s in ("GMT, London", "BST, Manchester", "UTC+2, Berlin",
                  "CET, Amsterdam", "PST, San Francisco", "EST, New York",
                  "IST, Bengaluru", "AEST, Sydney", "SGT, Singapore"):
            self.assertEqual(city_of(s), "", s)

    def test_a_bare_offset_is_not_a_city(self):
        for s in ("UTC+5", "GMT-3", "UTC +1"):
            self.assertEqual(city_of(s), "", s)


class OrdinaryPlacesSurvive(unittest.TestCase):
    """The reason `wet`, `west` and `art` are not on the list."""

    def test_a_place_that_is_also_a_word(self):
        self.assertEqual(city_of("West, Texas"), "West")
        self.assertEqual(city_of("Art, France"), "Art")

    def test_a_place_that_merely_starts_with_a_zone(self):
        # Anchored to the whole string, so a prefix must not match.
        self.assertEqual(city_of("Westminster, London"), "Westminster")
        self.assertEqual(city_of("Estoril, Portugal"), "Estoril")
        self.assertEqual(city_of("Istanbul, Turkey"), "Istanbul")

    def test_the_ordinary_case_is_untouched(self):
        self.assertEqual(city_of("London, England"), "London")
        self.assertEqual(city_of("Bristol, England"), "Bristol")
        self.assertEqual(city_of("Hemel Hempstead, England"), "Hemel Hempstead")


if __name__ == "__main__":
    unittest.main()
