# Skills

Claude Code skills that pair with job-radar. They are usable on their own, and
they are usable together: `setup` can read a CV to propose what to search for,
and `screen-role` reads a job description with the same dealbreakers the
scanner uses.

## What lives here, and where it comes from

| Skill | Source of truth | Vendored at |
|---|---|---|
| `rate-cv` | [maccydee/rate-cv](https://github.com/maccydee/rate-cv) | `01449e6` |
| `screen-role` | this repo | native |
| `job-radar-setup` | this repo | native |

`rate-cv` ships in two places on purpose. It stands on its own for anyone who
only wants a CV scored, and it ships here so that cloning job-radar gets you a
working set rather than a scanner and a list of things to go and install.

**Its own repository is the source of truth.** The copy in this directory is
generated. Do not edit it here: changes belong upstream, and
`.github/workflows/sync-skills.yml` checks weekly that this copy still matches
and opens a pull request when it does not. Editing the copy directly is how two
versions of the same skill quietly stop agreeing with each other.

## Also required for document generation

The drafting prompts and two of the four quality gates call
[natural-writing](https://github.com/maccydee/natural-writing). It is not
vendored here because it is a general writing skill with a life of its own,
but generation degrades silently without it:

```bash
git clone https://github.com/maccydee/natural-writing ~/.claude/skills/natural-writing
```

## Installing

Copy any of these into `~/.claude/skills/`:

```bash
cp -r skills/rate-cv ~/.claude/skills/
```
