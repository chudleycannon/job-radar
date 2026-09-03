"""A server that cannot take the port must not clean up after the one that did.

Everything `serve` does at startup assumes "I am starting, therefore no other
server is running": it clears interrupted generations, releases locks, and
resets rank state on the strength of it. A launchd job with KeepAlive breaks
that assumption in the worst way. It retries every few seconds, loses the bind
to the server already up, and on the way to losing it reaps that server's
work. Seven queued screenings died four seconds after starting, killed by a
process that never served a request.

A failed bind is the only reliable evidence another server owns this database,
so the bind happens before anything else.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import serve as serve_mod

SRC = Path(serve_mod.__file__).read_text(encoding="utf-8")
BODY = SRC[SRC.index("def serve(db_path=None"):]


class BindFirst(unittest.TestCase):
    def test_the_bind_comes_before_the_database_is_opened(self):
        bind = BODY.index("ThreadingHTTPServer((host, port)")
        con = BODY.index("con = store.connect(db_path)")
        self.assertLess(bind, con, "the database is touched before the port is taken")

    def test_the_bind_comes_before_the_reaper(self):
        bind = BODY.index("ThreadingHTTPServer((host, port)")
        reap = BODY.index("store.reap_orphans")
        self.assertLess(bind, reap, "a losing bind still reaps the winner's jobs")

    def test_the_bind_comes_before_locks_are_cleared(self):
        bind = BODY.index("ThreadingHTTPServer((host, port)")
        locks = BODY.index("store.clear_locks")
        self.assertLess(bind, locks)

    def test_a_taken_port_returns_rather_than_continuing(self):
        head = BODY[:BODY.index("con = store.connect(db_path)")]
        self.assertIn("EADDRINUSE", head)
        self.assertIn("return 1", head)

    def test_the_server_is_only_constructed_once(self):
        self.assertEqual(BODY.count("ThreadingHTTPServer((host, port)"), 1)


if __name__ == "__main__":
    unittest.main()
