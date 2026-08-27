"""A prebuilt index of the slow half of the scan, so a new user is not idle.

A full scan reads 17,888 sources in four passes. The first pass is nine
thousand fast sources and finishes in about five minutes. The other three are
Ashby, Greenhouse and Workable's own boards -- 8,780 employer boards, one
request each, and `apply.workable.com` is paced at 0.7 requests a second
because it answered a burst with a sixteen hour refusal. That floor is fifty
minutes and no amount of concurrency moves it.

So the slow passes are exactly what is worth shipping ahead of time, and the
fast pass is exactly what is not: a new user can have that themselves in five
minutes, fresher than any file we could publish.

**Descriptions are in.** Without one, `rank` refuses to score a role (it wants
200 characters), no dealbreaker can be checked against it, and no CV can be
written from it. An index of titles and links would be a list, not a tool.

**Sharded by country**, because the whole world with adverts attached is 181MB
and nobody wants it. A UK reader takes the UK shard plus two that everybody
takes: roles whose country could not be read, and roles open in several
countries at once. That is about 27MB together. Neither of those is evidence
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
        out["$"] = [s.min, s.max, s.currency, s.period, 1 if s.confirmed else 0]
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
                              period=sal[3] or "year", confirmed=bool(sal[4]))
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


def build(jobs: Iterable[Job], out_dir: str | Path, *,
          generated: str | None = None, boards: int = 0,
          note: str = "") -> dict:
    """Write one gzipped shard per country, plus an index describing them.

    Returns the index. Writes nothing anywhere but `out_dir`.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict]] = {}
    for j in jobs:
        buckets.setdefault(shard_of(j), []).append(_pack(j))

    index = {
        "schema": SCHEMA,
        "generated": generated or date.today().isoformat(),
        "boards": boards,
        "note": note or ("slow-phase employer boards only: Ashby, Greenhouse "
                         "and apply.workable.com. Run a scan for the rest."),
        "shards": {},
    }
    for name, rows in sorted(buckets.items()):
        blob = json.dumps({"schema": SCHEMA, "shard": name, "roles": rows},
                          separators=(",", ":"), ensure_ascii=False)
        path = out / f"{name}.json.gz"
        # Written whole then renamed, like everything else here that is not
        # cheaply regenerable, so a reader can never open a half-written file
        # and take it for a short one. mtime=0 so rebuilding an unchanged
        # shard produces an identical file and does not look like a change.
        tmp = path.with_suffix(".tmp")
        with gzip.GzipFile(tmp, "wb", compresslevel=9, mtime=0) as fh:
            fh.write(blob.encode("utf-8"))
        tmp.replace(path)
        index["shards"][name] = {"roles": len(rows), "bytes": path.stat().st_size}

    tmp = out / "index.tmp"
    tmp.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out / "index.json")
    return index


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


def load(src: str | Path, countries: Iterable[str]) -> Iterator[Job]:
    """Every role in the shards these countries need.

    Missing shards are skipped in silence, because "no roles in Portugal" and
    "no Portugal shard" are the same fact from the reader's side. A shard that
    exists and cannot be read is not skipped: it raises.
    """
    root = Path(src)
    root = root if root.is_dir() else root.parent
    for name in shards_for(countries):
        path = root / f"{name}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
        _check(payload.get("schema"), str(path))
        for row in payload.get("roles") or []:
            yield _unpack(row)


def describe(idx: dict, countries: Iterable[str]) -> str:
    """One line for the user, naming what they are about to get and its age."""
    want = shards_for(countries)
    have = {k: v for k, v in (idx.get("shards") or {}).items() if k in want}
    roles = sum(v.get("roles", 0) for v in have.values())
    size = sum(v.get("bytes", 0) for v in have.values())
    if not roles:
        return f"No prebuilt roles for {', '.join(want)} in this index."
    return (f"{roles:,} roles for {', '.join(sorted(have))}, "
            f"{size / 1e6:.0f}MB, built {idx.get('generated', 'at some point')}. "
            f"Your own scan runs anyway and its answer wins.")
