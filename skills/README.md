# Skills

Prompt and quality-gate instructions that pair with job-radar. The app uses
them in Anthropic API mode and in the Claude CLI fallback. They are also
usable as Claude Code skills on their own: `setup` can read a CV to propose
what to search for, and `screen-role` reads a job description with the same
dealbreakers the scanner uses.

## What lives here, and where it comes from

| Skill | Source of truth | Vendored at |
|---|---|---|
| `rate-cv` | [maccydee/rate-cv](https://github.com/maccydee/rate-cv) | `acca44e` |
| `natural-writing` | [maccydee/natural-writing](https://github.com/maccydee/natural-writing) | `6443f21` |
| `screen-role` | this repo | native |
| `job-radar-setup` | this repo | native |

`rate-cv` and `natural-writing` ship in two places on purpose. They stand on
their own for anyone who only wants a CV scored or prose checked, and they ship
here so that cloning job-radar gets you a working set rather than a scanner and
a list of things to go and install.

**Their own repositories are the source of truth.** The copies in this
directory are generated. Do not edit them here: changes belong upstream, and
`.github/workflows/sync-skills.yml` checks weekly that this copy still matches
and opens a pull request when it does not. Editing the copy directly is how two
versions of the same skill quietly stop agreeing with each other.

## Writing gate

The drafting prompts and two of the local quality gates call
[natural-writing](https://github.com/maccydee/natural-writing). It is bundled
here so Docker and API-backed generation can run the checker without a local
Claude skills install. A personal `~/.claude/skills/natural-writing` copy still
wins when present, which lets you test local edits without changing the
vendored copy.

## Installing

Nothing needs copying for job-radar's own use. `generate` reads this directory
straight out of the checkout, resolved from the package rather than from the
working directory, so a fresh clone can screen and draft with no setup step.
It looks in `~/.claude/skills` first and falls back to here, so a skill you
have edited yourself is the one that gets used.

To use them in Claude Code generally, copy them where Claude Code looks:

```bash
cp -r skills/rate-cv ~/.claude/skills/
cp -r skills/natural-writing ~/.claude/skills/
```
