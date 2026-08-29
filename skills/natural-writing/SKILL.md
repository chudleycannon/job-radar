---
name: natural-writing
description: >-
  Write and edit prose that reads as authentically human — voice-driven,
  specific, and free of the tells that make text sound machine-generated (and
  that trip AI-detectors). Use this whenever producing or revising
  reader-facing written content: blog posts, articles, essays, newsletters,
  marketing and website copy, social posts, product descriptions, cover
  letters, emails, LinkedIn posts, scripts, or long-form documentation. Trigger
  it whenever the user asks to "make this sound human," "not sound like AI/
  ChatGPT," "pass AI detection," "humanize," "de-slop," "add voice," or to
  write/rewrite/edit/polish any substantial piece of prose — even if they don't
  name this skill. Do not use it for code, config, data transforms, or terse
  factual lookups where prose style is irrelevant.
---

# Natural writing

## What this is for

The goal is prose that reads as if a specific, thinking person wrote it — because that is also what evades AI-detectors, and it is the only version that stays evaded. Detectors are unreliable and change monthly, so tuning text to beat a particular scorer is a losing game. But the qualities that lower a detector's score — genuine voice, concrete detail, varied rhythm, an actual opinion — are the same qualities that make writing worth reading. **Optimize for the writing, not the score.** Everything below serves that.

If a user explicitly wants to game a named detector, tell them plainly: the durable move is writing that genuinely reads as human, which this skill produces, and no rewrite can *guarantee* a given detector's verdict. Then do the real work.

## The two failure modes

Machine-sounding prose fails in two directions at once. Fix both.

1. **It does things humans don't** — the tells: inflated diction, throat-clearing openers, rule-of-three everything, "it's not X, it's Y," uniform paragraphs, relentless positivity, hedging, vague "studies show." The full catalogue with fixes is in `references/ai-tells.md` — read it before a serious de-slop or humanize job.

   One that hides from every rule about punctuation: **a denial in front of a
   colon**, used to make the next clause land harder. "Not a trial: the team's
   daily output ships to customers." "Not as a pilot: the same." It is an
   em-dash aside wearing different punctuation, and a density check will never
   see it, because one is already the tic. Write the two sentences instead, or
   drop the denial and just say the thing. The linter now fails on it
   (`negation-colon`), but it was caught by hand twice on a document the
   linter had already passed at 3/100, which is the point: the score is a
   backstop, not the test.
2. **It fails to do things humans do** — the craft: varied rhythm, concrete specifics, a real stance, plain words, uneven structure. This is the harder half, and the more important one. You can strip every "delve" from a piece and it will still sound like a machine if it has no voice and no specifics.

Avoiding the tells makes prose *inoffensive*. The craft below makes it *human*. Do both.

## The craft — how humans actually write

### Write for the ear: vary the rhythm

Uniform cadence is the loudest machine signal. Humans mix long and short on purpose.

- After two or three medium sentences, drop a short one. Three words. It lands.
- Then let one sentence run long, building a clause onto a clause until the thought is fully carried, and only then stop.
- Use fragments for emphasis. Sparingly.
- Start sentences with "And" or "But" when the momentum wants it. The rule against it is a schoolroom myth.
- Let a one-line paragraph stand alone when a point deserves the pause.

The test is auditory: read it aloud. Where you stumble or run out of breath, the reader will too.

**Don't overcorrect into a new formula.** These moves are seasoning, not a recipe. A one-word fragment every other paragraph ("Compounding." "Curiosity." "It held."), a punchy antithesis in every slot, a callback-reveal ending bolted onto everything — deploy them mechanically and they become their own machine signature: prose that's obviously *engineered* to sound casual. A reader (or detector) can feel the technique. The goal is a natural spread, where a plain, slightly uneven sentence is often the right call and the fragment lands *because* it's rare. When a device starts to feel like a habit, that's the signal to let the next few sentences be ordinary.

### Be specific — the single highest-leverage move

Abstraction is where voice goes to die, and it's what the machine defaults to. Specificity is the fastest way to sound like someone who actually knows the thing.

