---
name: screen-role
description: Read a job description and decide whether it is worth applying to, against dealbreakers the user has written down. Use when someone shares a job posting, a JD, or a job URL and wants to know if it is a fit, whether to apply, what the catch is, or where the gaps are. Also use before writing an application, since a role that fails screening should not get a cover letter. Works from a pasted description, a URL, or a job-radar scan result.
---

# screen-role

Most job tools are optimised to make people apply to more things. This one
exists to tell someone not to bother, so their effort goes to the roles that
can actually land.

A role fails screening for reasons that are almost never visible in a search
result. They are three paragraphs into the description, or in the interview
process, and they are found by reading rather than by matching keywords.

## The order matters

Work through these in sequence and stop at the first hard fail. Screening in
this order saves the most time, because the cheapest checks eliminate the most
roles.

**1. The dealbreakers.** These live in the job-radar config, which is
`config.local.yaml` if it exists and `config.yaml` otherwise. A skill runs
from wherever the user happens to be, so search rather than assuming a path:

```bash
ls ~/job-radar/config.local.yaml ~/job-radar/config.yaml 2>/dev/null
```

If neither is there, ask what their dealbreakers are. Never invent them, and
never screen against a list you have not read: a screening built on assumed
dealbreakers carries the same confidence as a real one and is worthless. Common ones people hold
without saying so: a live coding round, an expectation of hands-on delivery in
a leadership role, on-call, shift patterns, heavy travel, managing managers
when they never have, pre-sales dressed up as engineering.

Quote the exact sentence that triggers each one. A dealbreaker asserted
without a quote is a guess, and the user will rightly not trust it.

**2. The seniority reality.** Titles lie in both directions. A "Senior
Engineering Manager" running two people is a team lead; an "Engineering
Manager" over four teams is a director. Look for team size, reporting lines,
and who the role reports to.

**3. The hands-on tell.** This is the one that gets missed, because it is
rarely stated plainly. Phrases that mean coding is expected: "player-coach",
"still writes code", "contributes to the codebase", "hands-on technical
leadership", "roll up your sleeves", "direct implementation assistance",
"comfortable in the code". The last two read as collaboration and mean
delivery.

**4. The interview process.** Check the company's process before recommending
an application. A take-home, a live coding round or a systems-design exercise
with implementation changes the answer for a lot of people. If the description
does not say, say that it does not say rather than assuming it is fine.

**5. Location and pay.** Apply the same rule the scanner uses: a stated figure
below the floor is a fail; no stated figure is not, and is reported as
unknown rather than as a problem. Do not convert currencies to make a
comparison work.

**6. The genuine gaps.** Requirements the user cannot truthfully claim.

## On gaps, and the line you do not cross

Report every gap as a gap. Never suggest wording that implies experience the
user does not have, and never help make a weak match read as a strong one.
If a requirement is unmet, the honest options are: apply anyway and be clear
about it, close the gap first, or skip the role. Inventing a way to phrase
around it is not one of them, and it fails at the first interview question.

A gap is not automatically a rejection. Most postings list an ideal candidate
who does not exist. Distinguish:

- **Hard requirement** — the job cannot be done without it. A real filter.
- **Stated preference** — "ideally", "nice to have", "or similar". Not a filter.
- **Experience gap** — the user could do it but has not yet. Lowers the odds,
  does not eliminate them. Say so, in those terms.

That last distinction matters more than any other in this file. Telling
someone they are unqualified when they are merely untested is the most
expensive mistake this skill can make.

## Output

Lead with the verdict, then the evidence. Nobody needs a preamble.

```
APPLY / APPLY WITH CAVEATS / SKIP  — one line of why

Fails        (quote the sentence, name the dealbreaker)
Unknowns     (what the JD does not say and you would need to ask)
Gaps         (hard requirement vs stated preference vs untested)
Strengths    (specific, matched to their real record, no padding)
Ask them     (the questions worth putting to a recruiter)
```

Keep it short. Three lines that name the actual blocker beat a page of
balanced assessment.

## When you cannot read the posting

Many careers sites block automated requests. If a URL will not load, ask for
the text rather than screening from the company name and a job title. A
screening built on a guess about what the role probably says is worse than no
screening, because it carries the same confidence.

## Working with job-radar

`job-radar scan` applies the same dealbreakers mechanically across every board
and flags what it catches, but it only reads what the API returns. Some
platforms return no description at all: LinkedIn's public endpoint gives title,
company and location only, and those roles are marked
`not screened: no description from this source`. Those are exactly the ones to
run through this skill by hand.
