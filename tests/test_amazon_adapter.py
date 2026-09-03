"""amazon.jobs, the largest employer this tool could not read.

Every assertion here is a field, not a count. A parser that returns the right
number of rows with an empty column is this repo's signature failure, and this
adapter produced one on its first live run: `normalized_location` is the bare
string "GBR" on virtual roles and on some ordinary ones, so stripping the
country code emptied the location entirely. Two of the first three hundred
rows. A row count would have passed.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters, fetch
from jobradar.adapters import platforms
from jobradar.models import Source

FIXTURE = Path(__file__).parent / "fixtures" / "amazon_jobs_search.json"
SRC = Source(company="Amazon", platform="amazon", country="UK", sector="technology",
             url="https://www.amazon.jobs/en/search.json?base_query=&sort=recent"
                 "&result_limit=100&offset=0&country=GBR")


def _jobs(payload=None):
    payload = payload or json.loads(FIXTURE.read_text(encoding="utf-8"))
    return list(platforms.parse_amazon(payload, SRC))


class AmazonFields(unittest.TestCase):
    def setUp(self):
        self.jobs = _jobs()

    def test_every_row_becomes_a_job(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(self.jobs), len(raw["jobs"]))

    def test_every_job_has_a_title(self):
        self.assertTrue(all(j.title.strip() for j in self.jobs))

    def test_every_job_has_a_location(self):
        self.assertEqual([j.title for j in self.jobs if not j.location.strip()], [])

    def test_a_location_never_ends_in_a_country_code(self):
        # "Cambridge, England, GBR" would put GBR in the city filter.
        for j in self.jobs:
            self.assertFalse(j.location.upper().endswith("GBR"), j.location)

    def test_a_bare_country_code_still_produces_a_location(self):
        # The live failure: normalized_location is "GBR" on virtual roles, so
        # stripping the code left nothing at all.
        payload = {"hits": 1, "jobs": [{
            "title": "Customer Service Associate", "job_path": "/en/jobs/1/x",
            "normalized_location": "GBR", "location": "GB, VCC UK, Virtual",
            "city": "Virtual", "state": "VCC UK", "country_code": "GBR",
            "posted_date": "August 28, 2026", "description": "Work from home."}]}
        job = _jobs(payload)[0]
        self.assertTrue(job.location.strip())
        self.assertNotIn("GBR", job.location)

    def test_virtual_is_not_stored_as_a_city(self):
        # Amazon's word for home-based. In the city column it reads exactly
        # like a town you could commute to.
        payload = {"hits": 1, "jobs": [{
            "title": "Associate", "job_path": "/en/jobs/1/x",
            "normalized_location": "GBR", "location": "GB, VCC UK, Virtual",
            "city": "Virtual", "country_code": "GBR",
            "posted_date": "August 28, 2026", "description": "x"}]}
        job = _jobs(payload)[0]
        self.assertEqual(job.city, "")
        self.assertTrue(job.remote, "a virtual role was not marked remote")

    def test_the_country_is_translated_out_of_alpha_three(self):
        # GBR is not what anything downstream filters on.
        self.assertTrue(all(j.country == "UK" for j in self.jobs))

    def test_every_job_has_a_posted_date_in_iso(self):
        for j in self.jobs:
            self.assertIsNotNone(j.posted_at, j.title)
            self.assertRegex(j.posted_at, r"^\d{4}-\d{2}-\d{2}$")

    def test_the_must_haves_are_in_the_description(self):
        # `description` alone is the pitch. `basic_qualifications` is where
        # "5+ years" lives, which is what dealbreakers and fit judge on.
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        first = raw["jobs"][0]
        job = self.jobs[0]
        self.assertGreater(len(job.description), len(first["description"]) // 2)
        for word in first["basic_qualifications"].split()[:4]:
            if len(word) > 4 and "<" not in word:
                self.assertIn(word.strip(",."), job.description)
                break

    def test_the_description_keeps_its_line_breaks(self):
        # `<br/>` collapsed to spaces turns a qualifications list into one
        # unreadable paragraph and loses the structure a regex reads.
        self.assertTrue(any("\n" in j.description for j in self.jobs))

    def test_no_markup_survives_into_the_description(self):
        for j in self.jobs:
            self.assertNotIn("<br", j.description)
            self.assertNotIn("</", j.description)

    def test_every_job_has_its_own_url_on_the_real_host(self):
        urls = [j.url for j in self.jobs]
        self.assertTrue(all(u.startswith("https://www.amazon.jobs/en/jobs/") for u in urls))
        self.assertEqual(len(set(urls)), len(urls))

    def test_the_employing_entity_does_not_become_the_company(self):
        # company_name is "Amazon Web Services Malaysia SDN. BHD." and varies
        # per row; one board must not become two hundred employers.
        self.assertTrue(all(j.company == "Amazon" for j in self.jobs))

    def test_uids_are_distinct(self):
        self.assertEqual(len({j.uid for j in self.jobs}), len(self.jobs))


class AmazonWiring(unittest.TestCase):
    def test_the_builder_scopes_by_country(self):
        u = adapters.by_name("amazon").build("GBR")
        self.assertIn("country=GBR", u)
        self.assertIn("result_limit=100", u)

    def test_the_platform_matches_its_own_url(self):
        self.assertEqual(adapters.detect(SRC.url).name, "amazon")

    def test_a_page_is_a_hundred_so_a_paging_bug_is_visible(self):
        self.assertEqual(fetch.PAGE_SIZES["amazon"], fetch.AMAZON_PAGE)
        self.assertEqual(fetch.AMAZON_PAGE, 100)

    def test_the_hit_cap_is_recorded(self):
        # `hits` never exceeds it and `offset` past it returns nothing, so a
        # run that reaches it is truncated by the API rather than complete.
        self.assertEqual(fetch.AMAZON_HIT_CAP, 10000)
        src_txt = Path(fetch.__file__).read_text(encoding="utf-8")
        block = src_txt[src_txt.index("def fetch_amazon("):]
        block = block[:block.index("\ndef ")]
        self.assertIn("AMAZON_HIT_CAP", block)
        self.assertIn("truncated = True", block)


if __name__ == "__main__":
    unittest.main()
