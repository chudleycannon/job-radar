"""`seed load` reporting more than it fetched, and crashing on what it did.

Four failures found by running the command the way an unlucky person runs it:
against a URL, with a config that has a `relocate_to`, with no countries at
all, over a directory that got cut short, and over one downloaded months ago.
Three of the four ended in `Stored.` or in a summary that reads exactly like a
healthy import, which is the shape this repo keeps producing.

* **The download and the read asked for different countries.** `cmd_seed_load`
  handed `seed.fetch` the bare `cfg.countries` and handed `describe` and
  `seed.load` the same list plus `relocate_to`. So every config with a
  relocation country in it downloaded fewer shards than the read then
  demanded, and died on "the index lists a SG shard holding 20 roles, but
  SG.jsonl.gz is not there. This shard set is incomplete or was written by a
  different version of job-radar. Rebuild it rather than importing part of
  it." The published set was fine. Nobody had asked for that shard, and the
  message sent the reader to rebuild somebody else's file.

* **An empty country list means two different things to the two halves.**
  `seed.shards_for([])` is `unplaced, multiple`, so `fetch` takes two shards.
  `describe` and `seed._wanted` both read an empty list as "no country
  filter", which is what `config.example.yaml` documents. Against the build of
  2026-08-28 that printed `289,640 roles for AE, AR, ... US, 242MB`, read
  21,337, said `21,337 match your config`, stored them and exited 0. 93% of
  the seed was never fetched and no line said so.

* **A shard truncated on disk left the gzip module's EOFError uncaught.**
  `seed.load` guards its header `readline` against exactly this and its row
  loop does not, and `EOFError` is neither `OSError` nor `ValueError`, so a
  file cut off after row one arrived as a fifteen-frame traceback.

* **`Stored.` claimed "a day old at best" about anything.** The set is rebuilt
  weekly and `seed load ./seed` works on a directory of any age, so that
  sentence was a guess printed as a fact over roles that had been gone for
  months. A stale import looks identical to a fresh one: the roles are simply
  not on the boards any more, which is what an unpopular vacancy looks like.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import seed
from jobradar.cli import main
from jobradar.models import Job

DESC = ("We are hiring an engineering manager to lead a platform team. "
        "You will own delivery, hiring and the roadmap. " * 4)


def _jobs(plan: dict) -> list:
    """One Job per role, `plan` mapping shard name to how many."""
    out = []
    for shard, n in plan.items():
        for i in range(n):
            out.append(Job(
                company=f"Acme{shard}{i}", title="Engineering Manager",
                url=f"https://boards.example/{shard}/{i}", platform="greenhouse",
                location=f"City {shard}", city=f"City{shard}",
                # `unplaced` is a shard name and not a country code, so a role
                # that belongs in it is a role with no country at all.
                country="" if shard == "unplaced" else shard,
                work_mode="hybrid", sector="Technology",
                posted_at="2026-08-20", description=DESC))
    return out


PLAN = {"UK": 5, "US": 8, "DE": 3, "SG": 4, "AE": 2, "multiple": 6,
        "unplaced": 7}


@contextlib.contextmanager
def _published(plan=None, generated="2026-08-28"):
    """A shard set on disk, and `seed._http_get` serving it as if published.

    A real socket rather than a stub would be a live network test and a
    Windows firewall prompt in CI. What is under test is the command's own
    arithmetic about which shards it asked for, so the transport is the one
    part that can be faked without weakening the test: every byte still goes
    through `fetch`'s size check and `load`'s gzip reader.
    """
    root = Path(tempfile.mkdtemp())
    pub = root / "pub"
    seed.build(_jobs(plan or PLAN), pub, generated=generated, boards=999)
    real = seed._http_get
    served = {p.name: p.read_bytes() for p in pub.iterdir()}

    def fake(url, timeout=60):
        name = url.rsplit("/", 1)[-1]
        if name not in served:
            raise OSError(f"HTTP Error 404: {name}")
        return served[name]

    seed._http_get = fake
    try:
        yield root
    finally:
        seed._http_get = real
        shutil.rmtree(root, ignore_errors=True)


def _cfg(root: Path, countries="['UK']", relocate="[]") -> Path:
    cfg = root / "config.yaml"
    cfg.write_text(
        "titles:\n  include: ['engineering manager']\n"
        f"locations:\n  countries: {countries}\n"
        f"  relocate_to: {relocate}\n"
        "sources:\n  use_bundled: false\n",
        encoding="utf-8")
    return cfg


def _run(cfg, *argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["-c", str(cfg), *argv])
    return code, buf.getvalue()


def _run_no_cfg(*argv) -> tuple[int, str]:
    """The same, without `-c`, which is the case the default path got wrong."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


