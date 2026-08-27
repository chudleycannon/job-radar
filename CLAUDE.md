# Working on job-radar

Notes for anyone, human or agent, changing this codebase. `README.md` is for
people using the tool; this is for people editing it.

## The bug this project keeps producing

Nearly every serious defect found here has one shape: **a failure that renders
identically to a success.** Not a crash, not a wrong answer anybody can see. A
thing that finishes, reports that it worked, and quietly lost the work.

Real ones, so the shape is recognisable rather than abstract:

- A 429 stored as an empty board, so a throttled employer read as an employer
  with no vacancies, and `validate --prune` then offered to delete them.
- A quality gate that could not run recorded no value, and every reader counts
  only `is False`, so "never checked" rendered exactly like "passed".
- `rescreen` promised titles, locations, dealbreakers and the salary floor,
  and ran the title check alone. A role paying 50,000 against a floor of
  900,000, matching a hard dealbreaker, came back as "still matches".
- A retry loop written `git push && break; ...; sleep 3` exited with the
  status of `sleep`, so three failed pushes reported success.
- Workday's page cap was three, so a 200-role board returned its first 60 and
  looked complete.
- A pull request titled "Prune 2 dead source(s)" whose diff removed 17,171 of
  17,810. Every guard passed, because all of them reasoned about the run and
  none of them looked at the diff.
- An adapter reading a key the API does not send. `parse_workable` asked for
  `j["location"]`, which apply.workable.com has never had, so every posting
  from 2,094 boards was stored with no location and no error anywhere. The
  same sweep found iCIMS writing locations in a second markup shape the
  pattern did not match, and Avature in three.
- A count stored as a place. Workday and Jobvite collapse a multi-location
  posting to the string "2 Locations", which was written into the location
  column, where it sits exactly where a city would and reads as one.
- A PDF CV read as UTF-8 survived as thousands of characters of `%PDF-1.4`,
  object tables and Flate streams, cleared the "is this empty" length check,
  and became the document every fit score was judged against.

So the question to ask of any code you add, and of any code you are reviewing:
**if this failed, would it look different from this succeeding?** If the answer
is no, that is the bug, and it is worth more than whatever you came to fix.

Three corollaries the codebase already applies:

- **An adapter that finds nothing is not the same as a board with nothing.**
  Reading a field that is not there returns empty, silently, forever. When you
  add or change one, look at a real payload and check the fields you rely on
  are the fields it sends.

- **An unmeasurable gate is a failed gate.** If a check could not run, record
  `False`, never nothing. Readers count `is False`.
- **Zero is a verdict, not an absence.** Anywhere zero results means "dead",
  the code has to be able to say whether it read the board or merely failed to.
- **A value meaning "we cannot say" must never be read as "no".** Found three
  times in one afternoon: the seed gave roles open in several countries a
  shard of their own and handed it to nobody, so a job open in London and New
  York vanished from every UK download; `sources.countries: [NL]` dropped all
  1,599 boards tagged `multi`, which are the multinationals most likely to
  have a Dutch vacancy; and a salary with no currency was compared against a
  floor as though it shared one. The unknown case needs its own branch, and
  usually that branch is "show it and label it".

## Tests

`tests/run_all.py` is what CI runs. It discovers every `tests/test_*.py` and
must work with **nothing installed, pytest included**.

- No module-level `import pytest`. It breaks the standalone run.
- Every test file starts with
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`.
- Every `read_text`/`write_text` passes `encoding="utf-8"`. CI runs Windows,
  where the default is cp1252 and the bundled source list has accented names.
- No live network in a test. Capture a real payload, trim it, put it in
  `tests/fixtures/`.

**A test is a bug that happened, written down so it cannot come back.** Before
keeping one, revert the fix and confirm it goes red. A test that passes against
the broken code is worse than no test, because it is a claim of coverage that
is not there.

Do not assert on timings. A timing test is flaky by construction, and this
suite has already lost a morning to one.

**Do not grep source text for a thing that must be absent.** The comment
explaining why it is absent contains it, so the test fails on its own
explanation. That has happened three times here: a test greping cli.py for a
phrase caught the comment saying why the phrase was wrong, and a test checking
a Windows flag was unused caught the sentence saying so. Parse it, or read the
one function rather than the module.

## What not to do

- **Do not price, place or judge a posting by the reader's config.** The
  reader's floor currency was used as the default for any unsymbolled figure
  anywhere in the world, so an Indian salary of 900,000 rupees was stored,
  confirmed, as $900k. The advert and its country are evidence; the person
  reading it is not.
- **Never work around bot protection.** A 403, a CAPTCHA, a JavaScript
  challenge or a runtime-minted token means blocked: record it and move on. No
  user-agent rotation, no proxies, no headless browsers, no token replay.
  robots.txt is a separate question and is deliberately ignored, documented at
  each place the code relies on that.
- **Never run `generate` or `rank` to test something.** They call a paid model.
  Stub `runner.claude_bin` or `rank._call` and say so.
- **Be polite to third parties.** There are 17,807 boards here and every one of
  them belongs to somebody. When you are probing or experimenting by hand, one
  request a second per host and stop after two failures. The scan itself is
  paced by `fetch.PER_HOST_RPS`, which is higher and is measured: see
  `docs/PLATFORMS.md` for what each host is documented to allow and what it was
  actually observed to tolerate. Do not raise a number there without evidence,
  and never raise Workable's.
- **No em-dashes anywhere**, prose or generated output. Nothing in the repo has
  one and a test checks the workflows.

## Things that look wrong and are deliberate

Each of these has a comment next to it saying so. Read the comment before
"fixing" it.

- Enrichment does not honour `cfg.concurrency`. Plenty of configs still carry
  the `concurrency: 4` the old README advised, and honouring it there turns two
  minutes into eight.
- `enrich()` runs after `match()` in `screen.run`, not before. It is 85% of
  screening CPU and the title gate discards more than 99% of postings. The
  order after that is load-bearing: `sponsorship_gate` reads `job.country`,
  and `apply_salary` and `screen` both append to `job.flags`.
- `apply.workable.com` is paced at 0.7 requests a second. It is not a rate
  limit but a long-window quota: a burst of 90 requests sails through 3.0/s,
  and a sustained 1.5/s is refused at request 301. Burst-testing it will tell
  you it is fine and be wrong.
- Every write of anything not cheaply regenerable is write-then-rename with
  `os.replace`. `os.rename` raises on Windows when the target exists.

## Numbers

Do not write a count into prose. They rot, and this repo has shipped
`17,625`, `17,826`, `17,828`, `13 ATS APIs`, `25 platforms` and `395 tests`
while none of them was true. Derive it, or leave it in the run's own output.
`meta.boards` in `sources/sources.json` is computed by `sources.save()` for
exactly this reason.

Current, if you need them for a comment: 17,807 employer boards, 17,811
entries, 4 keyword templates, 21 board platforms in the data, 29 adapters
written.

## Style

Comments explain **why**, and usually name the failure that motivated the code.
That is the house style and it is the reason this file could be written at all:
the history is in the source, not only in the commits. Match it.
