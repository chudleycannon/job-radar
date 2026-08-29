# The AI-tells reference

A catalogue of the patterns that make prose read as machine-written, plus fixes. Two things to hold in mind while using it:

1. **No single item here is proof of anything.** Humans use em-dashes. Humans say "crucial." The tell is never one word — it's the *density* and *co-occurrence* of these patterns. A page with three "delve"s, a rule-of-three in every paragraph, and a restate-everything conclusion reads as AI. One em-dash does not.
2. **This is a diagnostic list, not a banned-words filter.** Don't find-and-replace your way through it. If you swap "delve" for "explore" and change nothing else, you still sound like a machine that owns a thesaurus. Fix the underlying habit — reaching for inflated diction, hedging instead of committing, explaining what the reader already knows.

---

## 1. Inflated diction (the "elevation" reflex)

Grand words bolted onto flat subjects. The machine reaches for the fancier synonym by default; a person reaches for the word they'd actually say.

**Verbs/adjectives that signal reaching:** delve, underscore, boasts, showcase, leverage (as a verb), foster, garner, robust, crucial, pivotal, vibrant, meticulous, seamless, comprehensive, nuanced, multifaceted, enhance, bolster, utilize, facilitate, endeavor, myriad, plethora.

**"Tapestry nouns" — abstractions that lend false weight:** tapestry, landscape, realm, mosaic, ecosystem, symphony, labyrinth, beacon, cornerstone, bedrock, testament, kaleidoscope, odyssey.

Fix: use the plain word. *utilize → use. facilitate → help. purchase → buy. endeavored to → tried to. a myriad of → many, or a number.* And when you catch a "tapestry noun," ask what concrete thing it's standing in for and name that instead.

> **Before:** "We endeavored to facilitate a more streamlined utilization of available resources."
> **After:** "We tried to make better use of what we had."

---

## 2. Throat-clearing and connective clichés

Openers and transitions that fill space before the point arrives.

- "It's important to note that…"
- "In today's fast-paced world / digital landscape…"
- "In the ever-evolving landscape of…"
- "When it comes to…"
- "Whether you're X or Y…"
- "plays a crucial / pivotal role in…"
- "stands as a testament to…"
- "It's worth noting that…"
- "This article will explore / discuss…"

Fix: delete the opener and start on the actual sentence. The point is almost always the second sentence — promote it.

> **Before:** "In today's fast-paced digital landscape, email remains a crucial tool that plays a pivotal role in how businesses communicate."
> **After:** "Email is still how most business gets done."

---

## 3. The three signature constructions

These three are weighted heavily by "slop" scorers because they're so distinctive. If you write one, you've likely written the others nearby.

**Rule-of-three triples** — "adjective, adjective, and adjective," "verb, verb, and verb," relentlessly.
> **Before:** "The tool is powerful, intuitive, and versatile, helping teams plan, build, and ship faster."
> **After:** "The tool is fast, and it stays out of your way."

**"Not only… but also" / forced parallelism.**
> **Before:** "The policy not only affected businesses but also influenced everyday citizens."
> **After:** "The policy hit businesses first. Then it trickled down to everyone else."

**The negation pivot — "It's not just X, it's Y" / "It's not X. It's Y."** This one is a signature AI cadence; treat it as almost a hard stop.
> **Before:** "It's not just a notebook. It's a system for thinking."
> **After:** "I use it as a notebook, though it's grown into how I plan my week."

It wears other subjects too, and those are easier to miss because they read as analysis rather than slogan: *"The hard-won part **was not** adoption, **it was** proving output was safe."* Same move, same problem. Say which one it was and drop the discarded half: *"Most of the effort went into proving the output was safe."* The discarded half is almost never doing work; it exists to make the kept half sound arrived-at.

**The polished-cadence tell — tidy parallel lists that resolve into a neat payoff, plus a clever closer on every point.** The sneakiest tell of all, because it passes every automated check; only the ear catches it. Two shapes. (a) A parallel list, often three gerund or noun phrases, that lands on an abstract, quotable kicker: *"Writing things down, keeping the docs and runbooks real, and working so nobody has to be in a room to get unblocked."* (b) Ending point after point on a smooth aphorism: *"…and it's the one I want," "…somewhere I'd fit," "…the thing I wished existed."* The problem is never one line. It's that every sentence lands a little too well, with the balance and payoff of a sentence built to be quoted rather than someone thinking out loud. Fixes: break the parallelism (say two things, not a rhythmic three), and let sentences end flat, on the plain fact, with no kicker. The triad does not have to be gerunds. The **imperative variant** is just as common and slips past a gerund-only filter: *"Scope the problem, structure the context, anticipate how the model fails, and you get production rather than plausible slop."* Three bare-verb commands, then a payoff clause with an antithesis ("X rather than Y") and a coined phrase to land on. Every ingredient of the tell, none of the `-ing` words. Watch especially for the closing shape **"…, and you get X rather than Y"** — the antithesis is doing the work of sounding insightful. Fix by stating the point flat: *"Most of the effort goes into scoping the problem properly and knowing where the model tends to fail before it does."*