- Concrete nouns over category words: "a dented Ford Transit," not "a vehicle."
- Real numbers over intensifiers: "cut load time from 4.2s to 900ms," not "significantly faster."
- Named examples over gestures: "Postgres choked on the third join," not "the database had issues."
- One lived, checkable detail the model couldn't have invented does more than a paragraph of adjectives.
- **The portability test.** If a sentence could move unchanged to another person, company, product or country, it is filler. Either cut it or replace it with a fact, mechanism, consequence or judgement that only fits this subject. This is the fastest way to find the sentences that feel fine and say nothing.

> **Before:** "Users often struggle with onboarding and abandon the process."
> **After:** "Forty percent of new users quit on the same screen — the one asking for a card before showing a single feature."

### Have a stance

Machine prose hedges everything and commits to nothing. A person has a position and occasionally an opinion.

- Say what you actually think. "This is the wrong tool for the job" reads as authored; "there are pros and cons to consider" reads as filler.
- Admit uncertainty or bias openly — "I might be wrong, but…" / "I've always found X overrated." Owned subjectivity is a strong voice signal.
- Use "I" and "you." Put a person in the room.
- Allow one dry aside per section. More and it reads as trying too hard; restraint is itself voice.

> **Before:** "There are various approaches, each with advantages and disadvantages depending on context."
> **After:** "There are three ways to do this. Two are wrong, and I've shipped both."

### Use plain words

- Short word over long: use/not utilize, buy/not purchase, help/not facilitate, now/not "at this point in time."
- Contractions — don't, it's, you'll. Their absence is one of the loudest robotic tells.
- Live idiom ("cuts both ways," "back of a napkin") signals a native ear — but only fresh idiom, never the pre-printed cliché.
- Skip the thesaurus reach. The over-precise synonym (plethora, myriad, leverage-as-verb) reads as straining.
- Leave a little roughness. One sentence that isn't perfectly balanced reads human. Polish everything to a gleam and it looks like plastic.

### Let structure breathe

- Cut throat-clearing intros. Delete "In today's world," "It's important to note," "This article will discuss." Start on the point.
- Cut recap conclusions. Trust the reader; end on the strongest concrete beat, not a summary.
- Choose where the lede goes — front-load when the reader needs the answer fast (most practical writing), bury it when a short build earns a bigger payoff. The point is *choosing*, not defaulting.
- Keep paragraph lengths uneven. A one-liner next to a long one. Uniform blocks are a signature.

## When the words are someone else's

Everything above describes how to write. Applying it to a draft someone else wrote is a different job, and the failure mode is specific: you strip the tells, apply the craft, and hand back prose that is cleaner, more varied, more concrete — and no longer sounds like them. That isn't a de-slop, it's a transplant, and the writer will feel it even if they can't name it.

So before changing a line, read the whole thing and note three to five things that are **theirs**: a vocabulary quirk, a blunt register, a running joke, an admission, a digression they clearly enjoyed, an unevenness in polish. Keep that note to yourself. Then protect those things through every pass.

> **Their line:** "Anyway the migration took three weekends and I still don't fully trust it."
> **Over-edited:** "The migration required three weekends of work and continues to warrant monitoring."
> Every tell is gone. So is the person. The admission was the most human thing on the page, and tidying it cost more than the roughness did.

- **Make the minimum effective edit.** Fix the tells, the errors, the genuinely tangled sentences. Leave strong human lines alone even when you can see a tidier version. A rough draft with a real voice should still read as the same person afterwards, just clearer.
- **Edge is not slop.** Strong opinions, profanity, self-interruption, an abrupt ending, a paragraph that runs long because they were annoyed — these are voice. Don't sand them into something more professional.
- **Their structure is a choice until proven otherwise.** Keep the progression and the detours. If you do reorganise, say why.
- **Don't add.** No invented examples, numbers, sources or opinions. Where something is unclear, ask rather than filling the gap with something plausible.
- **Cut in proportion to the actual slop.** A draft that is 90% theirs and 10% filler needs a 10% edit. Compressing it by half because the craft rules say "cut 10-30%" strips character along with the padding — that guidance is for your own drafts, not someone else's.

Say what you changed, briefly, at the end. A short list of what moved and why beats a silent rewrite the writer has to diff in their head.

## Detect without rewriting

Sometimes the job is an audit, not an edit — "does this read as AI?", "scan this before I send it". Answer that as a report, and don't rewrite the draft.

Name each pattern you find, quote the line it appears in, and give the fix in a few words. That is it. Three specific rules make the report worth more than a detector:

