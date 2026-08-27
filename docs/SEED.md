# The seed: prebuilt roles for the slow half of a scan

A scan reads its sources in four passes.

| Pass | What | Sources | Time |
|---|---|---|---|
| 1 | the fast ones | 9,108 | about 5 minutes |
| 2 | Ashby | 2,607 | |
| 3 | Greenhouse | 4,079 | |
| 4 | `apply.workable.com` | 2,094 | 50 minutes on its own |

Pass 4 is the floor of the whole thing. Workable's own boards are read one
employer at a time and the host is paced at 0.7 requests a second, because it
answered a burst with `Retry-After: 57841`, a sixteen hour refusal. 2,094
requests at that rate is fifty minutes and no amount of concurrency moves it.
See `docs/PLATFORMS.md`.

So passes 2 to 4 are worth publishing ahead of time and pass 1 is not: anyone
can have pass 1 themselves in five minutes, fresher than any file could be.

## What is in it

Everything the adapters produce, **including the advert text**. Without a
description `rank` will not score a role, no dealbreaker can be checked
against it, and no CV can be written from it, so an index of titles and links
would be a list rather than a tool.

What is deliberately **not** in it: `score`, `fit`, `reasons`, `app_status`.
Those are answers to a question only your own config asks. A seed is a saved
fetch, not a saved decision, and `seed load` screens what it reads against
your config exactly as a scan does. Nobody else's filters can reach you.

## Shards

The whole world with adverts attached is about 181MB gzipped, measured over
180 real slow-phase boards and projected across all 8,780. So it is split by
country and you take only what you need.

    UK   14.1 MB  +  13.3 MB  =  27.4 MB
    DE    4.4 MB  +  13.3 MB  =  17.7 MB
    US  111.7 MB  +  13.3 MB  = 125.0 MB

Two shards go to **every** reader, which is the 13.3MB above:

- `unplaced`, roles whose country could not be read
- `multiple`, roles open in more than one country

Neither is evidence that a role is somewhere else. A role open in London and
New York is a UK role, and a role we could not place might be down your road.
Dropping either would hide real vacancies in a way that, from the reader's
side, looks exactly like the job not existing.

## Using one

    job-radar seed load ./shards

It reads the shards for `locations.countries` in your config, screens them
against that config, and stores what survives. `--dry-run` tells you what it
would store without writing.

It does **not** replace a scan, and it does not make one faster. None of these
board APIs has a "changed since" parameter, so a scan still reads every board.
What changes is that your first hour is spent reading a dashboard rather than
watching a counter. Roles die in days and a published file is a day old at
best, so run a scan anyway; its answer wins on every field.

## Building one

    job-radar seed build --out ./shards

A maintainer's command. It reads the slow-phase boards, writes one gzipped
shard per country plus an `index.json`, and touches nothing else: no
database, no screening, no config but its own source list. `--limit N` reads
only the first N boards, which is how to try it without an hour.

Shards are written whole and renamed, and gzipped with `mtime=0`, so
rebuilding an unchanged shard produces an identical file rather than one that
merely looks changed.

## Format

`index.json` names the schema version, the build date, how many boards were
read, and every shard with its role count and byte size. Each shard is
gzipped JSON: `{"schema": 1, "shard": "UK", "roles": [...]}`.

Row keys are one or two characters because they repeat once per role and
there are a quarter of a million of them. Nothing else is optimised; it is
JSON so a person can read it.

A reader that does not recognise the schema number **refuses the file** rather
than reading what it can. A half-understood role still looks like a role.

## Publishing

Nothing here publishes. If a shard set is ever published it should go to a
GitHub **release asset**, not into the repository: 27MB a day committed is
about 8.4GB of history a year and every clone pays it forever, while release
assets never enter history and can be replaced in place.

It should also be built from a machine on a normal connection. GitHub Actions
runners are Azure addresses, and the hosts here refuse those far harder than
they refuse a home IP.