def _read_count(out: str) -> int:
    """The `N roles read` figure, which is what actually reached the screen."""
    for line in out.splitlines():
        if " roles read," in line:
            return int(line.split(" roles read,")[0].split()[-1].replace(",", ""))
    raise AssertionError(f"no 'roles read' line in:\n{out}")


def test_url_download_includes_the_relocation_countries():
    """countries: [DE] + relocate_to: [SG, AE] has to fetch SG and AE.

    It fetched neither, and then `seed.load` raised FileNotFoundError naming
    SG, because the index it had just written does list an SG shard. Exit 1,
    nothing stored, and a message telling the reader their download was
    corrupt when the only thing wrong was which shards this command asked for.
    """
    with _published() as root:
        cfg = _cfg(root, countries="['DE']", relocate="['SG', 'AE']")
        keep = root / "keep"
        code, out = _run(cfg, "seed", "load", "https://seed.example/base",
                         "--keep", str(keep), "--dry-run")
        assert code == 0, out
        for name in ("DE", "SG", "AE", "unplaced", "multiple"):
            assert (keep / f"{name}.jsonl.gz").exists(), f"{name} not fetched\n{out}"
        # 3 DE + 4 SG + 2 AE + 6 multiple + 7 unplaced.
        assert _read_count(out) == 22, out


def test_no_countries_never_announces_shards_it_did_not_fetch():
    """With no countries set, the summary must count what arrived.

    `fetch` takes `unplaced` and `multiple` and `describe` was handed the
    whole published index, so the announced total was every role in the set
    and the delivered total was 13 of 35. Nothing failed; the run stored 13
    roles under a line naming 35 and exited 0.
    """
    with _published() as root:
        cfg = _cfg(root, countries="[]")
        keep = root / "keep"
        code, out = _run(cfg, "seed", "load", "https://seed.example/base",
                         "--keep", str(keep), "--dry-run")
        assert code == 0, out
        read = _read_count(out)
        assert read == 13, out                       # multiple 6 + unplaced 7
        # No line may name a bigger number of roles than reached the reader.
        # The whole set is 35 and that figure was printed as though it had
        # been imported.
        assert "35 roles" not in out, out
        # And the gap is named rather than left to be inferred from a shard
        # list the reader has no way to compare against the index.
        assert "22 more roles" in out, out


