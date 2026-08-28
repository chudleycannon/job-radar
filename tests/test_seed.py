"""The prebuilt index of the slow half of a scan.

A full scan is four passes. Pass one is nine thousand fast sources and takes
about five minutes; passes two to four are 8,780 employer boards with
`apply.workable.com` paced at 0.7 requests a second, which is a fifty minute
floor no concurrency moves. So the slow passes are what is worth shipping
ahead of time and the fast pass is what is not, because a new user can have
that themselves, fresher, in five minutes.

The tests that matter here are the ones about what a reader is handed. A seed
that quietly omits a role is indistinguishable, from the reader's side, from
a job that does not exist.
"""
import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import seed                       # noqa: E402
from jobradar.models import Job, Salary         # noqa: E402


def _job(**kw):
    kw.setdefault("company", "Acme")
    kw.setdefault("title", "Engineering Manager")
    kw.setdefault("url", "https://boards.example/j/1")
    kw.setdefault("platform", "ashby")
    return Job(**kw)


def _tmp():
    return Path(tempfile.mkdtemp())


JOBS = [
    _job(url="https://x/1", country="UK", location="London, UK",
         description="d" * 400,
         salary=Salary(min=140000, max=160000, currency="GBP",
                       period="year", confirmed=True)),
    _job(url="https://x/2", country="DE", location="Berlin", company="Beta",
         description="e" * 400),
    _job(url="https://x/3", country=None, location="", company="Ceta",
         description="f" * 400),
    _job(url="https://x/4", country="multiple", company="Deta",
         location="London / New York", description="g" * 400),
]


def test_a_uk_reader_gets_uk_unplaced_and_multiple_and_not_germany():
    d = _tmp(); seed.build(JOBS, d)
    got = {j.company for j in seed.load(d, ["UK"])}
    assert got == {"Acme", "Ceta", "Deta"}, got


def test_a_role_open_in_several_countries_reaches_everyone():
    """The bug the first cut shipped.

    "multiple" is the screening logic's answer for a role open in more than
    one country. Given a shard of its own and handed only to readers who ask
    for a country called "multiple", which is nobody, it dropped a role open
    in London and New York out of every UK download: 127 of 3,060 roles on a
    120-board test. It renders as the role not existing.
    """
    assert "multiple" in seed.shards_for(["UK"])
    assert "multiple" in seed.shards_for([])
    d = _tmp(); seed.build(JOBS, d)
    assert "Deta" in {j.company for j in seed.load(d, ["ZZ"])}


def test_a_role_that_could_not_be_placed_reaches_everyone_too():
    assert seed.UNPLACED in seed.shards_for(["UK"])
    d = _tmp(); seed.build(JOBS, d)
    assert "Ceta" in {j.company for j in seed.load(d, ["ZZ"])}


def test_no_role_is_in_two_shards():
    """Overlap would double-count in the index and re-import roles twice."""
    d = _tmp(); idx = seed.build(JOBS, d)
    assert sum(v["roles"] for v in idx["shards"].values()) == len(JOBS)


def test_every_role_lands_in_some_shard():
    d = _tmp(); seed.build(JOBS, d)
    every = {c for name in ("UK", "DE", "ZZ") for c in
             (j.company for j in seed.load(d, [name]))}
    assert every == {j.company for j in JOBS}


def test_the_uid_survives_the_round_trip():
    """Everything downstream keys on it: dedup, status, artifacts.

    A seed whose roles come back with different uids would re-alert every
    role on the next scan and orphan every CV already written against one.
    """
    d = _tmp(); seed.build(JOBS, d)
    back = {j.uid for j in seed.load(d, ["UK", "DE"])}
    assert {j.uid for j in JOBS} <= back | {JOBS[1].uid}


def test_the_description_and_a_confirmed_salary_survive():
    d = _tmp(); seed.build(JOBS, d)
    j = next(x for x in seed.load(d, ["UK"]) if x.company == "Acme")
    assert len(j.description) == 400
    assert (j.salary.min, j.salary.max, j.salary.currency) == (140000, 160000, "GBP")
    assert j.salary.confirmed is True


