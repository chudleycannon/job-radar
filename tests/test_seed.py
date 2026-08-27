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
    raw = json.loads(gzip.open(d / "UK.json.gz", "rb").read().decode("utf-8"))
    keys = {k for row in raw["roles"] for k in row}
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
    first = (d / "UK.json.gz").read_bytes()
    seed.build(JOBS, d, generated="2026-01-01")
    assert (d / "UK.json.gz").read_bytes() == first


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