**The same instinct shows up smaller, as crafted phrasing.** Not a full triad, just a plain fact dressed up so it sounds earned. Three shapes worth knowing, all caught by `detect.py` as `crafted-phrasing`:

- **Stock idiom as a headline.** *"Hands-on where it earns its keep."* The idiom is doing the work a fact should do. Also: punches above, moves the needle, heavy lifting, secret sauce, north star, force multiplier, step change, bread and butter.
- **A purpose clause bolted to a plain noun.** *"Built the gates **that let the team ship AI output safely**."* Compare *"Added validation gates to the team's authoring workflow."* Same information, no performance. This is the headline-position cousin of the trailing appositive in §4.
- **The neat turn.** *"Turned a recurring problem **into something the team solves without me**."* / *"Pushed the team **from symptom to root cause**."* Both are real things that happened, phrased for the applause rather than the reader.

The giveaway across all three: you could state the fact in fewer words and lose nothing but the flourish. In a CV or a cover letter this matters more than in an essay, because every line is read as a claim, and a claim that sounds performed reads as inflated.

A quick test: if a sentence would work as a motivational poster, cut the poster half. `detect.py` flags the triadic-phrase shape as `polished-cadence`, but most of this is a Tier-2 adversarial-read catch, not a linter one. When a reader says "I can't say why but it sounds like AI," this is almost always what they're feeling.

---

## 4. Structural tells

- **Uniform paragraph length** — every paragraph three or four sentences, every section the same shape. Real writing has a one-line paragraph sitting next to a seven-line one.
- **Restate-everything conclusions** — "Despite its challenges, X remains promising, and its importance will only grow." Cut it. End on your last real point, not a summary of the points above.
- **Bulleting prose that should flow** — chopping connected reasoning into bold-header + colon bullets. Lists are for genuinely list-like things (steps, options, specs). If the items depend on each other, write sentences.
- **Over-explaining / closing every point with its significance** — "…highlighting the enduring importance of collaboration." Trust the reader to see why it matters; delete the significance clause. The sneakiest form is a **trailing appositive tacked on after a comma** that restates what the thing is *for*: "…compliance content for DISA STIG and PCI, the rules that keep regulated organisations in control of what runs in their estate." The facts before the comma are fine; the "…, the rules that keep…" is the tell, and it's worse when it parrots the reader's own words (a job description, a brief) back at them, which reads as reverse-engineered. Fixes: state the thing and stop ("…compliance content for DISA STIG and PCI."). Watch for the family: ", the X that keeps/makes/gives/ensures…", ", which is why/what/how…", "…that keeps them in check/control." `detect.py` flags these as `over-explain`. One occasional appositive is fine; the habit is the tell.

---

## 5. Punctuation and typography

- **Em-dash overuse** — several per paragraph, standing in for commas, colons, and parentheses all at once. Em-dashes are fine; a cluster of them is the tell. Roughly one per few paragraphs is a safe ceiling.
- **Corrective antithesis** — defining a thing by what it is not, in the same breath. "The opt-out is one line, not six." / "It prunes its own boards but never gains new ones." / "A convenience, not the product." Same family as "it's not X, it's Y", which most checkers already catch, but the trailing and mid-sentence forms are the ones people actually write and they slip through. One is ordinary English. Two in a short piece is a habit, and it reads as rhetorical balancing rather than as someone saying what happened. Say what the thing is and stop: "There's one." A genuine factual disambiguation ("Workday is POST, not GET") is not this tell. (`detect.py` flags this as `corrective-antithesis`.)

- **The reveal opener** — a sentence whose job is to withhold for a beat and then spring a mild surprise, before any fact arrives. "Turns out the hard part was paying for it." / "The real problem was never the models." / "What shaped it was the token bill." It reads as blog scaffolding because it is a structure rather than a thought: the surprise is manufactured by word order, not by anything the reader learns. State the fact and let it be interesting on its own ("Screening every role would cost 12 million tokens"). Note that the cleft version ("What X was Y", "It was Y that X") is the same move wearing a different coat, so rewriting one into the other fixes nothing a reader would notice. (`detect.py` flags this as `reveal-opener`.)

- **Colon-reveal overuse** — the "statement: elaboration" move ("The answer was simple: gates." / "Your framing is mine: reduce friction.") used again and again. One is fine; three or more in a short piece reads as an engineered rhythm, a dramatic pause dropped in every few lines. Recast most of them into plain sentences ("The answer was simple. I built gates."); keep the colon for a genuine list or the occasional deliberate reveal. A structural label colon in a skills list or table is not this tell. (`detect.py` flags this as `colon-reveal`.)
- **Curly/smart quotes and apostrophes** where a person typing in a plain editor would produce straight ones — a copy-paste-from-chatbot fingerprint. Match whatever the surrounding context uses.
- **Title Case On Every Heading** — humans usually sentence-case ("Impact of technology," not "The Impact Of Technology And Digitalization").
- **Emoji section bullets/headers** (✅ 🚀 💡 🔑) and reflexive **bold** on every key term. Use sparingly and only where the context actually calls for it.

