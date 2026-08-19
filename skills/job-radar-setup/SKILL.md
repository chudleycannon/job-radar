---
name: job-radar-setup
description: Set up or adjust a job-radar config by talking it through, including reading a CV to work out what titles and sectors to search for, and finding the job boards of specific companies the user names. Use when someone wants to configure job-radar, change what it searches for, add companies to watch, adjust their salary floor or dealbreakers, or does not know what job titles they should be looking for. Also use for a first run after cloning the repo.
---

# job-radar-setup

Writes `config.yaml` for job-radar by asking, rather than making someone
hand-edit YAML. There is a plain CLI wizard (`job-radar setup`) that does the
same job; this is the conversational front door to the same file, so the two
cannot drift.

## Before anything else

Read `config.example.yaml` in the repo. It is the schema and it is commented.
If a `config.yaml` or `config.local.yaml` already exists, read it and **edit
it** rather than starting again. People lose carefully tuned dealbreakers to
tools that helpfully start over.

## Ask for the CV first, and do not proceed without one

This is not one of the questions below, it comes before them, and it is the
one thing that has no default. Everything that drafts a document works from
the real CV: without it the tool does not degrade, it invents a career.

Ask for a path. Check the file exists before accepting it. `.docx`, `.pdf`,
`.md` and `.txt` all work. If they cannot produce one, stop and say so rather
than writing a config that will fail later at generation time with a less
obvious error.

## What to establish

Seven things. Ask about them in this order, and stop asking once you have
enough to write a working file. A first config that needs editing later is
much better than twenty questions.

**1. Titles.** What roles do they want. These matter more than they look:
NHS Jobs and LinkedIn are searches rather than employer boards, and these are
the words they get searched with. Wrong titles there means those sources
return nothing useful, not merely that the filter is loose. If they do not know, offer to read
their CV: extract the titles their record actually supports, plus adjacent
ones they would not have searched for. Present those as a starting list to
edit, never as a verdict, and never include a title their experience does not
support just because it pays more.

If they have a CV to hand, `rate-cv` (in `skills/rate-cv`) scores it properly
and its output is a better basis for this than a skim.

**2. Where.** Country, whether remote counts, anywhere they would relocate to,
anywhere to always exclude. Worth being explicit that "remote" on a posting
often means remote *within a country*: job-radar treats `Remote - US` as a US
role, because it is.

**3. Salary.** Get the floor, and explain the rule while you do, because it
surprises people: a role whose **stated** pay is below the floor is hidden,
and a role with **no stated** pay is shown and marked. Roughly two thirds of
postings state nothing, so filtering on absence would throw away most of the
market.

Ask for a walk-away number, not an aspiration. The floor removes noise; it is
not a negotiating position and it never leaves their machine.

**4. Dealbreakers.** What would make them turn a role down after reading it.
Coding rounds, hands-on delivery expectations, on-call, shift work, travel,
managing managers. These become regexes read against the job description.
Write patterns that are specific: `coding` alone matches every engineering
job ever posted.

**5. Sectors.** Which employers to watch. Blank means all of them. Worth
mentioning that the bundled list is currently weighted towards technology, so
someone in healthcare or the public sector should expect to add their own
employers via step 6 and should not read a thin first scan as the market
being empty.

**6. Companies to watch.** Ask for **names**, not URLs. Nobody knows their
target employer's ATS endpoint. Run `job-radar discover <name or domain>` for
each and report honestly:

- found and verified → add it
- found but identity mismatched → show them, let them decide, do not add it
  silently
- blocked → say so plainly. Some large employers refuse automated requests.
  That is a real answer, not a failure to try hard enough.

**7. Politeness.** Concurrency. Four is plenty. It is capped at twelve
because these are other people's servers.

## Writing the file

Write through the same code path the wizard uses:

```python
from jobradar.setup_wizard import write_config
write_config(Path("config.yaml"), answers)
```

That keeps the comments and keeps both front doors consistent. Do not
hand-assemble YAML.

Then say where it went, and run `job-radar scan --limit 25` as a smoke test so
they see it work rather than taking your word for it.

## The one thing to get right

If they do not know what to search for, that is the whole job. Everything else
is a form. Someone who has only ever held one job title will search for that
title and miss the roles their experience actually fits, which is the specific
problem this tool exists to solve. Spend the time there.