def test_a_shard_cut_short_on_disk_is_a_sentence():
    """A truncated shard ends the run with a message, not an EOFError.

    The index's byte check only ever sees what came down the wire, so a file
    truncated after it landed -- a full disk, a killed copy, a bad USB stick --
    reaches `seed.load`, whose row loop catches `(ValueError, TypeError)`
    only. `EOFError` went straight past `cmd_seed_load`'s
    `except (OSError, ValueError)` and out of `main`.
    """
    with _published() as root:
        cfg = _cfg(root, countries="['UK']")
        pub = root / "pub"
        shard = pub / "UK.jsonl.gz"
        raw = shard.read_bytes()
        # Half a shard: the header line and the first rows decompress, and
        # then the stream stops without its end marker.
        shard.write_bytes(raw[: len(raw) // 2])
        code, out = _run(cfg, "seed", "load", str(pub), "--dry-run")
        assert code == 1, out
        assert "cut short" in out, out
        assert "Traceback" not in out, out


def test_stored_line_states_how_old_the_set_actually_is():
    """`Stored.` said "a day old at best" about a set of any age.

    The published set is rebuilt weekly and a local directory can be any age
    at all, so that sentence was true of one download and false of the rest.
    Roles that died months ago are not visibly different from roles nobody
    applied to.
    """
    built = date.today() - timedelta(days=88)
    with _published(generated=built.isoformat()) as root:
        cfg = _cfg(root, countries="['UK']")
        code, out = _run(cfg, "seed", "load", str(root / "pub"),
                         "--db", str(root / "x.db"))
        assert code == 0, out
        assert "88 days ago" in out, out
        assert "a day old at best" not in out, out


def test_an_undated_set_says_so_rather_than_reading_as_fresh():
    """No `generated` key is "we cannot say", never "built today".

    The two answers this repo keeps confusing, and the reassuring one is the
    wrong default: an index with no build date would have rendered as a set
    built this morning.
    """
    with _published() as root:
        pub = root / "pub"
        idx = json.loads((pub / "index.json").read_text(encoding="utf-8"))
        idx.pop("generated", None)
        (pub / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        cfg = _cfg(root, countries="['UK']")
        code, out = _run(cfg, "seed", "load", str(pub), "--db", str(root / "x.db"))
        assert code == 0, out
        assert "does not say when it was built" in out, out


def test_a_leftover_shard_is_named_rather_than_folded_into_a_total():
    """A shard from an earlier run under different countries gets read.

    `seed._wanted` globs the directory when no countries are configured, so a
    `US.jsonl.gz` left behind by last month's config is imported. That is
    defensible; announcing it out of the published index rather than off the
    disk was not, because the two lists differ by exactly the shards that were
    never downloaded.
    """
    with _published() as root:
        cfg = _cfg(root, countries="[]")
        keep = root / "keep"
        keep.mkdir()
        shutil.copy(root / "pub" / "US.jsonl.gz", keep / "US.jsonl.gz")
        code, out = _run(cfg, "seed", "load", "https://seed.example/base",
                         "--keep", str(keep), "--dry-run")
        assert code == 0, out
        assert _read_count(out) == 21, out           # 8 US + 6 multiple + 7 unplaced
        # Named off the disk: "21 roles for US, multiple, unplaced".
        assert "21 roles for US, multiple, unplaced" in out, out
        # 14 of the 35 are still unfetched: UK, DE, SG, AE.
        assert "14 more roles" in out, out


def test_the_release_web_page_is_named_as_the_wrong_url():
    """`/releases/tag/seed-latest` is the URL a person actually copies.

    It is the page the repository links to, it differs from the download base
    by two path segments, and GitHub answers it 200 with HTML rather than 404.
    So `index.json` under it parses as nothing and the run ended on
    "Expecting value: line 8 column 1 (char 9)", which names a JSON parser's
    disappointment and not the two segments the reader has to change.
    """
    root = Path(tempfile.mkdtemp())
    real = seed._http_get
    seed._http_get = lambda url, timeout=60: (
        b"<!doctype html>\n<html><body>Release seed-latest</body></html>")
    try:
        cfg = _cfg(root, countries="['UK']")
        code, out = _run(cfg, "seed", "load",
                         "https://github.com/maccydee/job-radar/releases/tag/"
                         "seed-latest", "--keep", str(root / "k"), "--dry-run")
        assert code == 1, out
        assert "/releases/download/seed-latest" in out, out
    finally:
        seed._http_get = real
        shutil.rmtree(root, ignore_errors=True)


def test_a_download_with_no_config_flag_lands_inside_the_working_directory():
    """No `-c` must not put the cache in the PARENT of where it was run.

    The default was `Path(args.config or ".").resolve().parent / "seed"`.
    `.parent` is right for a config file and wrong for the `"."` standing in
    for one, because `Path(".").resolve()` is already the working directory.
    So the download went one level up, into a directory the reader never
    named, and docs/SEED.md promises `seed/` beside the config.

    Two runs in sibling directories then shared one cache without either
    asking to. That is not just untidy: `seed._wanted` reads an empty
    `locations.countries` as "every shard on disk", so the next reader with no
    countries set imports whatever a neighbour happened to download, counted
    and stored as their own roles.
    """
    import os
    with _published() as root:
        work = root / "work"
        work.mkdir()
        _cfg(work, countries="['UK']").rename(work / "config.yaml")
        was = os.getcwd()
        os.chdir(work)
        try:
            # No `-c`, so the command finds ./config.yaml itself and the keep
            # directory has to come out beside it.
            code, out = _run_no_cfg("seed", "load", "https://seed.example/base",
                                    "--dry-run")
        finally:
            os.chdir(was)
        assert code == 0, out
        assert (work / "seed" / "UK.jsonl.gz").exists(), out
        assert not (root / "seed").exists(), \
            f"downloaded into the parent of the working directory\n{out}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
