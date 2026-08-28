"""What a wrong `--db` did, which was mostly to answer anyway.

Four paths, four failures, all found on the same afternoon. A path that is not
there was created, filled with an empty schema and reported as a database with
nothing in it, exit 0. A directory, a file that is not a database, and a
directory this user cannot write each came out as a raw sqlite exception under
a nine-frame traceback, and two of the three said the same sentence: `unable
to open database file`, which names neither the path nor the mistake.

The first is the one that matters. `0 role(s)` from a typo renders exactly
like `0 role(s)` from the real database on a quiet week, and this repository
exists because of failures that render as successes.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.cli import main


@contextlib.contextmanager

def _lab():
    """A scratch directory, plus a config that loads without a network."""
    root = Path(tempfile.mkdtemp())
    cfg = root / "config.yaml"
    cfg.write_text(
        "titles:\n"
        "  include: ['engineering manager']\n"
        "locations:\n"
        "  countries: ['UK']\n"
        "sources:\n"
        "  use_bundled: false\n",
        encoding="utf-8")
    try:
        yield root, cfg
    finally:
        # Restore anything chmodded down, or the tree cannot be removed.
        for p in root.rglob("*"):
            with contextlib.suppress(OSError):
                if p.is_dir():
                    p.chmod(0o755)
        shutil.rmtree(root, ignore_errors=True)


def _run(cfg, *argv) -> tuple[int, str]:
    """Run the CLI and give back its exit code and everything it printed."""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["-c", str(cfg), *argv])
    return code, buf.getvalue()


# ------------------------------------------------- a database that is not there

def test_a_read_command_does_not_invent_a_database():
    """`job-radar list --db typo.db` printed `0 role(s)`, exited 0, and left a
    64KB file behind. Nothing about that output said the path was wrong."""
    with _lab() as (root, cfg):
        typo = root / "typo.db"
        code, out = _run(cfg, "list", "--db", str(typo))
        assert code == 1, out
        assert "0 role(s)" not in out, out
        assert str(typo) in out
        assert not typo.exists(), "a read command created the database"


def test_no_read_command_invents_a_database():
    """`applied` already refused, because it looks a role up and finds none.
    The other readers all answered confidently instead, and a rule that holds
    for one command out of six is not a rule."""
    with _lab() as (root, cfg):
        typo = root / "nope.db"
        for argv in (["list"],
                     ["applied", "https://example.invalid/1"],
                     ["rescreen"],
                     ["enrich"]):
            code, out = _run(cfg, *argv, "--db", str(typo))
            assert code == 1, (argv, out)
            assert "No database at" in out, (argv, out)
            assert not typo.exists(), f"{argv[0]} created the database"

        # The other half of the rule, kept in the same test so it cannot be
        # deleted on its own. This is about READ commands: `scan`, `seed load`
        # and now `serve` exist to fill or expose a fresh database and must go
        # on making one, or a first browser run has nowhere to write.
        # `scan --dry-run` runs on `:memory:`, which exists on no filesystem,
        # and the path rules must not read that as a typo either.
        fresh = root / "sub" / "fresh.db"
        store.connect(fresh).close()
        assert fresh.exists(), "a writer stopped creating the database"
        mem = store.connect(":memory:")
        try:
            assert mem.execute("SELECT COUNT(*) c FROM roles"
                               ).fetchone()["c"] == 0
        finally:
            mem.close()


def test_the_daily_nudge_does_not_create_the_database_either():
    """The staleness nudge runs before `list`, `serve` and `rank`, opens the
    database to remember it has spoken today, and so got to a mistyped `--db`
    first. The command was then blamed for a file the nudge had made."""
    from jobradar import cli
    with _lab() as (root, cfg):
        typo = root / "nudged.db"
        cfg_obj = cli._cfg_or_default(str(cfg))
        # Force the nudge to have something to say: it returns early unless
        # the bundled list is in use and is older than the threshold.
        cfg_obj.use_bundled_sources = True
        real_age = cli.src_mod.age_days
        cli.src_mod.age_days = lambda: cli.STALE_AFTER_DAYS + 1
        import io
        from contextlib import redirect_stdout
        try:
            with redirect_stdout(io.StringIO()):
                cli._daily_sync_nudge(cfg_obj, str(typo))
        finally:
            cli.src_mod.age_days = real_age
        assert not typo.exists(), "the nudge created the database"


# ------------------------------------------------------------- unusable paths

def test_a_directory_given_to_db_is_named_as_one():
    """`sqlite3.OperationalError: unable to open database file`, under a
    traceback, for `--db out/` instead of `--db out/roles.db`."""
    with _lab() as (root, cfg):
        d = root / "adir"
        d.mkdir()
        code, out = _run(cfg, "list", "--db", str(d))
        assert code == 1
        assert "Traceback" not in out
        assert "is a directory, not a database file" in out, out


def test_a_file_that_is_not_a_database_is_named_as_one():
    """`sqlite3.DatabaseError: file is not a database` says which of the two
    things went wrong and nothing about which file or what to do."""
    with _lab() as (root, cfg):
        notdb = root / "config-not-db.db"
        notdb.write_text("titles:\n  include: []\n", encoding="utf-8")
        code, out = _run(cfg, "list", "--db", str(notdb))
        assert code == 1
        assert "Traceback" not in out
        assert "not a job-radar database" in out, out
        assert str(notdb) in out


def test_a_directory_nobody_can_write_is_named_as_one():
    """The third `unable to open database file`, and the one that is not
    about the file at all: the directory exists and the mode on it refuses.
    WAL mode writes `-wal` and `-shm` beside the database, so this is what a
    scan meets pointing `--db` at a folder it cannot write, and sqlite's
    sentence names neither the directory nor the permission."""
    if _cannot_make_a_directory_unwritable():
        return
    with _lab() as (root, cfg):
        ro = root / "readonly"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            store.connect(ro / "roles.db")
            raise AssertionError("connect accepted an unwritable directory")
        except store.StoreError as exc:
            assert "cannot write there" in str(exc), str(exc)
        finally:
            ro.chmod(0o755)


def test_a_scan_into_an_unwritable_folder_says_so_before_it_scans():
    """The writer's half of the same mistake. `store.connect` is the first
    thing `scan` does with the path, so this has to be a sentence there too
    rather than an exception the CLI never catches."""
    if _cannot_make_a_directory_unwritable():
        return
    with _lab() as (root, cfg):
        ro = root / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            try:
                store.connect(ro / "deeper" / "roles.db")
                raise AssertionError("connect accepted an unwritable path")
            except store.StoreError as exc:
                assert "Pick a --db path somewhere you can write" in str(exc)
        finally:
            ro.chmod(0o755)


def _cannot_make_a_directory_unwritable() -> bool:
    """Whether this platform can even set up the case under test.

    Two ways it cannot. On Windows a directory mode is not a write
    permission: `chmod(0o500)` sets the read-only attribute, which NTFS
    ignores for creating files inside, so the "unwritable" directory is
    perfectly writable and the test asserts against a thing that did not
    happen. And root writes anywhere regardless of the mode.

    Checked by trying it rather than by naming platforms, so a filesystem
    that behaves differently is handled by what it does, not by what its
    operating system usually does. `os.geteuid` is not used: it does not
    exist on Windows, and every one of these tests died on the
    AttributeError before reaching its own subject.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ro = pathlib.Path(d) / "ro"
        ro.mkdir()
        try:
            ro.chmod(0o500)
            (ro / "probe").write_text("x", encoding="utf-8")
        except OSError:
            return False        # genuinely refused: the case is testable
        finally:
            try:
                ro.chmod(0o700)
            except OSError:
                pass
        return True             # the write went through, so there is no case