def test_a_role_with_no_salary_does_not_come_back_claiming_one():
    d = _tmp(); seed.build(JOBS, d)
    j = next(x for x in seed.load(d, ["DE"]) if x.company == "Beta")
    assert j.salary.confirmed is False
    assert j.salary.min is None and j.salary.max is None


def test_the_seed_carries_no_answer_that_belongs_to_a_config():
    """A seed is a saved fetch, not a saved decision.

    score, fit and reasons are answers to a question only somebody's own
    config asks, and shipping them would put whoever built the file in
    everybody else's search.
    """
    d = _tmp(); seed.build(JOBS, d)
    lines = gzip.open(d / "UK.jsonl.gz", "rt", encoding="utf-8").read().splitlines()
    keys = {k for line in lines[1:] for k in json.loads(line)}
    for banned in ("score", "fit", "reasons", "app_status"):
        assert banned not in keys
    for j in seed.load(d, ["UK"]):
        assert j.score == 0 and j.reasons == []


def test_a_file_from_a_future_format_is_refused_not_guessed_at():
    d = _tmp(); seed.build(JOBS, d)
    idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
    idx["schema"] = seed.SCHEMA + 1
    (d / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    try:
        seed.read_index(d)
    except ValueError as exc:
        assert "format" in str(exc)
    else:
        raise AssertionError("a newer format was read as if it were this one")


def test_a_shard_that_is_absent_is_not_an_error():
    """"No roles in Portugal" and "no Portugal shard" are the same fact."""
    d = _tmp(); seed.build(JOBS, d)
    assert list(seed.load(d, ["PT"])) or True
    assert not [j for j in seed.load(d, ["PT"]) if j.country == "PT"]


def test_rebuilding_an_unchanged_set_produces_identical_bytes():
    """So a shard set can be published without every file looking changed."""
    d = _tmp()
    seed.build(JOBS, d, generated="2026-01-01")
    first = (d / "UK.jsonl.gz").read_bytes()
    seed.build(JOBS, d, generated="2026-01-01")
    assert (d / "UK.jsonl.gz").read_bytes() == first


def test_build_writes_nothing_outside_the_directory_it_was_given():
    d = _tmp()
    before = sorted(p.name for p in d.iterdir())
    seed.build(JOBS, d / "inner")
    assert sorted(p.name for p in d.iterdir()) == sorted(before + ["inner"])
    assert not list((d / "inner").glob("*.tmp")), "a temp file was left behind"


def test_describe_says_the_age_and_does_not_promise_freshness():
    d = _tmp(); idx = seed.build(JOBS, d, generated="2026-01-01")
    line = seed.describe(idx, ["UK"])
    assert "2026-01-01" in line
    assert "scan" in line.lower()


def test_remote_false_is_not_confused_with_remote_unknown():
    """The falsy-value trap in `_pack`.

    `remote` is True, False or None, and those are three different answers:
    False means the advert says office, None means it did not say. Omitting
    every falsy value from the packed row would turn "office" into "we do not
    know", which is the difference between a role a remote-only reader should
    never see and one they should see with a caveat.
    """
    d = _tmp()
    seed.build([
        _job(url="https://x/a", company="A", country="UK",
             remote=False, work_mode="office"),
        _job(url="https://x/b", company="B", country="UK",
             remote=True, work_mode="remote"),
        _job(url="https://x/c", company="C", country="UK", remote=None),
    ], d)
    got = {j.company: (j.remote, j.work_mode) for j in seed.load(d, ["UK"])}
    assert got["A"] == (False, "office")
    assert got["B"] == (True, "remote")
    assert got["C"] == (None, "unstated")


def test_a_shard_is_read_one_role_at_a_time_not_parsed_whole():
    """A single JSON array has to be parsed whole before the first role is
    available. A 35,000-role shard cost 360MB of resident memory that way,
    and the US shard is eight times that. One object per line means the
    reader holds one role and the caller decides what to keep."""
    d = _tmp(); seed.build(JOBS, d)
    text = gzip.open(d / "UK.jsonl.gz", "rt", encoding="utf-8").read()
    lines = [l for l in text.splitlines() if l.strip()]
    assert json.loads(lines[0])["schema"] == seed.SCHEMA, "no header line"
    assert len(lines) > 1
    for line in lines[1:]:
        json.loads(line)          # every line stands alone
    import types
    assert isinstance(seed.load(d, ["UK"]), types.GeneratorType)


def test_an_empty_shard_file_is_refused_rather_than_read_as_no_roles():
    """A shard that exists is one somebody meant to publish. Reading nothing
    out of a truncated file, silently, is the failure this project keeps
    finding: it renders exactly like a country with no vacancies."""
    d = _tmp(); seed.build(JOBS, d)
    (d / "UK.jsonl.gz").write_bytes(gzip.compress(b""))
    try:
        list(seed.load(d, ["UK"]))
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("a truncated shard read as an empty one")


def test_a_shard_the_index_promises_but_does_not_ship_is_an_error():
    """The failure a real first-run hit.

    The shard extension changed and the schema number did not, so the index
    was accepted, every file was missing under its new name, and `load`
    skipped them all in silence. The run printed the shard sizes it had read
    out of the index and then "Nothing in this index for your countries.
    Config locations.countries", blaming the reader for an incomplete file.
    """
    d = _tmp(); seed.build(JOBS, d)
    (d / "UK.jsonl.gz").unlink()
    try:
        list(seed.load(d, ["UK"]))
    except FileNotFoundError as exc:
        assert "UK" in str(exc) and "ebuild" in str(exc)
    else:
        raise AssertionError("a promised shard went missing without a word")


def test_a_country_the_index_never_mentions_is_still_quiet():
    """"No roles in Portugal" is not an error, it is an answer."""
    d = _tmp(); seed.build(JOBS, d)
    assert [j.company for j in seed.load(d, ["PT"])]  # unplaced + multiple


def test_the_raw_pay_string_survives_so_the_reader_does_not_print_none():
    """`score` interpolates `salary.raw` into a reason it shows the user.

    The seed carried the numbers and not the string, so ten of nineteen
    priced roles came back reading "pay stated (None)" on the dashboard and
    in `list --json`, next to a perfectly good "$165k - $185k" label.
    """
    d = _tmp()
    seed.build([_job(url="https://x/p", country="UK",
                     salary=Salary(min=140000, max=160000, currency="GBP",
                                   confirmed=True,
                                   raw="£140,000 - £160,000 per annum"))], d)
    j = next(iter(seed.load(d, ["UK"])))
    assert j.salary.raw == "£140,000 - £160,000 per annum"


def test_a_shard_written_without_the_raw_string_still_loads():
    """Appended to the packed list rather than inserted, so a shard set built
    before this change is read rather than refused."""
    from jobradar.seed import _unpack
    old = {"c": "Acme", "t": "EM", "u": "https://x/1", "p": "ashby",
           "$": [140000, 160000, "GBP", "year", 1]}
    j = _unpack(old)
    assert j.salary.min == 140000 and j.salary.raw is None


def test_a_published_seed_carries_nobody_s_own_boards():
    """`seed build` reads the bundled file, not `sources.load(cfg)`.

    Through the config it would have picked up `sources.extra`, and on the
    machine this was written on that was four boards: Seamflow, Balbix,
    Intel471 and Dropzone. That is not a list of employers, it is a list of
    the companies the person running the command has been applying to, and a
    public file is the wrong place to say so. A `sectors` or
    `sources.countries` setting would have narrowed it to one person's search
    while the index went on describing itself as the slow half of the scan.
    """
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_seed_build)
    assert "load_file(src_mod.BUNDLED)" in src, \
        "seed build is reading sources through somebody's config again"
    assert "src_mod.load(cfg)" not in src
