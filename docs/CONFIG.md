# Config reference

Every setting, what it accepts, and what happens when it is wrong. The file is
`config.local.yaml` if present, otherwise `config.yaml`. `job-radar setup`
writes one for you; this is for editing it afterwards, or writing it by hand.

**The config is validated when it loads.** An unknown key, a broken regex, a
salary that is not a number or a format that does not exist stops the run with
a message naming the setting. It does not silently do something else.

## titles

| key | type | notes |
|---|---|---|
| `include` | list of strings | **Required.** Matched against the posting title, whole words, case-insensitive. Also the search terms for NHS Jobs and LinkedIn, so wrong titles there return nothing rather than merely filtering loosely. Only the **first six** are used as search terms. |
| `exclude` | list of strings | Never show these, even when `include` matches. Escaped, so brackets and punctuation are safe: `healthcare assistant (bank)` works. |

An empty `include` is refused: with no titles every posting matches and the
keyword sources have nothing to look for.

## locations

| key | type | notes |
|---|---|---|
| `countries` | list of codes | Empty means anywhere. Country names are accepted and normalised (`Portugal` becomes `PT`); anything the filter cannot use is refused at load rather than silently matching nothing. Note the UK is `UK`, not `GB`. |
| `remote_ok` | true / false | Unquoted. `"no"` and `"false"` are understood, anything else is refused rather than read as true. |
| `relocate_to` | list of codes | Shown, scored below home. Same validation as `countries`. |

The full set of codes the location filter recognises:

`AE`, `AR`, `AT`, `AU`, `BE`, `BR`, `CA`, `CH`, `CN`, `CZ`, `DE`, `DK`, `ES`, `FI`, `FR`, `HK`, `ID`, `IE`, `IL`, `IN`, `IT`, `JP`, `KR`, `MX`, `MY`, `NL`, `NO`, `NZ`, `PH`, `PL`, `PT`, `RO`, `SE`, `SG`, `TH`, `TR`, `UK`, `US`, `VN`, `ZA`

A country not on this list cannot be filtered on. Roles there are still
fetched; they are dropped as "location not recognised" unless `countries` is
empty.
| `exclude` | list of places | Applied per location. A role in London only is dropped; a role in "London / Manchester" survives on Manchester. |

## cv

| key | type | notes |
|---|---|---|
| `path` | path | **Required for document generation.** `~` is expanded. Checked on every load, so a CV you moved fails loudly instead of producing an invented one. |

## salary

| key | type | notes |
|---|---|---|
| `currency` | `GBP`, `USD` or `EUR` | Anything else is refused. A salary in a different currency to your floor is never converted: it is shown and marked "not compared", and it can neither disqualify a role nor earn it points. |
| `floor` | number | `70000`. `£70,000` and `70,000` are accepted and converted. Words are refused. A role whose **stated** pay is below this is hidden; a role with **no** stated pay is always shown and marked. |

## dealbreakers

A list of `{name, pattern, hard}`. `pattern` is a regular expression read
against the job description. `hard: true` hides the role, `hard: false` shows
it with a warning.

Every entry is validated: a missing pattern, an unknown key, or a regex that
does not compile stops the run and names the entry. Previously these were
dropped in silence, so a dealbreaker you thought was protecting you was simply
absent.

## sectors

Which employers to watch. Empty means all of them. These are the tags that
actually exist in the bundled list, out of 17,627 sources:

| sector | sources |
|---|---|
| `untagged` | 16,981 |
| `technology` | 228 |
| `finance` | 74 |
| `healthcare` | 44 |
| `industry` | 42 |
| `professional-services` | 34 |
| `security` | 33 |
| `media` | 31 |
| `retail` | 26 |
| `telecoms` | 25 |
| `education` | 23 |
| `public-sector` | 23 |
| `legal` | 17 |
| `travel` | 16 |
| `hospitality` | 15 |
| `charity` | 15 |

**Read the first row before setting this.** Only 646 sources carry a tag at
all. The rest arrived from a crawl-index harvest that knows a board's address
and not the employer's industry, so `technology` being 228 is a count of
labels, not of technology companies.

Setting `sectors` **keeps every untagged source as well** as the ones tagged
with what you asked for. So it removes the labelled sources you did not ask
for and leaves the other 16,981 in place, which is why it narrows the list far
less than the numbers above suggest. A tag that is not in this table is
refused at load rather than quietly matching nothing. Check yours with
`job-radar coverage`, which counts the file rather than this table.

## sources

| key | type | notes |
|---|---|---|
| `use_bundled` | true / false | |
| `countries` | list of codes | Only filters sources that carry a country tag, and 386 of 17,627 do. The rest are always fetched, and `job-radar coverage` says so when this is set. |
| `extra` | list | Either a bare URL string, or `{company, url, platform}`. `job-radar discover <name> --add` writes these for you. |
| `reed_api_key` | string | Free key from <https://www.reed.co.uk/developers/jobseeker>, needed only if you add the Reed source. Falls back to the `REED_API_KEY` environment variable when blank, which is the route for GitHub Actions. **Put a real key in `config.local.yaml`, never in `config.yaml`**: the second one is committed. Blank means the Reed source is skipped, with a message naming it. |
| `adzuna_app_id`, `adzuna_app_key` | string | Free pair from <https://developer.adzuna.com/signup>, needed only if you add the Adzuna source. Both fall back to `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` in the environment when blank, which is the route for GitHub Actions. **Real values go in `config.local.yaml`, never in `config.yaml`.** Either one missing means no credentials, and the Adzuna source is skipped with a message naming it. Adzuna's free limits are 25 calls a minute, 250 a day, 1,000 a week and 2,500 a month; one scan is one call per job title per page. |

## output

| key | type | notes |
|---|---|---|
| `formats` | list | `html`, `json`, `markdown`. Anything else is refused, rather than producing a successful run that writes no files. |
| `dir` | path | `~` is expanded. |

## fetch

| key | type | notes |
|---|---|---|
| `concurrency` | number | Default 4, capped at 12 with a warning. These are other people's servers. It is also what sets how long a scan takes: 4 measured 6.8 boards a second, so about 43 minutes for the whole 17,600-board bundled list. |
| `timeout` | seconds | Default 20. |
| `retries` | number | Default 2. |
| `user_agent` | string | Identifies the tool. Leave it identifying. |

## Command-line flags not in the examples

| flag | applies to | notes |
|---|---|---|
| `-c, --config` | all | Which config to load. |
| `--db` | scan, list, applied, generate, serve | Database path. Default `data/job-radar.db`. |
| `--docs` | generate, serve | Where generated documents go. Default `~/job-applications`. |
| `--limit` | scan, list | Cap the sources fetched, or the rows listed. |
| `--dry-run` | scan, rank, enrich | Do not record what was seen. On `rank`, show what it would cost and send nothing. |
| `--json` | list | Machine-readable output. |
| `--all` | list | Include settled roles, and roles no longer on a board. |
| `--new` | list | Only roles first seen on the most recent scan. |
| `--no-enrich` | scan | Skip fetching full postings for headline-only sources. They stay unscreenable. |
| `--prune`, `--force-prune` | validate | Rewrite `--file` without the dead sources. |
| `--refresh`, `--top` | rank | Re-score roles that already have a fit; how many to print. |
| `--port`, `--host`, `--no-browser` | serve | |
