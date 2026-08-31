"""A writer with no place must not erase a place already stored.

Loading a seed on top of a scanned database blanked `country` on 53 roles the
scan had resolved that morning. Shard rows carry a country only where the
builder could read one, so an empty value means "this writer does not know",
never "this role has no country". A blanked country drops the role out of
every country-filtered view, which reads exactly like the job being withdrawn.
`sector` already had this guard and carried the comment explaining why; place
did not.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.models import Job


def _job(**kw):
    base = dict(company="Acme", title="Engineering Manager",
                url="https://example.com/jobs/1", platform="greenhouse")
    base.update(kw)
    return Job(**base)


class SeedDoesNotBlankPlace(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")

    def _row(self):
        return self.con.execute(
            "SELECT location, city, country, sector FROM roles").fetchone()

    def test_an_empty_place_does_not_overwrite_a_stored_one(self):
        store.upsert_roles(self.con, [_job(
            location="London, England", city="London", country="UK",
            sector="technology")])
        store.upsert_roles(self.con, [_job()])   # same url, knows no place
        r = self._row()
        self.assertEqual(r["location"], "London, England")
        self.assertEqual(r["city"], "London")
        self.assertEqual(r["country"], "UK")
        self.assertEqual(r["sector"], "technology")

    def test_a_stated_place_still_replaces_an_earlier_one(self):
        store.upsert_roles(self.con, [_job(
            location="London, England", city="London", country="UK")])
        store.upsert_roles(self.con, [_job(
            location="Manchester, England", city="Manchester", country="UK")])
        r = self._row()
        self.assertEqual(r["location"], "Manchester, England")
        self.assertEqual(r["city"], "Manchester")


if __name__ == "__main__":
    unittest.main()
