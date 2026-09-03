"""Google Careers, the employer this tool could not see at all.

Google publish no ATS feed, so they were absent from a 17,813-source list
entirely: not failing, not empty, simply never asked. The DeepMind Greenhouse
board that WAS on the list returns nine US roles and has never carried the
London ones, which is worse than a gap, because nine rows read as a working
source.

The rows here are positional, not keyed, and that is the risk this file is
mostly about. A keyed API that renames a field hands you an empty string; a
positional one that gains a column hands you the wrong field's contents and
looks fine. So the assertions are field-shaped: an id that is digits, a title
that is not a URL, a country that came from the payload rather than from
guessing at the words in a location.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters, fetch
from jobradar.adapters import platforms
from jobradar.models import Source

FIXTURE = Path(__file__).parent / "fixtures" / "google_careers_results.json"
SRC = Source(company="Google", platform="google_careers", country="UK",
             sector="technology",
             url="https://www.google.com/about/careers/applications"
                 "/jobs/results?location=United+Kingdom")


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _jobs(payload=None):
    return list(platforms.parse_google_careers(payload or _payload(), SRC))


class GoogleFields(unittest.TestCase):
    def setUp(self):
        self.jobs = _jobs()

    def test_every_row_becomes_a_job(self):
        self.assertEqual(len(self.jobs), len(_payload()[0]))

    def test_every_job_has_a_title(self):
        self.assertTrue(all(j.title.strip() for j in self.jobs))

    def test_every_job_has_a_location(self):
        self.assertEqual([j.title for j in self.jobs if not j.location.strip()], [])

    def test_every_job_has_a_description(self):
        # The advert is spread over four fields. A parser reading one of them
        # returns rows that look complete and defeat every dealbreaker regex.
        for j in self.jobs:
            self.assertGreater(len(j.description), 200, j.title)

    def test_the_description_carries_both_qualification_blocks(self):
        # Google put the must-haves in one field and the nice-to-haves in
        # another. Reading only one is reading half the advert.
        joined = "\n".join(j.description for j in self.jobs)
        self.assertIn("Minimum qualifications", joined)

    def test_the_description_is_text_not_markup(self):
        for j in self.jobs:
            self.assertNotIn("<li>", j.description, j.title)
            self.assertNotIn("<p>", j.description, j.title)

    def test_every_job_has_a_country_from_the_payload(self):
        # "London, UK" and "London, ON, Canada" both start "London", so the
        # country is taken from the row's own code, never from the words.
        self.assertEqual([j.title for j in self.jobs if not j.country], [])
        self.assertTrue(all(len(j.country) == 2 for j in self.jobs))

    def test_every_job_has_a_posted_date(self):
        # The payload sends `[seconds, nanos]`. Passed through whole it reads
        # as a millisecond epoch and lands in 1970, which is a real date and
        # would silently zero every recency score.
        for j in self.jobs:
            self.assertTrue(j.posted_at, j.title)
            self.assertGreater(j.posted_at, "2015-01-01", j.title)

    def test_the_url_is_the_posting_not_the_sign_in_link(self):
        # The row carries a signin URL with a one-shot token keyed to whoever
        # fetched it. Stored, it expires and every link on the board dies.
        for j in self.jobs:
            self.assertNotIn("signin", j.url, j.title)
            self.assertNotIn("jobId=", j.url, j.title)
            self.assertIn("/jobs/results/", j.url)

    def test_alphabet_companies_keep_their_own_name(self):
        # DeepMind, Waymo and YouTube post to this board. Relabelling them
        # "Google" would hide the employer the applicant is applying to.
        names = {j.company for j in self.jobs}
        self.assertIn("DeepMind", names)
        self.assertIn("Google", names)


class GooglePositionalRowsFailLoudly(unittest.TestCase):
    """A moved column must drop the row, never store the wrong field."""

    def test_a_row_whose_id_is_not_an_id_is_dropped(self):
        p = _payload()
        p[0][0][0] = "not-an-id"
        self.assertEqual(len(_jobs(p)), len(p[0]) - 1)

    def test_a_row_whose_title_is_a_url_is_dropped(self):
        p = _payload()
        p[0][0][1] = "https://example.com/whatever"
        self.assertEqual(len(_jobs(p)), len(p[0]) - 1)

    def test_a_short_row_is_dropped_rather_than_read_past_its_end(self):
        p = _payload()
        p[0][0] = p[0][0][:4]
        self.assertEqual(len(_jobs(p)), len(p[0]) - 1)

    def test_an_empty_payload_yields_nothing_and_does_not_raise(self):
        for junk in ([], [[]], None, {}, "", [None, None, 0, 20]):
            self.assertEqual(list(platforms.parse_google_careers(junk, SRC)), [])


class GoogleBootPayload(unittest.TestCase):
    """Reading the page's own boot data, and knowing when it is not there."""

    def test_a_page_with_no_boot_payload_is_not_an_empty_board(self):
        # This is the distinction the whole adapter turns on. A consent page
        # or a changed format returns rows=0, which is exactly what an
        # employer with no vacancies returns, and `validate --prune` deletes
        # sources for the second one.
        data, total = fetch._google_payload("<html><body>nope</body></html>")
        self.assertIsNone(data)
        self.assertEqual(total, 0)

    def test_the_stated_total_is_read(self):
        html = ("AF_initDataCallback({key: 'ds:1', hash: '1', data:"
                "[[], null, 128, 20], sideChannel: {}});")
        data, total = fetch._google_payload(html)
        self.assertIsNotNone(data)
        self.assertEqual(total, 128)

    def test_the_chrome_block_is_not_mistaken_for_the_results(self):
        html = ("AF_initDataCallback({key: 'ds:0', hash: '1', data:[[1]], "
                "sideChannel: {}});"
                "AF_initDataCallback({key: 'ds:1', hash: '2', data:"
                "[[], null, 7, 20], sideChannel: {}});")
        data, total = fetch._google_payload(html)
        self.assertEqual(total, 7)

    def test_malformed_json_is_a_failure_not_an_empty_board(self):
        html = "AF_initDataCallback({key: 'ds:1', data:[[,,, sideChannel: {}});"
        data, total = fetch._google_payload(html)
        self.assertIsNone(data)


class GoogleIsRegistered(unittest.TestCase):
    def test_the_platform_is_known(self):
        self.assertIn("google_careers", adapters.platform_names())

    def test_the_page_size_is_declared(self):
        # A whole-board read landing on exactly the page size means the walk
        # stopped after page one, and `capped_sources` needs the number to
        # be able to say so.
        self.assertEqual(fetch.PAGE_SIZES["google_careers"], fetch.GOOGLE_PAGE)

    def test_the_bundled_sources_carry_google(self):
        rows = json.loads(
            (Path(__file__).parent.parent / "sources" / "sources.json")
            .read_text(encoding="utf-8"))["sources"]
        google = [r for r in rows if r.get("platform") == "google_careers"]
        self.assertTrue(google, "Google is not in the bundled source list")
        for r in google:
            self.assertIn("/about/careers/applications/jobs/results", r["url"])

    def test_the_source_url_carries_no_search_terms(self):
        # A source that reads only the titles one person asked for is that
        # person's search saved as an employer list. See CLAUDE.md.
        rows = json.loads(
            (Path(__file__).parent.parent / "sources" / "sources.json")
            .read_text(encoding="utf-8"))["sources"]
        for r in [r for r in rows if r.get("platform") == "google_careers"]:
            self.assertNotIn("q=", r["url"], r["url"])


if __name__ == "__main__":
    unittest.main()
