"""Two commands that answered without doing the thing they claim to do.

`rescreen` exists to be the second opinion: it re-applies the current config
to roles already stored, and the whole reason to trust it is that it runs the
same code a scan runs. It re-ran the titles, the locations, the dealbreakers
and the salary floor, and left `city`, `country` and `work_mode` holding
whatever the scan that first saw the role thought. Those three are derived by
`screen.enrich` and then written into the table, so every fix to that
derivation, or to an adapter feeding it, reached new rows and no old ones.
Measured on one row: a Manchester posting stored as city "US Remote", country
"US", work mode "remote" came through a rescreen unchanged, under the sentence
"All 1 roles still match your config."

`seed load` guarded `read_index` and not `load`, which is the call that opens
the shards. A partly downloaded shard set therefore printed a cheerful summary
of what it was about to read and then a nine-frame traceback.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.cli import main


@contextlib.contextmanager
def _tmp():
    root = Path(tempfile.mkdtemp())
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _cfg(root: Path) -> Path:
    cfg = root / "config.yaml"
    cfg.write_text(
        "titles:\n  include: ['engineering manager']\n"
        "locations:\n  countries: ['UK']\n"
        "sources:\n  use_bundled: false\n",
        encoding="utf-8")
    return cfg


def _run(cfg, *argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["-c", str(cfg), *argv])
    return code, buf.getvalue()


DESC = "A fine EM role in our Manchester office. " + "detail " * 60


def _db_with_a_stale_row(root: Path) -> Path:
    """One role whose stored derivations disagree with what the code says now.

    Wrong in all three columns and in different directions: a US city on a UK
    posting, a US country, and "remote" on an advert that says it is in an
    office. That is the shape an adapter fix leaves behind.
    """
    db = root / "roles.db"
    con = store.connect(db)
    con.execute(
        "INSERT INTO roles (uid,company,title,url,location,city,country,"
        "work_mode,platform,description,score,reasons,flags,first_seen,"
        "last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("u1", "Acme", "Engineering Manager", "https://acme.invalid/1",
         "Manchester, United Kingdom", "US Remote", "US", "remote", "ashby",
         DESC, 70, "[]", "[]", "2026-08-27", "2026-08-27"))
    con.close()
    return db


def _row(db):
    con = store.connect(db)
    try:
        return dict(con.execute("SELECT city, country, work_mode FROM roles "
                                "WHERE uid='u1'").fetchone())
    finally:
        con.close()


# -------------------------------------------------------------- rescreen

def test_rescreen_re_derives_the_stored_city_country_and_work_mode():
    """All three were left exactly as the first scan wrote them, so an adapter
    fix could only reach existing rows through a full rescan."""
    with _tmp() as root:
        db = _db_with_a_stale_row(root)
        code, out = _run(_cfg(root), "rescreen", "--db", str(db))
        assert code == 0, out
        got = _row(db)
        assert got["city"] == "Manchester", got
        assert got["country"] == "UK", got
        assert got["work_mode"] != "remote", got

        # The limit of what this can re-derive, asserted here so the fix
        # cannot grow into a second bug. The board's own country tag is the
        # fallback a scan uses when the posting names nowhere, and it is not
        # stored per role. Blanking it would hand the country filter an empty
        # string, which that filter reads as "not here" -- dropping somebody's
        # role out of their own results in order to correct a tag.
        con = store.connect(db)
        con.execute(
            "INSERT INTO roles (uid,company,title,url,location,city,country,"
            "work_mode,platform,description,score,reasons,flags,first_seen,"
            "last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("u2", "Acme", "Engineering Manager", "https://acme.invalid/2",
             "", "", "UK", "unstated", "greenhouse", DESC, 70, "[]", "[]",
             "2026-08-27", "2026-08-27"))
        con.close()
        _run(_cfg(root), "rescreen", "--db", str(db))
        con = store.connect(db)
        try:
            tagged = con.execute("SELECT country FROM roles WHERE uid='u2'"
                                 ).fetchone()["country"]
        finally:
            con.close()
        assert tagged == "UK", f"the board's tag was thrown away: {tagged!r}"


def test_rescreen_says_it_re_derived_them():
    """On a database where every role still matches, this is the only thing
    the command changes. "All N roles still match your config" and a silent
    rewrite of three columns is a report that omits its own only effect."""
    with _tmp() as root:
        db = _db_with_a_stale_row(root)
        _, out = _run(_cfg(root), "rescreen", "--db", str(db))
        assert "Re-derived" in out, out
        # And only once. A second run over a row it has already fixed must
        # report no changes, or the count means nothing and a settled database
        # is indistinguishable from one that keeps drifting.
        _, again = _run(_cfg(root), "rescreen", "--db", str(db))
        assert "Re-derived" not in again, again


# ------------------------------------------------------------- seed load

def _broken_seed(root: Path) -> Path:
    """An index that parses and a shard that is eight bytes of something
    else, which is what half a download looks like."""
    d = root / "seed"
    d.mkdir()
    (d / "index.json").write_text(
        '{"schema":1,"generated":"2026-01-01","boards":1,'
        '"shards":{"UK":{"roles":1,"bytes":10}}}', encoding="utf-8")
    (d / "UK.jsonl.gz").write_text("not gzip", encoding="utf-8")
    return d


def test_a_corrupt_shard_is_a_sentence_not_a_traceback():
    """`seed.load` writes an actionable message and `cmd_seed_load` let it out
    as a `ValueError` under nine frames, because the guard was around
    `read_index` and the shards are opened by `load`."""
    with _tmp() as root:
        code, out = _run(_cfg(root), "seed", "load", str(_broken_seed(root)),
                         "--dry-run")
        assert code == 1, out
        assert "Traceback" not in out
        assert "Could not read the seed" in out, out


def test_a_shard_the_index_promises_and_does_not_have_is_the_same_sentence():
    """A missing shard arrives as FileNotFoundError, which is an OSError, and
    is the other half of a partly finished download."""
    with _tmp() as root:
        d = root / "seed"
        d.mkdir()
        (d / "index.json").write_text(
            '{"schema":1,"generated":"2026-01-01","boards":1,'
            '"shards":{"UK":{"roles":1,"bytes":10}}}', encoding="utf-8")
        code, out = _run(_cfg(root), "seed", "load", str(d), "--dry-run")
        assert code == 1, out
        assert "Traceback" not in out
        assert "Could not read the seed" in out, out
