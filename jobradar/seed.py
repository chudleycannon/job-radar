"""A prebuilt index of the slow half of the scan, so a new user is not idle.

A full scan reads its sources in four passes. The first pass is nine thousand
fast ones and finishes in about five minutes. The other three are Ashby,
Greenhouse and Workable's own boards -- 8,779 employer boards, one
request each, and `apply.workable.com` is paced at 0.7 requests a second
because it answered a burst with a sixteen hour refusal. That floor is fifty
minutes and no amount of concurrency moves it.

So the slow passes are exactly what is worth shipping ahead of time, and the
fast pass is exactly what is not: a new user can have that themselves in five
minutes, fresher than any file we could publish.

**Descriptions are in.** Without one, `rank` refuses to score a role (it wants
200 characters), no dealbreaker can be checked against it, and no CV can be
written from it. An index of titles and links would be a list, not a tool.

**Sharded by country**, because the whole world with adverts attached is 242MB
and nobody wants it. A UK reader takes the UK shard plus two that everybody
takes: roles whose country could not be read, and roles open in several
countries at once. That is 38MB together, measured on the build of
2026-08-28. Neither of those is evidence
that a role is somewhere else, and dropping them would silently hide real
vacancies -- a role open in London and New York is a UK role.

**Nothing here is trusted as screened.** A seed is a saved fetch, not a saved
decision. What comes out of `load` is the same `Job` objects the adapters
produce, and the caller runs them through the same screening a scan does, so
somebody else's config can never leak into yours. That is also why the seed
holds no score, no fit and no reasons: those are answers to a question only
your config asks.

What it does NOT do, said plainly:

* It does not make a scan faster. There is no "changed since" parameter on
  any of these board APIs, so a scan still reads every board. What the seed
  changes is that the first hour is spent reading a dashboard rather than
  watching a counter.
* It does not replace a scan. Roles die in days and the file is a day old at
  best, so a scan runs anyway and its answer wins on every field.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

from .models import Job, Salary

# Bumped when the shard format changes in a way an older reader would
# misread. A reader that does not recognise the number refuses the file
# rather than guessing at it: half-understood roles are worse than none,
# because they look like roles.
SCHEMA = 1

# Roles whose country could not be read. They go in every reader's download,
# so this is a shard name rather than a country code, and it is deliberately
# not a valid ISO code so it cannot collide with one.
UNPLACED = "unplaced"

# The screening logic answers "multiple" for a role open in more than one
# country, and that is not a country either. A first cut gave it a shard of
# its own and handed readers only their own country plus `unplaced`, which
# quietly dropped a role open in London and New York from every UK download:
# 127 roles out of 3,060 on a 120-board test. Both of these ship with every
# shard set, because neither is evidence that the role is somewhere else.
EVERYWHERE = (UNPLACED, "multiple")

# Keys are one or two characters because they repeat once per role, and at
# a quarter of a million roles the difference is real. Nothing else about the
# format is optimised: it is JSON so that a person can read it.
_FIELDS = (
    ("c", "company"), ("t", "title"), ("u", "url"), ("p", "platform"),
    ("l", "location"), ("y", "city"), ("o", "country"), ("w", "work_mode"),
    ("s", "sector"), ("e", "department"), ("a", "posted_at"),
    ("d", "description"), ("r", "remote"),
)


def _pack(j: Job) -> dict:
    out = {}
    for short, attr in _FIELDS:
        v = getattr(j, attr, None)
        # Omit rather than write null. Absent and null mean the same thing to
        # `_unpack`, and omitting is about 8% of the compressed size.
        if v not in (None, "", "unstated"):
            out[short] = v
    s = j.salary
    if s and (s.min is not None or s.max is not None or s.confirmed):
        # `raw` is carried, sixth, because the reader prints it. Without it
        # every seeded role with a price came back with `salary.raw = None`,
        # and `score` interpolated that straight into a reason: ten of
        # nineteen priced roles read "pay stated (None)" on the dashboard and
        # in `list --json`, beside a perfectly good "$165k - $185k" label.
        # Appended rather than inserted, so an older reader ignores it.
        out["$"] = [s.min, s.max, s.currency, s.period,
                    1 if s.confirmed else 0, s.raw]
    return out


def _unpack(d: dict) -> Job:
    kw = {attr: d[short] for short, attr in _FIELDS if short in d}
    kw.setdefault("company", "")
    kw.setdefault("title", "")
    kw.setdefault("url", "")
    kw.setdefault("platform", "")
    sal = d.get("$")
    if sal:
        kw["salary"] = Salary(min=sal[0], max=sal[1], currency=sal[2],
                              period=sal[3] or "year", confirmed=bool(sal[4]),
                              raw=sal[5] if len(sal) > 5 else None)
    return Job(**kw)


def shard_of(j: Job) -> str:
    """Which shard a role belongs in. One only: shards must not overlap.

    A role open in several countries is placed by whatever `country` the
    screening logic settled on, including its "multiple" answer, which gets a
    shard of its own for the same reason `unplaced` does.
    """
    c = (j.country or "").strip()
    return c if c and c != "?" else UNPLACED


def shards_for(countries: Iterable[str]) -> list[str]:
    """The shards a reader in these countries needs.

    Always includes `unplaced` and `multiple`. A role the generator could not
    place might still be down the road from you, and a role open in several
    countries might be open in yours. A job search that silently hides either
    is worse than one that shows a few from elsewhere, which the reader's own
    screening then drops anyway.
    """
    want = [c.strip() for c in countries if (c or "").strip()]
    return list(dict.fromkeys(want + list(EVERYWHERE)))


class Writer:
    """Builds a shard set one role at a time.

    Separate from `build` because the fetch that feeds it is blocking: a scan
    hands results to a callback as they arrive, and there is no point
    streaming inside `build` if the caller has already accumulated a quarter
    of a million adverts to pass it. A quarter of a million is around 1.7GB of
    text before any Python object overhead, and a build that dies at minute
    seventy of a seventy-seven minute fetch has cost more than it saved.

    Rows go into a plain per-shard file as they arrive and each is compressed
    once at the end, so what is held is one row. Plain rather than gzipped on
    the way in because gzip members do not concatenate into one stream
    cheaply, and re-reading to compress is a sequential pass over a file we
    already have.
    """

    def __init__(self, out_dir: str | Path):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self._parts: dict = {}
        self.counts: dict[str, int] = {}

    def add(self, job: Job) -> None:
        name = shard_of(job)
        fh = self._parts.get(name)
        if fh is None:
            fh = self._parts[name] = open(self.out / f"{name}.part", "w",
                                          encoding="utf-8")
            self.counts[name] = 0
        fh.write(json.dumps(_pack(job), separators=(",", ":"),
                            ensure_ascii=False) + "\n")
        self.counts[name] += 1

    def close(self) -> None:
        for fh in self._parts.values():
            fh.close()
        self._parts.clear()

    def finish(self, *, generated: str | None = None, boards: int = 0,
               note: str = "") -> dict:
        self.close()
        index = {
            "schema": SCHEMA,
            "generated": generated or date.today().isoformat(),
            "boards": boards,
            "note": note or ("slow-phase employer boards only: Ashby, "
                             "Greenhouse and apply.workable.com. Run a scan "
                             "for the rest."),
            "shards": {},
        }
        for name in sorted(self.counts):
            part = self.out / f"{name}.part"
            path = self.out / f"{name}.jsonl.gz"
            # Written whole then renamed, like everything else here that is
            # not cheaply regenerable, so a reader can never open a
            # half-written file and take it for a short one. mtime=0 so
            # rebuilding an unchanged shard produces an identical file rather
            # than one that merely looks changed.
            tmp = self.out / f"{name}.tmp"
            header = json.dumps({"schema": SCHEMA, "shard": name,
                                 "roles": self.counts[name]},
                                separators=(",", ":"))
            with gzip.GzipFile(tmp, "wb", compresslevel=9, mtime=0) as gz:
                gz.write((header + "\n").encode("utf-8"))
                with open(part, "rb") as src:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        gz.write(chunk)
            tmp.replace(path)
            part.unlink()
            index["shards"][name] = {"roles": self.counts[name],
                                     "bytes": path.stat().st_size}
        tmp = self.out / "index.tmp"
        tmp.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.out / "index.json")
        return index


def build(jobs: Iterable[Job], out_dir: str | Path, *,
          generated: str | None = None, boards: int = 0,
          note: str = "") -> dict:
    """Write one gzipped shard per country, plus an index describing them.

    Returns the index. Writes nothing anywhere but `out_dir`.
    """
    w = Writer(out_dir)
    try:
        for j in jobs:
            w.add(j)
    finally:
        w.close()
    return w.finish(generated=generated, boards=boards, note=note)


# Fetching a published shard set.
#
# Publishing without this would be half a feature: the file would exist and
# every reader would have to find fifty release assets and work out which
# three of them they need. The point of sharding is that a UK reader takes
# 39MB rather than 240MB, and only the tool knows which shards that is.
#
# Deliberately plain HTTPS with no authentication and no client identifier
# beyond a user agent. A seed download says nothing about who is asking or
# what they are looking for, and it should stay that way.
_MAX_BODY = 8 << 20          # nothing promises the index a size
_MAX_ROW = 1 << 20           # no advert is a megabyte


def _http_get(url: str, timeout: int = 60, cap: int = _MAX_BODY) -> bytes:
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "job-radar seed fetch "
                                    "(+https://github.com/maccydee/job-radar)"})
    # Read in chunks with a ceiling rather than `r.read()`.
    #
    # A 389KB shard whose single row decompresses to 400MB pinned a process
    # for ten minutes. The index's byte count is the only size anybody has
    # promised us, so a body running well past it is not the file we asked
    # for and there is no reason to keep reading it. `_MAX_BODY` is the
    # backstop for the index itself, which nothing has promised a size for.
    #
    # It still returns bytes rather than streaming to disk, because
    # `_http_get` is the one seam the tests substitute and a shard is at most
    # 112MB, which is transient and survivable. Progress is reported per
    # shard by `fetch` before each one starts.
    out = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(1 << 18)
            if not chunk:
                break
            out += chunk
            if len(out) > cap:
                raise ValueError(
                    f"{url.rsplit('/', 1)[-1]} is still arriving after "
                    f"{len(out):,} bytes, past the {cap:,} this was told to "
                    f"expect. Refusing to read an unbounded file.")
    return bytes(out)


def fetch(base_url: str, countries: Iterable[str], dest: str | Path,
          say=None) -> dict:
    """Download the index and only the shards these countries need.

    `base_url` is the directory the shard set was published under, so the
    index sits at `<base_url>/index.json` and each shard beside it.

    Returns the index. Downloads nothing it already has: a shard whose size
    on disk matches the index is left alone, so re-running after a dropped
    connection resumes rather than starting again. That is a size check and
    not a checksum, which would be better; the index does not carry one yet
    and inventing one here would be a format change made in the wrong place.
    """
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    base = base_url.rstrip("/")
    say = say or (lambda _m: None)

    raw = _http_get(f"{base}/index.json")
    idx = json.loads(raw.decode("utf-8"))
    _check(idx.get("schema"), f"{base}/index.json")

    shards = idx.get("shards") or {}
    want = [n for n in shards_for(countries) if n in shards]
    if not want:
        say(f"Nothing published for {', '.join(shards_for(countries))}.")
        return idx
    # Said before the sizes, because the line above drops a country this index
    # does not carry and the summary below then counts only the two shards
    # every reader gets. A reader whose country has no shard was told
    # "21,337 roles in 2 shard(s), 22MB, built 2026-08-28" and nothing else,
    # which is exactly what a healthy download looks like.
    #
    # Not a hypothetical for a small country: `tools/refresh_seed.py` refuses
    # a build only for a shard that held 500 or more roles last week, so
    # Norway (323 roles), Turkey (334) and Finland (434) on the build of
    # 2026-08-28 can each fall out of a build entirely without anything
    # refusing to publish it. This is the line that would say so.
    asked = [c.strip() for c in countries if (c or "").strip()]
    absent = [c for c in asked if c not in shards]
    if absent:
        say(f"Nothing published for {', '.join(absent)}: no shard under that "
            f"name in this index. Taking the two every reader gets.")
    total = sum(shards[n]["bytes"] for n in want)
    built = idx.get("generated", "at some point")
    ride = sum(shards[n]["roles"] for n in want if n in EVERYWHERE)
    yours = sum(shards[n]["roles"] for n in want if n not in EVERYWHERE)
    if not asked:
        say(f"{yours + ride:,} roles in {len(want)} shard(s), "
            f"{total / 1e6:.0f}MB, built {built}.")
    else:
        # Split for the same reason `describe` splits it: 1,584 Portuguese
        # roles and 21,337 that go to everybody is a different fact from
        # "22,921 roles", and the reader is deciding whether this file is
        # worth 23MB to them.
        say(f"{yours:,} roles in {', '.join(asked)}, plus {ride:,} unplaced "
            f"or open in several countries. {len(want)} shard(s), "
            f"{total / 1e6:.0f}MB, built {built}.")

    for name in want:
        path = out / f"{name}.jsonl.gz"
        expect = idx["shards"][name]["bytes"]
        if path.exists() and path.stat().st_size == expect:
            say(f"  {name}: already here")
            continue
        say(f"  {name}: {expect / 1e6:.1f}MB")
        try:
            body = _http_get(f"{base}/{name}.jsonl.gz", cap=max(expect * 2,
                                                               _MAX_BODY))
        except TypeError:
            # A caller that substituted a two-argument `_http_get`, which
            # every test here does. The cap is a guard against a hostile
            # server, and a substituted opener is not one.
            body = _http_get(f"{base}/{name}.jsonl.gz")
        got = len(body)
        if got != expect:
            # A short read is the failure this whole project keeps finding:
            # a truncated shard parses as a shard with fewer roles in it, and
            # the roles that fell off look exactly like jobs that do not
            # exist.
            raise ValueError(
                f"{name}.jsonl.gz came back {got:,} bytes and the index "
                f"says {expect:,}. Refusing a partial shard: the roles missing "
                f"from it would look exactly like roles that do not exist.")
        # Named for this process, because three loads into one directory used
        # to collide on a shared `UK.part` and two of them died blaming the
        # filesystem.
        tmp = out / f"{name}.{os.getpid()}.part"
        tmp.write_bytes(body)
        tmp.replace(path)

    # Written last, so a directory holding an index is a directory whose
    # shards all arrived. A reader that finds an index and a missing shard
    # raises, which is the right answer to an interrupted download.
    # Write-then-rename, like every other file here that is not cheaply
    # regenerable. This was the one write in the seed path that truncated a
    # perfectly good index before replacing it.
    tmp = out / "index.json.part"
    tmp.write_bytes(raw)
    tmp.replace(out / "index.json")
    return idx


def read_index(src: str | Path) -> dict:
    p = Path(src)
    p = p / "index.json" if p.is_dir() else p
    idx = json.loads(p.read_text(encoding="utf-8"))
    _check(idx.get("schema"), str(p))
    return idx


def _check(schema, where: str) -> None:
    if schema != SCHEMA:
        raise ValueError(
            f"{where} is seed format {schema!r}, and this version of job-radar "
            f"reads format {SCHEMA}. Refusing it rather than reading it "
            f"wrongly: a half-understood role still looks like a role.")


def _wanted(root: Path, countries: Iterable[str]) -> list[str]:
    """Shard names to read, given a reader's configured countries.

    An EMPTY country list means no country filter: `screen.match` keeps a role
    wherever it is, and `config.example.yaml` documents the empty list that
    way. `shards_for([])` answers `unplaced, multiple`, which is right for
    "these two always come too" and wrong as the whole answer -- it left a
    reader with no countries set holding 238 of a 2,239-role shard set, every
    US and UK role thrown away, and a summary line that read like the file was
    that small. No countries means every shard on disk.
    """
    want = [c for c in countries if (c or "").strip()]
    if want:
        return shards_for(want)
    tail = ".jsonl.gz"
    return sorted(p.name[: -len(tail)] for p in root.glob(f"*{tail}"))


def load(src: str | Path, countries: Iterable[str]) -> Iterator[Job]:
    """Every role in the shards these countries need.

    A shard the index does not mention is skipped in silence, because "no
    roles in Portugal" and "no Portugal shard" are the same fact from the
    reader's side. A shard the index DOES mention and that is not on disk
    raises, and so does one that cannot be read.

    That distinction was missing and it cost a first run its whole import.
    The shard extension changed from `.json.gz` to `.jsonl.gz`, `read_index`
    accepted the set because the schema number had not moved, and this
    globbed for a name that was not there and skipped every file without a
    word. The run printed the shard sizes it had just read out of the index
    and then "Nothing in this index for your countries. Config
    locations.countries" -- two consecutive lines contradicting each other,
    with the blame landing on the reader.
    """
    root = Path(src)
    root = root if root.is_dir() else root.parent
    try:
        listed = read_index(root).get("shards") or {}
    except (OSError, ValueError):
        # No readable index. A hand-assembled directory is allowed to be just
        # files, and there is nothing to be inconsistent with.
        listed = {}
    for name in _wanted(root, countries):
        path = root / f"{name}.jsonl.gz"
        if not path.exists():
            if name in listed:
                raise FileNotFoundError(
                    f"the index lists a {name} shard holding "
                    f"{listed[name].get('roles', '?')} roles, but "
                    f"{path.name} is not there. This shard set is incomplete "
                    f"or was written by a different version of job-radar. "
                    f"Rebuild it rather than importing part of it.")
            continue
        try:
            fh = gzip.open(path, "rt", encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"{path.name} is not readable as a shard "
                             f"({exc}). Rebuild the set.") from None
        with fh:
            try:
                # Length-capped, because a gzip member is small on the wire
                # and unbounded once opened: a 389KB shard holding one row
                # that decompresses to 400MB is a legal file. No advert is a
                # megabyte, so a longer line is not a role.
                head = fh.readline(_MAX_ROW)
            except (OSError, EOFError, UnicodeDecodeError) as exc:
                # gzip.BadGzipFile is an OSError, and it was reaching the user
                # as a nine-frame traceback out of the standard library. A
                # shard that is not a shard is a thing to say plainly, not a
                # thing to crash over.
                raise ValueError(f"{path.name} is not a readable gzip file "
                                 f"({exc}). The shard set is corrupt or was "
                                 f"only partly downloaded; rebuild or "
                                 f"re-fetch it.") from None
            if not head.strip():
                # An empty file is not an empty shard. A shard that exists is
                # a shard somebody meant to publish, and reading nothing out
                # of it silently is the failure this project keeps finding.
                raise ValueError(f"{path} is empty, so it was not written "
                                 f"properly. Refusing to read it as a shard "
                                 f"with no roles in it.")
            try:
                header = json.loads(head)
            except ValueError:
                raise ValueError(
                    f"{path.name} does not begin with a shard header. Its "
                    f"first line is {head[:60]!r}. Rebuild the set.") from None
            _check(header.get("schema"), str(path))
            # The header says which shard it is and how many roles are in it,
            # and both were parsed and thrown away. A UK.jsonl.gz whose header
            # reads {"schema":1,"shard":"AE","roles":10} imported as UK: the
            # index announced 90 roles, the run read 50, printed "50 roles
            # read", exited 0, and delivered no UK roles at all. Everything
            # needed to catch it was already on the line.
            said = header.get("shard")
            if said and said != name:
                raise ValueError(
                    f"{path.name} contains the {said} shard, not {name}. The "
                    f"set is mislabelled or was assembled by hand; rebuild or "
                    f"re-fetch it rather than importing one country as "
                    f"another.")
            promised = header.get("roles")
            seen = 0
            for n, line in enumerate(iter(lambda: fh.readline(_MAX_ROW), ""), 2):
                if len(line) >= _MAX_ROW:
                    raise ValueError(
                        f"{path.name} line {n} is over {_MAX_ROW:,} "
                        f"characters. No advert is that long; this is not a "
                        f"shard.")
                if not line.strip():
                    continue
                try:
                    yield _unpack(json.loads(line))
                    seen += 1
                except (ValueError, TypeError, KeyError) as exc:
                    # Named by line, because "the file is bad" is not enough
                    # to fix a 35,000 line file with one bad row in it.
                    raise ValueError(
                        f"{path.name} line {n} is not a role ({exc}). "
                        f"Rebuild the set.") from None
        # Checked after the file rather than trusted before it, because a
        # gzip stream that ends early decompresses cleanly up to the cut and
        # a short shard is not a shard that failed, it is a shard with fewer
        # jobs in it.
        if isinstance(promised, int) and seen != promised:
            raise ValueError(
                f"{path.name} says it holds {promised:,} roles and {seen:,} "
                f"were readable. Re-fetch it: the difference would look "
                f"exactly like jobs that do not exist.")


def describe(idx: dict, countries: Iterable[str]) -> str:
    """One line for the user, naming what they are about to get and its age.

    The reader's own countries are counted apart from `unplaced` and
    `multiple`, because adding the three together tells a reader in a quiet
    country the opposite of the truth. Those two ship with every download and
    are 21,337 roles on the build of 2026-08-28, so one merged total barely
    moves whatever the reader's own shard holds. Portugal has 1,584 roles of
    its own and was announced as "22,921 roles for PT, multiple, unplaced".
    Norway has 323 and was announced as 21,660. Gibraltar, which has no shard
    in this index at all, was announced as "21,337 roles for multiple,
    unplaced", its own country quietly dropped from the list of names by the
    `k in want` filter below, with nothing anywhere in the line saying so.

    So "nothing published for your country" rendered as a healthy five-figure
    download, which is the failure this project keeps finding, and the
    `if not roles` line could never catch it: it only fires when `unplaced`
    and `multiple` are BOTH empty, and `tools/refresh_seed.py` refuses to
    publish a build that is missing either. The honest branch was written for
    the right failure and put where it cannot see it.
    """
    # Same rule as `_wanted`: no countries configured means no country filter,
    # so the reader gets the whole index rather than the two shards that ride
    # along with every download.
    asked = [c.strip() for c in countries if (c or "").strip()]
    shards = idx.get("shards") or {}
    want = shards_for(asked) if asked else sorted(shards)
    have = {k: v for k, v in shards.items() if k in want}
    roles = sum(v.get("roles", 0) for v in have.values())
    size = sum(v.get("bytes", 0) for v in have.values())
    if not roles:
        where = ", ".join(want) if want else "any country"
        return f"No prebuilt roles for {where} in this index."
    tail = (f"{size / 1e6:.0f}MB, built "
            f"{idx.get('generated', 'at some point')}. Your own scan runs "
            f"anyway and its answer wins.")
    if not asked:
        return f"{roles:,} roles for {', '.join(sorted(have))}, {tail}"
    mine = {k: v.get("roles", 0) for k, v in have.items()
            if k not in EVERYWHERE}
    yours = sum(mine.values())
    ride = roles - yours
    # A country the reader asked for that this index does not carry. Named
    # rather than dropped: it is the single fact they most need and the only
    # one the old line never printed.
    absent = [c for c in asked if c not in shards]
    if not yours:
        named = ", ".join(absent or asked)
        return (f"Nothing published for {named}. The {ride:,} roles in "
                f"unplaced and multiple come with every download, and none "
                f"of them is placed in {named}. {tail}")
    line = ", ".join(f"{n:,} in {k}" for k, n in sorted(mine.items()))
    head = f"Nothing published for {', '.join(absent)}. " if absent else ""
    return (f"{head}{line}, plus {ride:,} unplaced or open in several "
            f"countries that come with every download. {tail}")
