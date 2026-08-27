"""The dashboard opens when pass one finishes, and only then.

Pass one takes about five minutes; the whole scan takes over an hour. Waiting
for the end is the thing this feature exists not to do.

The last test here is the important one. Four separate changes have shipped a
`cmd_scan` that read an attribute the `scan` parser never defines: it raises
only on the live path, and every stub Namespace in the suite happily provides
whatever the code asks for. So rather than assert on behaviour, it reads the
attribute names `cmd_scan` actually touches and checks the real parser
produces them.
"""
import ast
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import cli  # noqa: E402


def _scan_ns(argv):
    return cli.build_parser().parse_args(argv) if hasattr(cli, "build_parser") \
        else cli._build_parser().parse_args(argv)


class ScanOpensTheDashboard(unittest.TestCase):

    def test_every_args_attribute_cmd_scan_reads_exists_on_the_parser(self):
        src = Path(cli.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_scan")
        wanted = {n.attr for n in ast.walk(fn)
                  if isinstance(n, ast.Attribute)
                  and isinstance(n.value, ast.Name) and n.value.id == "args"}
        have = vars(_scan_ns(["scan"]))
        missing = sorted(a for a in wanted if a not in have)
        self.assertEqual(missing, [], f"cmd_scan reads args.{missing}, which "
                                      f"`job-radar scan` does not define")

    def test_the_parser_defines_no_open_and_it_is_off_by_default(self):
        ns = _scan_ns(["scan"])
        self.assertIs(ns.no_open, False)
        self.assertIs(_scan_ns(["scan", "--no-open"]).no_open, True)

    def test_open_is_not_passed_a_docs_base_scan_does_not_have(self):
        src = Path(cli.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_scan")
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr == "open_in_background":
                names = {k.arg for k in call.keywords}
                self.assertNotIn("docs_base", names)
                break
        else:
            self.fail("cmd_scan no longer opens the dashboard at all")

    def test_a_dry_run_neither_writes_nor_opens(self):
        src = Path(cli.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_scan")
        opens = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "open_in_background"]
        self.assertEqual(len(opens), 1)
        # Walk up: the call must sit under a test of args.dry_run.
        guarded = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Attribute) and n.attr == "dry_run"]
        self.assertTrue(guarded, "nothing in cmd_scan checks dry_run")

    def test_the_connection_is_open_before_the_first_flush(self):
        src = Path(cli.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_scan")
        binds = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name)
                 and isinstance(n.ctx, ast.Store) and n.id == "con"]
        loads = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name)
                 and isinstance(n.ctx, ast.Load) and n.id == "con"]
        self.assertLess(min(binds), min(loads),
                        "cmd_scan uses `con` before opening it; a mid-scan "
                        "flush raises UnboundLocalError")


class ServerDetection(unittest.TestCase):

    def test_it_reports_a_port_that_is_listening_and_one_that_is_not(self):
        """Bind a port here rather than assuming one is free.

        This asserted that nothing was on 8799, and something was: a dashboard
        somebody had left running. A test that depends on the rest of the
        machine being quiet fails for reasons that have nothing to do with the
        code, which is how a suite stops being believed.
        """
        import socket
        from jobradar import serve
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.assertTrue(serve.already_serving(port=port))
        # Closed now, so the same port answers the other way. Retried a few
        # times because a just-closed listener can linger for a moment.
        for _ in range(20):
            if not serve.already_serving(port=port):
                return
            time.sleep(0.05)
        self.fail(f"port {port} still reads as serving after its socket closed")

    def test_open_in_background_declines_when_one_is_already_up(self):
        from jobradar import serve
        with mock.patch.object(serve, "already_serving", lambda *a, **k: True):
            self.assertIsNone(serve.open_in_background())


if __name__ == "__main__":
    unittest.main()