- **Don't claim to know whether a machine wrote it.** You can't, and neither can any detector. Name the patterns present. A named pattern with a quoted line is evidence the writer can check and act on; a confidence percentage is a guess wearing a number.
- **Don't hand back a score as the answer.** `detect.py` produces one and it is useful for tracking a draft across revisions, but a number tells the writer nothing about what to change. Lead with the patterns.
- **Don't slip into editing.** Rewriting a line "to show what I mean" turns an audit into an edit the writer did not ask for. Offer to edit afterwards.

## Workflow

For a **new piece**: internalize the craft above, then write it that way from the start — it's far easier than de-slopping afterward. Draft with a real stance and concrete specifics already in place.

For **editing or humanizing existing text**: do targeted passes rather than one vague "make it better" sweep. Each pass has a single job, which is what makes it effective:

1. **Read `references/ai-tells.md`** if you haven't. It's the checklist for passes 2–3.
2. **Tells pass.** Hunt the specific patterns: inflated words, throat-clearing openers, the three signature constructions ("not X but Y," rule-of-three, "not only… but also"), em-dash clusters, vague attribution. Fix the habit, don't just swap the word.
3. **Craft pass.** Now add what's missing: vary the sentence lengths, replace one abstraction with a concrete specific, insert a real stance, cut a recap conclusion.
4. **Read-aloud pass.** Actually read the result as prose. Anything you stumble over, or that a smart friend wouldn't say out loud over coffee, gets rewritten or cut.
5. **Cut 10–30%.** Tighten. Every remaining word should earn its place. This pass alone removes most of the remaining machine feel — bloat and hedging are core tells, and cutting kills both.

Match the voice to the context. A cover letter, a punchy newsletter, and a technical runbook are all "human" in different registers — humanizing doesn't mean making everything casual and jokey. Read what the user gave you (and any surrounding material) and preserve their register while removing the machine tells.

## Self-test loop — draft, test, revise, repeat

For anything substantial — a blog post, an article, a page of copy, a design doc, anything the user will actually publish or send — don't stop at the first draft. Test it, fix what the test flags, and loop until it holds up or stops improving. This is what "write it, then make sure it doesn't read as AI" actually looks like in practice. Trigger it whenever the user asks for content "using the natural-writing skill," asks for it to be undetectable/human, or when the piece is long enough to be worth the care.

The test has three tiers. Use as many as you can; they catch different things.

**Tier 1 — the bundled linter (run it if you have a shell).** `scripts/detect.py` is dependency-free Python that flags the mechanical tells — tell-words, throat-clearing, the signature constructions, weak rhythm, promotional gush, low concreteness — and points at the exact offending spots.

```
python3 scripts/detect.py path/to/draft.md
```

It prints a per-check report and a slop score, and exits non-zero until the draft has no FAILs and scores ≤ 20. If you have no shell (e.g. a chat-only environment), skip to Tier 2 — the linter is a convenience, not a requirement.

**Tier 2 — the adversarial self-read (always do this; it is the real gate).**

Worth knowing how often this is the only tier that fires. On a recent short post the linter passed three consecutive drafts at 10, 0 and 0, while the self-read caught a "not X but Y" opener, an announced rule-of-three with three identically shaped blocks under it, and then, in the fix for the first one, a reveal construction doing exactly the same work. Every real problem came from reading it as a skeptic. Treat a clean linter run as the start of the check rather than the end of it.
 Re-read your own draft as if you were a skeptical AI-detector being paid to catch it. Give it an honest AI-likelihood score out of 100 and name the three features most likely to give it away. Be harsh — assume it's guilty. This catches everything the linter can't: gratitude gush, relentless positivity, generic abstraction, no real stance, prose that's *technically* clean but has no pulse. In testing this separated human-grade from machine-grade writing more reliably than any statistical score, so treat it as binding: if you'd rate your own draft above ~25, it isn't done.

**Tier 3 — GPT-2 perplexity (optional, only if `torch`+`transformers` happen to be installed).** Adds the statistical signal classic detectors use:

```
python3 scripts/detect.py path/to/draft.md --perplexity
```

Higher perplexity and burstiness = more human. If the libraries aren't present the flag is ignored — don't install heavy dependencies just for this; Tiers 1–2 are enough.

**The loop:**

