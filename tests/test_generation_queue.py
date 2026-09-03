"""Everything asked for is queued. The cap limits what runs, not what is taken.

Nine roles were selected for screening, three started and six were refused,
while the dialog that took the click said "at most 3 run at once and the rest
queue". They did not queue. Selecting nine and getting three is worse than
useless, because the six that bounced look identical to six never asked for.

The second half of this is the reaper. A pending job has no thread and no
subprocess, so there is nothing to orphan, but `reap_orphans` failed every
unfinished row on startup. That was right when a click ran immediately and
there was no queue; with a queue it destroys exactly what the queue is for.
A restart failed all nine jobs, six of them with "the server restarted while
this was running" on jobs that had never run.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store


class TheQueue(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")
        for i in range(9):
            self.con.execute(
                "INSERT INTO roles (uid,company,title,url,platform,first_seen,last_seen) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"u{i}", "Acme", "EM", f"https://e.test/{i}", "greenhouse",
                 "2026-01-01", "2026-01-01"))

    def _queue_all(self):
        return [store.enqueue(self.con, f"u{i}", "screen") for i in range(9)]

    def test_every_role_asked_for_is_accepted(self):
        ids = self._queue_all()
        self.assertEqual(len(set(ids)), 9)
        self.assertEqual(store.pending_count(self.con), 9)

    def test_asking_twice_for_one_role_does_not_queue_it_twice(self):
        # A double-click must not put two runs into one role's folder.
        a = store.enqueue(self.con, "u0", "screen")
        b = store.enqueue(self.con, "u0", "screen")
        self.assertEqual(a, b)
        self.assertEqual(store.pending_count(self.con), 1)

    def test_a_busy_role_is_skipped_rather_than_blocking_the_queue(self):
        self._queue_all()
        store.claim(self.con, "generate:u0")
        row = store.next_pending(self.con, exclude_uids=store.busy_uids(self.con))
        self.assertIsNotNone(row)
        self.assertNotEqual(row["uid"], "u0")

    def test_busy_uids_reports_what_holds_a_lock(self):
        store.claim(self.con, "generate:u3")
        store.claim(self.con, "generate:u7")
        self.assertEqual(sorted(store.busy_uids(self.con)), ["u3", "u7"])

    def test_the_queue_is_taken_oldest_first(self):
        self._queue_all()
        self.assertEqual(store.next_pending(self.con)["uid"], "u0")


class TheReaperLeavesTheQueueAlone(unittest.TestCase):
    def setUp(self):
        self.con = store.connect(":memory:")
        self.con.execute(
            "INSERT INTO roles (uid,company,title,url,platform,first_seen,last_seen) "
            "VALUES ('u','Acme','EM','https://e.test/1','greenhouse',"
            "'2026-01-01','2026-01-01')")

    def test_a_restart_fails_a_running_job(self):
        job = store.enqueue(self.con, "u", "screen")
        store.mark_job(self.con, job, "running")
        store.reap_orphans(self.con, restarted=True)
        state = self.con.execute("SELECT state FROM jobs WHERE id=?",
                                 (job,)).fetchone()["state"]
        self.assertEqual(state, "failed")

    def test_a_restart_leaves_a_queued_job_queued(self):
        job = store.enqueue(self.con, "u", "screen")
        store.reap_orphans(self.con, restarted=True)
        state = self.con.execute("SELECT state FROM jobs WHERE id=?",
                                 (job,)).fetchone()["state"]
        self.assertEqual(state, "pending",
                         "a queued job was failed for a run that never happened")

    def test_a_queued_job_survives_to_be_picked_up(self):
        store.enqueue(self.con, "u", "screen")
        store.reap_orphans(self.con, restarted=True)
        self.assertEqual(store.pending_count(self.con), 1)


if __name__ == "__main__":
    unittest.main()
