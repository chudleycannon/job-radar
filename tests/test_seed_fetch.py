"""Downloading a published shard set, and the ways that can go quietly wrong.

Publishing without this would be half a feature. The shards would exist and
every reader would have to find fifty release assets and work out which three
of them they need; the whole point of sharding is that a UK reader takes 27MB
rather than 181MB, and only the tool knows which three that is.

The guard that matters is the short read. A truncated shard decompresses and
parses perfectly well as a shard with fewer roles in it, and the roles that
fell off the end look exactly like jobs that do not exist.
"""
import gzip
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import seed             # noqa: E402
from jobradar.models import Job       # noqa: E402


def _published():
    """A shard set, as bytes, keyed by the path it would be served at."""
    d = Path(tempfile.mkdtemp())
    seed.build([Job(company=f"Co{i}", title="Engineer", url=f"https://b/{i}",
                    platform="ashby", description="d" * 300,
                    country=("UK", "US", "DE", "multiple", None)[i % 5])
                for i in range(50)], d)
    return {p.name: p.read_bytes() for p in d.iterdir()}


def _serve(files, log=None):
    def get(url, timeout=60):
        name = url.rsplit("/", 1)[-1]
        if log is not None:
            log.append(name)
        if name not in files:
            raise OSError(f"404 {name}")
        return files[name]
    return get


def test_only_the_shards_this_reader_needs_are_downloaded():
    files, log = _published(), []
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files, log)):
        seed.fetch("https://example.invalid/seed", ["UK"], out)
    assert sorted(p.name for p in out.iterdir()) == [
        "UK.jsonl.gz", "index.json", "multiple.jsonl.gz", "unplaced.jsonl.gz"]
    assert "US.jsonl.gz" not in log and "DE.jsonl.gz" not in log


def test_a_short_read_is_refused_rather_than_stored():
    """The failure this project keeps finding. A truncated shard parses as a
    shorter one, and the roles that fell off look like jobs that do not
    exist."""
    files = _published()
    files["UK.jsonl.gz"] = files["UK.jsonl.gz"][:20]
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files)):
        try:
            seed.fetch("https://example.invalid/seed", ["UK"], out)
        except ValueError as exc:
            assert "Refusing a partial shard" in str(exc)
        else:
            raise AssertionError("a truncated shard was accepted")
    assert not (out / "UK.jsonl.gz").exists(), "the partial file was kept"


def test_the_index_is_written_last_so_it_never_promises_a_missing_shard():
    """`load` raises when the index lists a shard that is not on disk, which
    is the right answer to an interrupted download only if the index is not
    there yet when it is interrupted."""
    files = _published()
    files["multiple.jsonl.gz"] = files["multiple.jsonl.gz"][:5]
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files)):
        try:
            seed.fetch("https://example.invalid/seed", ["UK"], out)
        except ValueError:
            pass
    assert not (out / "index.json").exists(), \
        "an index was left behind promising a shard that never arrived"


def test_a_second_run_does_not_download_what_it_already_has():
    files, log = _published(), []
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files)):
        seed.fetch("https://example.invalid/seed", ["UK"], out)
    with mock.patch.object(seed, "_http_get", _serve(files, log)):
        seed.fetch("https://example.invalid/seed", ["UK"], out)
    assert log == ["index.json"], log


def test_a_shard_set_from_a_future_format_is_refused_before_anything_is_read():
    files = _published()
    idx = json.loads(files["index.json"].decode("utf-8"))
    idx["schema"] = seed.SCHEMA + 1
    files["index.json"] = json.dumps(idx).encode("utf-8")
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files)):
        try:
            seed.fetch("https://example.invalid/seed", ["UK"], out)
        except ValueError as exc:
            assert "format" in str(exc)
        else:
            raise AssertionError("a newer format was downloaded anyway")
    assert not list(out.glob("*.gz"))


def test_a_country_with_nothing_published_is_not_an_error():
    files = _published()
    out = Path(tempfile.mkdtemp())
    said = []
    with mock.patch.object(seed, "_http_get", _serve(files)):
        seed.fetch("https://example.invalid/seed", ["JP"], out, say=said.append)
    # JP has no shard, but `unplaced` and `multiple` always come.
    assert (out / "unplaced.jsonl.gz").exists()


def test_what_arrives_is_readable_by_load():
    files = _published()
    out = Path(tempfile.mkdtemp())
    with mock.patch.object(seed, "_http_get", _serve(files)):
        seed.fetch("https://example.invalid/seed", ["UK"], out)
    got = list(seed.load(out, ["UK"]))
    assert got and all(j.company and j.url for j in got)


def test_the_download_carries_no_identifier_beyond_a_user_agent():
    """A seed download says nothing about who is asking or what they are
    looking for, and it should stay that way."""
    import inspect
    src = inspect.getsource(seed._http_get)
    for leak in ("cookie", "token", "auth", "config", "titles", "email"):
        assert leak not in src.lower(), leak
