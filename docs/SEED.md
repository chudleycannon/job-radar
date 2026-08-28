# The seed: prebuilt roles for the slow half of a scan

A scan reads its sources in four passes.

| Pass | What | Sources | Time |
|---|---|---|---|
| 1 | the fast ones | 9,032 | about 5 minutes |
| 2 | Ashby | 2,607 | |
| 3 | Greenhouse | 4,078 | |
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

The whole world with adverts attached is 242MB gzipped: 289,640 roles from
8,779 boards, measured on the build of 2026-08-28. So it is split by country
and you take only what you need.

    UK   16.6 MB  +  21.7 MB  =  38.3 MB
    DE    6.0 MB  +  21.7 MB  =  27.7 MB
    IN    7.5 MB  +  21.7 MB  =  29.2 MB
    US  112.1 MB  +  21.7 MB  = 133.8 MB

(A 180-board sample had projected 267,000 roles and 181MB. The real thing is
8% more roles and 25% more bytes, which is worth knowing about any figure on
this page that came from a sample rather than a build.)

Two shards go to **every** reader, which is the 21.7MB above:

- `unplaced`, roles whose country could not be read
- `multiple`, roles open in more than one country

Neither is evidence that a role is somewhere else. A role open in London and
New York is a UK role, and a role we could not place might be down your road.
Dropping either would hide real vacancies in a way that, from the reader's
side, looks exactly like the job not existing.

## Using one

    job-radar seed load https://github.com/maccydee/job-radar/releases/download/seed-latest

It reads the index, works out which shards your `locations.countries` needs,
downloads only those, screens them against your config and stores what
survives. A UK reader fetches three files and 38MB, not 164 files and 242MB.

The download is kept (in `seed/` beside your config, or wherever `--keep`
says) so a second config or a second machine does not fetch it again, and a
re-run after a dropped connection resumes rather than starting over. A local
directory works too:

    job-radar seed load ./shards

`--dry-run` tells you what it would store without writing.

A shard whose downloaded size disagrees with the index is refused rather than
stored. A truncated shard decompresses and parses perfectly well as a shard
with fewer roles in it, and the roles that fell off the end look exactly like
jobs that do not exist. The index is written last, so a directory holding one
is a directory whose shards all arrived.

The request carries a user agent and nothing else: no config, no titles, no
identifier. A seed download says nothing about who is asking or what they are
looking for.

It does **not** replace a scan, and it does not make one faster. None of these
board APIs has a "changed since" parameter, so a scan still reads every board.
What changes is that your first hour is spent reading a dashboard rather than
watching a counter. The published set is rebuilt weekly, roles die in days,
and the fast half of the sources is not in it at all, so run a scan anyway;
its answer wins on every field.

## Building one

    job-radar seed build --out ./shards

A maintainer's command. It reads the slow-phase boards, writes one gzipped
shard per country plus an `index.json`, and touches nothing else: no
database, no screening, no config at all. `--limit N` reads only the first N
boards, which is how to try it without an hour.

**The bundled list only, deliberately.** Reading sources through a config
would add `sources.extra` and apply `sectors` and `sources.countries`, so a
published file would carry the boards the person building it had added by
hand. That is not a list of employers, it is a list of the companies they
have been applying to. A narrowing setting would be worse in a quieter way: a
seed cut down to one person's search, still describing itself as the slow
half of the scan.

Rows are written to disk as each board answers rather than collected and
compressed at the end. A quarter of a million adverts is about 1.7GB of text,
and a build that runs out of memory at minute seventy of a seventy-seven
minute fetch has thrown away an hour of other people's bandwidth.

Shards are written whole and renamed, and gzipped with `mtime=0`, so
rebuilding an unchanged shard produces an identical file rather than one that
merely looks changed.

## Format

`index.json` names the schema version, the build date, how many boards were
read, and every shard with its role count and byte size. Each shard is gzipped
JSON, one object per line: a header (`{"schema":1,"shard":"UK","roles":N}`)
and then one role per line.

One object per line rather than one array, because an array has to be parsed
whole before the first role is available. A 35,000-role shard cost 360MB of
resident memory that way, and the US shard is eight times the size. Per line,
the reader holds one role at a time.

Row keys are one or two characters because they repeat once per role and
there are a quarter of a million of them. Nothing else is optimised; it is
JSON so a person can read it.

A reader that does not recognise the schema number **refuses the file** rather
than reading what it can. A half-understood role still looks like a role.

## Publishing

Shards go to a GitHub **release asset**, never into the repository: 242MB a
day committed is history every clone pays for forever, while release assets
never enter history and can be replaced in place. `seed load <url>` expects
the assets to sit under one base URL with `index.json` beside them, which is
what a release download URL gives you.

`seed build` itself publishes nothing. Uploading is a separate step, and
`tools/refresh_seed.py` is the one that does both on a schedule.

## Keeping it fresh

    python3 tools/refresh_seed.py            # build, check, upload
    python3 tools/refresh_seed.py --dry-run  # build and check only

Run weekly by `com.maccydee.jobradar.seed`, Sundays at 11:00 local, after the
source validation and the crawler so it reads a list those two have already
had their turn at. Weekly rather than daily because it is 8,779 requests to
other people's servers and 242MB to a release, and the seed's job is to save
somebody the slow hour rather than to be current.

It is unattended and it writes to a public release, so the failure it is built
against is not a crash, which is loud, but a build that half works and
publishes anyway. A short seed is not visibly broken: it is a seed with fewer
jobs in it, and the ones that fell off look exactly like jobs that do not
exist. So a new build is compared with what is already published and refused
if it is under 80% of it, under 150,000 roles outright, missing `unplaced` or
`multiple`, or missing a shard that had 500 roles last week. That last check
is the one a role count cannot do on its own: Workable is 7% of the roles and
60% of the runtime, so a build that fetched none of it is still 93% of a good
one.

It builds into `seed-build.new` and only swaps that in after uploading, so a
bad run never leaves a half set where the good one was. `--force` overrides
the checks, which is for a genuine market change and never for the schedule.

It should also be built from a machine on a normal connection. GitHub Actions
runners are Azure addresses, and the hosts here refuse those far harder than
they refuse a home IP.