---

## 6. Tonal tells

**Relentless positivity.** Everything is exciting, delightful, vibrant; nothing is merely fine, boring, or bad. Real assessment includes the negative.
> **Before:** "This charming café offers a delightful array of beverages sure to please any palate."
> **After:** "The coffee's good. The pastries are stale by afternoon."

**Hedging / over-qualifying.** "often," "typically," "generally," "can be," "may," "in some cases," stacked so the writer is never wrong and never says anything.
> **Before:** "This can often be a factor that may, in some cases, contribute to slower performance."
> **After:** "This slows things down."

**Corporate neutrality / no stance.** Presenting "both sides," committing to nothing.
> **Before:** "There are various perspectives on remote work, each with its own merits and drawbacks."
> **After:** "Remote work is better for focus and worse for junior people, who learn by osmosis."

**Vague attribution.** "studies show," "experts agree," "researchers say," "many believe" — with no name, number, or date. Either name the source (person, year, figure) or cut the claim.
> **Before:** "Studies have shown that many users find the feature valuable."
> **After:** "In their March 2025 survey, 61% of the 400 respondents said they'd used it that week."

---

## 7. Telling the reader what to think

Four related habits, all the same underlying move: instead of showing something, the prose announces its significance. A human writer trusts the reader to draw the conclusion. Several of these are borrowed from [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop) (MIT), which names them more precisely than this file used to.

- **Faux-insight setups.** "What most people get wrong…", "Here's what nobody tells you…", "The part everyone misses…". The setup casts the writer as the only person who knows, then delivers an ordinary claim. It's throat-clearing that flatters. Cut it and let the claim stand: "The part everyone misses: distribution is the moat" becomes "Distribution is the moat." `detect.py` flags these as `faux-insight`, along with the wind-up variants ("What if I told you…", "Plot twist:", "Let that sink in").
- **Importance puffery.** "Stands as a testament to", "marks a pivotal moment", "plays a vital role", "solidifies its position", "underscores its significance". State the fact and stop. "The launch marks a pivotal moment for the company" becomes "It's the company's first paid product." Flagged as `puffery`.
- **Superficial analysis.** A trailing `-ing` clause that looks like interpretation and isn't: "…, highlighting the team's commitment to quality", "…, underscoring the shift", "…, reflecting a broader trend". Replace it with the actual consequence: "The launch adds file search, so you can find an old draft without leaving the editor." Also `puffery`. (Related to §4's over-explaining, which catches the appositive form ", the X that keeps…".)
- **Interpretive metadiscourse.** Lines that step outside the subject to direct the reader: "That last part matters more than it sounds", "The key point is", "As you can see", "This distinction matters", and redundant "In other words". If the point is already clear, delete the line. If it isn't, the fix is more support, not a label. Flagged as `metadiscourse`.

## 8. Small mechanical tells

- **Fake-strong verbs.** Reaching for an impressive verb where "is" or "has" is clearer and more honest. "The app serves as a centralised hub for sponsor management" becomes "The app tracks sponsors, drafts and due dates in one place." Watch "serves as", "acts as", "functions as", "leverages", "enables".
- **Synonym cycling.** Rotating terms for variety when the clear word was already right — "the agent… the assistant… the tool…" across three sentences about one thing. Schoolroom advice ("don't repeat yourself") that reads as evasion. Repeat the word.
- **Negative listing.** "Not a framework. Not a library. A way of thinking." Cousin of the negation pivot in §3, and the fix is the same: say the last thing and delete the run-up.
- **Weasel attribution.** "Experts agree", "studies show", "widely regarded as", "industry reports suggest". Name the source, or cut the claim. Never invent one to fill the hole — if the writer has no source, ask. (Also §6; `detect.py` flags it as `vague-attribution`.)

## 9. Why detectors "see" all of this (brief)

AI-text detectors measure two things above all: **predictability** (how closely each word matches what a language model would expect — low = machine-like) and **uniformity** (how little the rhythm, sentence length, and vocabulary vary). Every pattern above is a symptom of one or both: inflated-but-safe diction is predictable; uniform paragraphs and relentless parallelism are uniform.

This is why you can't reliably beat a detector by swapping words — and why you shouldn't try to. Detectors are unreliable (they falsely flag non-native English speakers and neurodivergent writers at high rates) and they change constantly; tuning to today's scorer is a moving target. But the traits that genuinely lower predictability and raise variance — real voice, specific detail, varied rhythm, an actual opinion — are the same traits that make writing *good*. Optimize for the writing, and the detector takes care of itself. See SKILL.md for the positive craft that does this.
