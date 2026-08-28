"""What the seed tells a reader whose country is barely in it, or not at all.

Found by setting up as a backend developer in Lisbon against the published
build of 2026-08-28, which holds 289,640 roles across 164 shards.

Portugal is 1,584 of those. `unplaced` and `multiple` are 21,337 between them
and go to every reader whatever country they set. `describe` added the three
together and announced "22,921 roles for PT, multiple, unplaced", so 93% of
the number put in front of a Portuguese reader was the part that would have
been there if they had said Norway, Nigeria or nothing at all. The same line
told a Norwegian reader 21,660 for a shard holding 323, and an Andorran
reader 21,338 for a shard holding exactly one.

The end of that scale is the bug this project keeps naming. A reader whose
country has no shard in the index got "21,337 roles for multiple, unplaced":
their country silently dropped from the list of names by the `k in want`
filter, and a healthy five-figure total where the one fact they needed should
have been. Nothing published for you rendered identically to plenty published
for you.

`describe` did carry an honest branch for that, `No prebuilt roles for ... in
this index`, and it could never fire. It tests the merged total, which is zero
only when `unplaced` and `multiple` are both empty, and `tools/refresh_seed.py`
refuses to publish a build that is missing either. The right message was
written and then wired to a condition that cannot happen.

Nor is an empty country shard hypothetical for a small one. `refresh_seed.py`
refuses a build for a missing shard only where that shard held 500 or more
roles the week before, so Norway at 323, Turkey at 334 and Finland at 434 can
each fall out of a build entirely and nothing declines to publish it.
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import seed             # noqa: E402
from jobradar.models import Job       # noqa: E402


def _job(i, country):
    return Job(company=f"Co{i}", title="Backend Engineer",
               url=f"https://boards.example/{i}", platform="ashby",
               description="d" * 300, country=country)


def _index(pt=3, unplaced=40, multiple=20, out=None):
    """A shard set shaped like the published one: a small home country and

    two ride-along shards that dwarf it. The published ratio is 1,584 against
    21,337; these are the same shape at a size a test can build.
    """
    d = Path(out or tempfile.mkdtemp())
    jobs = ([_job(i, "PT") for i in range(pt)]
            + [_job(1000 + i, None) for i in range(unplaced)]
            + [_job(2000 + i, "multiple") for i in range(multiple)])
    return d, seed.build(jobs, d, generated="2026-08-28")


def test_pt_describe_counts_portugal_apart_from_the_ride_along_shards():
    """1,584 Portuguese roles and 21,337 that go to everybody is a different

    fact from "22,921 roles", and it is the fact the reader is deciding on.
    """
    _, idx = _index(pt=3, unplaced=40, multiple=20)
    line = seed.describe(idx, ["PT"])
    assert "3 in PT" in line, line
    # The ride-alongs are still named, because they are still coming and the
    # reader is paying for them. They are just not counted as Portuguese.
    assert "60 unplaced" in line, line
    # The merged total was the whole of the old claim. It must not be the
    # headline number any more.
    assert "63 roles for" not in line, line


def test_pt_describe_names_a_country_with_no_shard_at_all():
    """The core failure: a reader whose country is absent was handed the two

    everybody-shards and a cheerful total, with their own country dropped
    from the sentence by the filter that builds it.
    """
    _, idx = _index(pt=0, unplaced=40, multiple=20)
    line = seed.describe(idx, ["GI"])
    assert "GI" in line, f"the reader's own country is not in the line: {line}"
    assert "Nothing published" in line, line
    assert not line.startswith("60 roles"), line


def test_pt_describe_still_names_the_absent_country_beside_a_present_one():
    """Tomas sets `countries: [PT]` and `relocate_to: [GI]`. Portugal having a

    shard must not swallow the news that the other one has none.
    """
    _, idx = _index(pt=3, unplaced=40, multiple=20)
    line = seed.describe(idx, ["PT", "GI"])
    assert "GI" in line, f"the absent country is not in the line: {line}"
    assert "3 in PT" in line, line


def _serve(files):
    def get(url, timeout=60):
        name = url.rsplit("/", 1)[-1]
        if name not in files:
            raise OSError(f"404 {name}")
        return files[name]
    return get


def test_pt_fetch_summary_says_when_the_country_has_no_shard():
    """The download summary is the FIRST line a new reader sees, and it ran

    the same merge. A reader whose country is absent was told
    "21,337 roles in 2 shard(s), 22MB, built 2026-08-28", which is precisely
    what a good download looks like.
    """
    d, _ = _index(pt=0, unplaced=40, multiple=20)
    files = {p.name: p.read_bytes() for p in d.iterdir()}
    said = []
    with mock.patch.object(seed, "_http_get", _serve(files)):
        seed.fetch("https://example.invalid/seed", ["GI"],
                   Path(tempfile.mkdtemp()), say=said.append)
    joined = "\n".join(said)
    assert "GI" in joined, f"nothing named the reader's own country:\n{joined}"
    assert "Nothing published" in joined, joined


def test_pt_fetch_summary_splits_the_home_country_from_the_ride_alongs():
    """Same split as `describe`, because the reader is being asked to spend

    23MB on it and 93% of the old number was the part everybody gets.
    """
    d, _ = _index(pt=3, unplaced=40, multiple=20)
    files = {p.name: p.read_bytes() for p in d.iterdir()}
    said = []
    with mock.patch.object(seed, "_http_get", _serve(files)):
        seed.fetch("https://example.invalid/seed", ["PT"],
                   Path(tempfile.mkdtemp()), say=said.append)
    joined = "\n".join(said)
    assert "3 roles in PT" in joined, joined
    assert "63 roles in" not in joined, joined
