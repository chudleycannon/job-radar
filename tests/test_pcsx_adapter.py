"""Phenom PCSX, the platform Microsoft are on.

Every assertion here is a field, not a count. A parser that returns the right
number of rows with an empty column is the failure this repo keeps producing:
`parse_workable` asked for a key the API has never sent and 2,094 boards
stored postings with no location, no error anywhere, for months. So a new
adapter proves each field it claims to fill.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters
from jobradar.adapters import platforms
from jobradar.models import Source

FIXTURE = Path(__file__).parent / "fixtures" / "pcsx_microsoft_search.json"
SRC = Source(
    company="Microsoft",
    url="https://apply.careers.microsoft.com/api/pcsx/search"
        "?domain=microsoft.com&query=&location=&start=0",
    platform="pcsx",
    sector="technology",
)


def _jobs():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return list(platforms.parse_pcsx(payload, SRC))


class PcsxFields(unittest.TestCase):
    def setUp(self):
        self.jobs = _jobs()

    def test_every_row_becomes_a_job(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(self.jobs), len(payload["data"]["positions"]))

    def test_every_job_has_a_title(self):
        self.assertTrue(all(j.title.strip() for j in self.jobs))

    def test_every_job_has_a_location(self):
        # The one that has bitten hardest. An adapter reading a key the API
        # does not send returns "" forever and looks like a working scan.
        missing = [j.title for j in self.jobs if not j.location.strip()]
        self.assertEqual(missing, [])

    def test_a_location_is_a_place_and_not_a_count(self):
        # Microsoft write "United States, Multiple Locations, Multiple
        # Locations". Stored whole it renders as a city.
        for j in self.jobs:
            self.assertNotIn("multiple locations", j.location.lower())

    def test_every_job_has_its_own_posting_url(self):
        urls = [j.url for j in self.jobs]
        self.assertTrue(all(u.startswith("https://") for u in urls))
        self.assertTrue(all("/careers/job/" in u for u in urls))
        # A shared URL is not a per-posting address, and uid derives from it.
        self.assertEqual(len(set(urls)), len(urls))

    def test_every_job_has_a_posted_date_in_iso(self):
        for j in self.jobs:
            self.assertIsNotNone(j.posted_at, j.title)
            self.assertRegex(j.posted_at, r"^\d{4}-\d{2}-\d{2}$")

    def test_every_job_has_a_department(self):
        self.assertTrue(all(j.department for j in self.jobs))

    def test_the_platform_and_company_are_carried(self):
        self.assertTrue(all(j.platform == "pcsx" for j in self.jobs))
        self.assertTrue(all(j.company == "Microsoft" for j in self.jobs))

    def test_the_description_is_empty_and_that_is_deliberate(self):
        # The search endpoint carries no advert. Empty is the honest answer,
        # and `enrich._from_pcsx` is what fills it. A teaser invented from the
        # title here would let the dealbreakers run against nothing and report
        # that they passed.
        self.assertTrue(all(j.description == "" for j in self.jobs))

    def test_work_mode_comes_from_the_employers_own_field(self):
        # `workLocationOption` is stated, so nothing here guesses from words
        # in a title.
        self.assertTrue(all(j.remote is False for j in self.jobs))

    def test_a_place_is_stored_most_specific_first(self):
        """PCSX writes "United States, Washington, Redmond"; every other board
        here writes the reverse, and the shared reader takes the first comma
        part. Left alone, every Microsoft role's city was its country."""
        from jobradar.screen import city_of
        cities = [city_of(j.location) for j in self.jobs]
        # Not "all of them": a posting that states only "United States" has no
        # town, and inventing one would be worse than leaving it empty.
        self.assertIn("Redmond", cities)
        for c in cities:
            self.assertNotIn("United States", c)

    def test_a_multi_place_list_does_not_become_a_city(self):
        """A list of places joined with `;` sits exactly where a town would."""
        from jobradar.screen import city_of
        self.assertEqual(city_of("United Kingdom; Ireland"), "")
        self.assertEqual(city_of("London, England; Dublin, Ireland"), "London")

    def test_uids_are_distinct(self):
        self.assertEqual(len({j.uid for j in self.jobs}), len(self.jobs))


class PcsxWiring(unittest.TestCase):
    def test_the_url_builder_uses_the_registrable_domain(self):
        built = adapters.by_name("pcsx").build("apply.careers.microsoft.com")
        self.assertIn("domain=microsoft.com", built)
        self.assertIn("/api/pcsx/search", built)

    def test_the_platform_matches_its_own_url(self):
        self.assertEqual(adapters.detect(SRC.url).name, "pcsx")

    def test_a_page_is_ten_so_a_paging_bug_is_visible(self):
        from jobradar import fetch
        self.assertEqual(fetch.PAGE_SIZES["pcsx"], 10)

    def test_enrichment_is_wired_for_the_platform_and_the_url(self):
        from jobradar import enrich
        self.assertIn("pcsx", enrich.FETCHERS)
        fns = enrich.fetcher_for(
            "https://apply.careers.microsoft.com/careers/job/1970393556981844",
            "pcsx")
        self.assertTrue(fns, "no fetcher would run for a PCSX posting")


if __name__ == "__main__":
    unittest.main()
