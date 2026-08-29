# natural-writing

[![Claude skill](https://img.shields.io/badge/Claude-skill-8A2BE2.svg)](https://docs.claude.com/en/docs/claude-code/skills)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A [Claude](https://claude.com/claude-code) skill for writing and editing prose that reads as authentically human — voice-driven, specific, and free of the tells that make text sound machine-generated (and that trip AI-detectors).

It ships with a **self-test loop**: content is drafted, tested against a dependency-free slop detector plus an adversarial self-read, revised where flagged, and looped until it holds up.

## The idea

The durable goal isn't beating a particular detector — detectors are unreliable, disagree with each other, and change every month. The goal is prose that genuinely reads as if a specific, thinking person wrote it. That happens to be exactly what lowers a detector's score too, so optimizing for the writing satisfies both. This skill is built around that principle, not around gaming a scorer.

## What's in here

```
natural-writing/
├── SKILL.md              # the skill: craft guidance + the self-test loop
├── references/
│   └── ai-tells.md       # catalogue of machine-tells with before/after fixes
├── scripts/
│   └── detect.py         # dependency-free "slop" linter (stdlib only)
├── examples/
│   └── ai-slop-sample.md # a deliberately sloppy sample to try the linter on
└── evals/
    └── evals.json        # sample test prompts
```

## Install

**Claude Code / desktop:** copy this folder into your skills directory:

```
cp -r natural-writing ~/.claude/skills/
```

It then loads automatically when you ask Claude to write or humanize prose.

**Claude.ai:** upload the packaged `.skill` file (build one with the skill-creator, or zip this folder), or paste `SKILL.md` into a project's custom instructions.

No dependencies are required. The linter uses only the Python standard library.

## The slop detector

Run it on any draft to get an objective report of the mechanical tells, each tied to a fixable location:

```
$ python3 scripts/detect.py examples/ai-slop-sample.md

  natural-writing slop report  —  77 words, 5 sentences
  ------------------------------------------------------------------
  [✗] tell-words               FAIL   crucial, game-changer, landscape, navigate, testament, vibrant
  [✗] throat-clearing          FAIL   In today's fast-paced world, It's important to note that
  [✗] signature-constructions  FAIL   'not only...but also' x1, 'whether you're...or' x1
  [✗] rhythm-burstiness        FAIL   stdev=2.9, cv=0.19 (want cv>=0.5; higher=more varied)
  [!] hedging                  WARN   often, can be (26.0/1k)
  ------------------------------------------------------------------
  SLOP SCORE: 100/100   (bar: <= 20, and no FAILs)   ->  NEEDS WORK  (has a FAIL)
```

Exit code is `0` on pass, `1` on needs-work — so you can use it in a loop or a pre-commit hook. Pass `--json` for machine-readable output, `--text "..."` to score a string directly.

**Optional statistical signal.** If `torch` and `transformers` happen to be installed, add `--perplexity` to include GPT-2 perplexity and burstiness (the signal classic detectors use; higher = more human). These libraries are *not* bundled — they're ~1 GB of platform-specific binaries, and the skill is intentionally kept tiny and portable. The linter and the adversarial self-read do the real work.

## How the self-test loop works

Three tiers, described fully in `SKILL.md`:

1. **Linter** (`detect.py`) — catches mechanical tells, points at the exact spots. Runs anywhere Python does.
2. **Adversarial self-read** — the model re-reads its own draft as a skeptical detector and scores it. This is the binding gate; in testing it separated human-grade from machine-grade writing more reliably than any statistical score.
3. **GPT-2 perplexity** — optional corroborating number when the libraries are present.

Draft → test → fix the flagged spots → re-test → stop when it passes or stops improving (cap ~3–4 rounds).

## Honest limits

A passing score means *no detector-style tells were found* — not that any specific commercial detector (Pangram, Turnitin, GPTZero, Originality, …) will clear it. Those are trained classifiers that don't rely purely on the signals a regex linter can see, and no rewrite can guarantee their verdict. Detectors also carry real false-positive risk, especially for non-native English speakers. Treat this as a tool for writing *well*, which is the only approach that survives detector updates.

## License

MIT — see [LICENSE](LICENSE).