1. Draft applying the craft above.
2. Test: run Tier 1 (if available) and always do Tier 2. Note every specific flag with its location.
3. Revise *those specific spots* — don't rewrite wholesale, and don't just swap flagged words for synonyms. Fix the underlying habit the flag points to.

   The trap here is the substitution that keeps the move. A "not X but Y" opener rewritten as "turns out it was Y" is the same rhetorical gesture in a new costume, and a reader clocks the gesture, not the wording. After each fix, ask what job the original sentence was doing. If the replacement still does that job, it has not been fixed. The way out is nearly always to state the plain fact and stop.
4. Re-test. Repeat until it passes (no linter FAILs, score ≤ 20, self-read ≤ ~25) — or until two passes produce no real improvement, whichever comes first. Cap it at about 3–4 rounds; past that you're polishing plastic, and over-editing introduces its own engineered feel.
5. When you hand the piece to the user, briefly say what you checked and where it landed (e.g. "ran it through the self-test — no tells flagged, reads clean"). Don't paste the full report unless they want it.

One caution worth repeating: passing every tier means *no detector-style tells were found*, not that any specific commercial detector will clear it. Detectors are unreliable and change often. The honest promise is writing that genuinely reads as human — which is what these tiers optimize for — not a guaranteed verdict from a tool you don't control. If a user needs a specific detector satisfied, tell them that plainly.

## Rewriting loses facts, and the harder you rewrite the more it loses

Measured, not asserted. A 2026 experiment on ten machine-written reports
(krllagent/text-watermark-roundtrip, MIT) ran four transformations over the
same texts and scored 100 pre-registered fact claims afterwards:

| transformation | fact claims kept |
|---|---|
| full paraphrase by a competent model | 100 of 100 |
| DIPPER-11B paraphraser | **77 of 100** |

A dedicated paraphraser silently dropped or altered **roughly one claim in
four** while producing text that still read fine. Nothing in the output
announced it. That is the risk in every de-slop and humanize job: the prose
improves and a number, a date or a scope quietly changes with it.

So, on any rewrite of someone else's text or your own:

- **Diff the claims, not the prose.** List every number, date, name, scope and
  title in the source, then confirm each one survives unchanged. This is the
  one check that cannot be done by reading the result, because a wrong number
  reads exactly as well as a right one.
- **Rewrite the sentence, not the paragraph**, when the paragraph carries
  facts. Wholesale regeneration is where claims go missing.
- **Prefer cutting to rephrasing.** A claim you remove is visibly gone. A
  claim you rephrase can come back subtly wrong and nobody notices.

The same study found round-trip translation through German or Chinese changed
almost nothing about the text's machine origin (0 of 10) while still degrading
it, so it is a cost with no return. Do not reach for it.

## Quick checklist

Run this over any finished draft:

- [ ] Sentence lengths genuinely vary — at least one short punch, at least one long build.
- [ ] No throat-clearing opener; the piece starts on its actual point.
- [ ] No recap conclusion; it ends on a real beat.
- [ ] The three signature constructions are absent: "not X but Y," rule-of-three triples, "not only… but also."
- [ ] Inflated words replaced with plain ones (see `references/ai-tells.md`).
- [ ] At least one concrete, checkable specific — a number, a name, a real detail.
- [ ] A clear stance or opinion is present, not just balanced neutrality.
- [ ] Contractions used; em-dashes not clustered; headings sentence-cased.
- [ ] No invisible characters: zero-width joiners, soft hyphens, bidi controls
      and non-breaking spaces ride along on a paste, break applicant tracking
      systems, and are visible to anyone who looks. The linter fails on them.
- [ ] If this is a rewrite: every number, date, name and scope in the source is
      still there and still says the same thing.
- [ ] No denial used as a drum-roll: "Not a trial: the output ships." Rewrite as
      two sentences, or cut the denial. The colon is doing an em-dash's job and
      it survives every rule written about em-dashes.
- [ ] Every "studies show" has a name/number/date, or is cut.
- [ ] Nothing announces its own significance — no "marks a pivotal moment", no trailing ", highlighting…", no "the key point is".
- [ ] No faux-insight setup ("what most people get wrong", "here's what nobody tells you").
- [ ] Every generic-sounding sentence survives the portability test, or it's been cut or made specific.
- [ ] If editing someone else's draft: they'd still recognise it as theirs.
- [ ] It survives the read-aloud test and it's been cut by ~10–30% from the draft.
