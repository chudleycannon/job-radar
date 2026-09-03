"""Screening many roles at once, and the two guards that makes necessary.

There was one lock called `generate` and the answer to "how many at once" was
one. That was the right guard for the wrong reason: the collision it stopped
is two runs writing the SAME role's folder, and two different roles were never
in each other's way. So the lock is per role and the cap is separate.

The cap matters because screening is the expensive half of this tool. Ranking
a 2,804-role board is about 1.1M input tokens; screening the same board one
role at a time is about 168M. A bulk button is the one control here that can
spend real money on a mis-click.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import rank, runner, serve, store
from jobradar.output import interactive


class PerRoleLockAndACap(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")

    def test_two_different_roles_may_run_together(self):
        ok, _ = store.claim_slot(self.con, "generate", "a", 3)
        self.assertTrue(ok)
        ok, _ = store.claim_slot(self.con, "generate", "b", 3)
        self.assertTrue(ok, "two different roles blocked each other")

    def test_the_same_role_twice_is_refused(self):
        store.claim_slot(self.con, "generate", "a", 3)
        ok, why = store.claim_slot(self.con, "generate", "a", 3)
        self.assertFalse(ok)
        self.assertIn("this role", why)

    def test_the_cap_holds_and_the_loser_does_not_keep_its_lock(self):
        for k in ("a", "b"):
            self.assertTrue(store.claim_slot(self.con, "generate", k, 2)[0])
        ok, why = store.claim_slot(self.con, "generate", "c", 2)
        self.assertFalse(ok)
        self.assertIn("2", why)
        # The refused role must not be left holding a lock: that would refuse
        # it for ever, which is the wedge the orphan reaper exists to undo.
        held = self.con.execute(
            "SELECT COUNT(*) c FROM locks WHERE name LIKE 'generate:%'").fetchone()["c"]
        self.assertEqual(held, 2)

    def test_releasing_one_frees_a_slot(self):
        for k in ("a", "b"):
            store.claim_slot(self.con, "generate", k, 2)
        store.release(self.con, "generate:a")
        self.assertTrue(store.claim_slot(self.con, "generate", "c", 2)[0])


class CostsAreDerivedNotTyped(unittest.TestCase):
    """`60_000` lived as a literal in three files. A page quoting a cost the
    tool no longer charges is worse than one quoting none: the reader budgets
    against it."""

    def test_no_placeholder_survives_into_the_page(self):
        js = interactive._js()
        for token in ("%SCREEN_TOKENS%", "%BULK_LIMIT%", "%MAX_RUNNING%"):
            self.assertNotIn(token, js, f"{token} reached the browser unfilled")

    def test_the_page_quotes_the_real_numbers(self):
        js = interactive._js()
        self.assertIn(f"SCREEN_TOKENS={rank.SCREEN_TOKENS}", js)
        self.assertIn(f"BULK_LIMIT={serve.BULK_LIMIT}", js)
        self.assertIn(str(runner.MAX_RUNNING), js)

    def test_the_bulk_limit_is_not_above_what_one_click_should_spend(self):
        # A guard against a mis-click on select-all over a four thousand row
        # board becoming four thousand paid agent runs.
        self.assertLessEqual(serve.BULK_LIMIT, 100)


class TheSelectionIsWhatTheReaderCanSee(unittest.TestCase):
    def test_select_all_skips_hidden_rows(self):
        # "Select all" that quietly took the four thousand filtered-out rows
        # with it is the expensive version of this repo's usual mistake.
        js = interactive._JS
        pick = js[js.index("function pickVisible("):]
        pick = pick[:pick.index("async function")]
        self.assertIn("if(r.hidden) continue;", pick)

    def test_the_bulk_action_asks_before_it_spends(self):
        js = interactive._JS
        body = js[js.index("async function bulkGenerate("):]
        body = body[:body.index("\n(function()")]
        self.assertIn("confirm(", body)
        self.assertIn("SCREEN_TOKENS", body)

    def test_skipped_roles_come_back_named(self):
        # "3 skipped" on a shortlist means opening every row to find out which
        # three and why.
        src = Path(serve.__file__).read_text(encoding="utf-8")
        block = src[src.index('if path == "/api/generate/bulk":'):]
        block = block[:block.index("\n        uid = data.get")]
        self.assertIn('"why"', block)
        self.assertIn('"skipped"', block)
        self.assertIn('"started"', block)


if __name__ == "__main__":
    unittest.main()
