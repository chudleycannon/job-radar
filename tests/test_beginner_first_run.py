"""What a first-time reader meets, following README.md literally.

These come from an actual beginner run: a career changer with no coding
background, three project-management titles, no salary floor and no
dealbreakers, doing `setup --defaults`, then `seed load <url>`, then
`scan --limit 200`, then `serve`.

The shape they guard against is the one CLAUDE.md names: a step that
finishes, says something confident, and is wrong. A beginner does not debug.
A wrong sentence at this point is the whole product.
"""

import contextlib
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import sources as src_mod  # noqa: E402
from jobradar import store  # noqa: E402
from jobradar.cli import main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SEED_MD = (ROOT / "docs" / "SEED.md").read_text(encoding="utf-8")


@contextlib.contextmanager
def _in(dirpath):
    """Run from a scratch directory, the way a reader runs from their own."""
    was = os.getcwd()
    os.chdir(dirpath)
    try:
        yield
    finally:
        os.chdir(was)


def _run(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


def _seed_section() -> str:
    """The README block the seed release is advertised in."""
    start = README.index("## Skip the slow hour")
    return README[start:README.index("\n---", start)]


SEED_URL = ("https://github.com/maccydee/job-radar/releases/"
            "download/seed-latest")


def test_seed_load_without_a_config_refuses_before_it_downloads():
    """Tens of megabytes fetched for a run that cannot use them.

    `seed load` screens against the config, so with no config there is
    nothing it can do with the download. Finding that out after the download
    costs a beginner 38MB, and 242MB if their countries are wider. The
    refusal has to come first, and it has to name `setup` rather than say
    "no config" and stop.
    """
    with tempfile.TemporaryDirectory() as d, _in(d):
        code, out = _run("seed", "load", SEED_URL)
        assert code != 0 or "config" in out.lower(), (code, out)
        assert "setup" in out, out
        # Nothing was written: no seed directory, no database, and nothing
        # in the parent either, which is where the default `--keep` lands.
        assert list(Path(d).iterdir()) == [], sorted(
            p.name for p in Path(d).iterdir())


def test_the_seed_section_names_the_platforms_the_seed_actually_holds():
    """"The slow half" says nothing to somebody who has never run a scan.

    The published file is passes 2 to 4 and nothing else. A reader deciding
    whether it covers their field needs the platform names, not a fraction,
    and the names have to come from `sources.PHASES` rather than from
    somebody's memory of it.
    """
    labels = [label for num, label, _ in src_mod.PHASES if num > 1]
    assert labels, "PHASES no longer has a slow phase"
    block = _seed_section()
    for label in labels:
        word = label.split("'")[0].split()[0]    # Ashby, Greenhouse, Workable
        assert word in block, (
            f"the README seed section never names {word}, which is one of "
            f"the passes the published file is made of")


def test_the_seed_section_says_a_scan_is_still_needed():
    """A beginner who reads this as "instead of a scan" stops there.

    Roles die in days and the fast pass is absent entirely, so a seed-only
    user has a stale, partial list that looks complete. Both facts have to
    survive any rewrite of this section.
    """
    block = _seed_section().lower()
    assert "does not replace" in block or "not a scan" in block, block
    assert "run one anyway" in block or "run a scan anyway" in block, block


def test_the_dry_run_flag_does_not_promise_a_free_look():
    """`--dry-run` downloads the shards. It has to: screening means reading.

    Documented as "without writing" it reads as a way to preview the cost of
    the download, and a reader on a metered connection pays 38MB to find out
    otherwise. Measured: `seed load <url> --dry-run` left 37MB on disk under
    the sentence "Dry run, so nothing was written."
    """
    for name, text in (("README.md", README), ("docs/SEED.md", SEED_MD)):
        if "--dry-run" not in text:
            continue
        i = text.index("--dry-run")
        near = text[max(0, i - 200):i + 400].lower()
        assert "still download" in near or "downloads" in near, (
            f"{name} describes --dry-run without saying it still downloads")


def test_the_readme_does_not_claim_new_means_since_the_last_scan():
    """`--new` is keyed on a DATE, not on a run.

    `store.NEW_SQL` is `first_seen = (SELECT MAX(last_seen) FROM roles)`, and
    `first_seen` is an ISO date. Two scans in one day, or the seed-then-scan
    sequence this README recommends, land in the same bucket: the beginner
    run had `list --new` return all 238 roles a minute after a scan that
    reported 3. Until it is keyed on a run, the README must not say otherwise.
    """
    assert "first_seen" in store.NEW_SQL, (
        "NEW_SQL changed; recheck what --new is documented to mean")
    i = README.index("job-radar list --new")
    line = README[i:README.index("\n", i)]
    assert "since the last scan" not in line, (
        "README promises --new is per-scan; it is per-day: " + line)


def test_the_salary_share_is_not_stated_as_one_number_for_everywhere():
    """"Around a third of postings state one" is a US figure.

    Measured on the published seed of 2026-08-28: 34.0% of the 151,044 US
    rows carry a figure, against 19.3% of the 41,038 a UK reader downloads
    (Greenhouse 18.6%, Ashby 25.4%, Workable 11.2%). A UK reader told to
    expect a third sets a salary floor against a market that states pay one
    time in five, and then wonders why the list is all `unconfirmed salary`.
    """
    i = README.index("Salary you can filter on")
    block = README[i:i + 700]
    assert "%" in block, (
        "the salary caveat gives one global fraction and no spread; a UK "
        "reader and a US reader do not see the same market")


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  pass  {_name}")
            except AssertionError as _e:
                failed += 1
                print(f"  FAIL  {_name}: {_e}")
    raise SystemExit(1 if failed else 0)
