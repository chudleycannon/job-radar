#!/usr/bin/env python3
"""Slop detector — a dependency-free self-test for the natural-writing skill.

Run it on a draft to get an objective report of the machine-tells the skill
cares about, so you know exactly what to fix before looping again:

    python3 detect.py path/to/draft.md
    python3 detect.py --text "paste some prose here"

It uses ONLY the Python standard library, so it runs anywhere. It does NOT
prove a text is or isn't AI — it flags the concrete patterns that push prose
toward "machine-written," each tied to a fixable location. Lower score = more
human-sounding. Exit code is 0 if the draft passes the bar, 1 if it needs work.

Optional, only if `transformers`+`torch` happen to be installed:
    python3 detect.py draft.md --perplexity
adds GPT-2 perplexity/burstiness (the signal classic detectors use). Skip it
if the libraries aren't present — the core report stands on its own.
"""
import sys, re, json, argparse, math
from collections import Counter

# ---- vocabulary (kept in sync with references/ai-tells.md) ----
TELL_WORDS = [
    "delve", "underscore", "boasts", "showcase", "leverage", "foster", "garner",
    "robust", "crucial", "pivotal", "vibrant", "meticulous", "seamless",
    "comprehensive", "nuanced", "multifaceted", "enhance", "bolster", "utilize",
    "facilitate", "endeavor", "myriad", "plethora", "tapestry", "landscape",
    "realm", "mosaic", "ecosystem", "symphony", "labyrinth", "beacon",
    "cornerstone", "bedrock", "testament", "kaleidoscope", "odyssey",
    "game-changer", "game changer", "elevate", "unlock", "harness", "navigate",
    "curated", "artisanal", "bespoke",
]
THROAT_CLEARING = [
    r"in today'?s (?:fast-paced |digital |modern )?(?:world|landscape|age|era)",
    r"in the (?:ever-)?(?:evolving|changing) (?:world|landscape) of",
    r"it'?s (?:important|worth) (?:to note|noting) that",
    r"when it comes to\b",
    r"this (?:article|post|guide|piece) will (?:explore|discuss|cover|delve)",
]
HEDGES = ["often", "typically", "generally", "usually", "arguably", "perhaps",
          "somewhat", "relatively", "in some cases", "may", "might", "can be",
          "tends to", "some would argue", "it could be said"]
VAGUE_ATTRIB = [r"studies (?:show|have shown|suggest)", r"experts (?:say|agree|believe)",
                r"research(?:ers)? (?:show|say|suggest|indicate)", r"many (?:people )?believe",
                r"it is (?:widely )?(?:known|believed|understood)", r"scientists (?:say|believe)"]

def words(t): return re.findall(r"\b[\w'-]+\b", t)
def sentences(t):
    t = re.sub(r"\s+", " ", t.strip())
    return [s for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]

def find_all(patterns, t, flags=re.I):
    hits = []
    for p in patterns:
        for m in re.finditer(p, t, flags):
            hits.append(m.group(0))
    return hits

def stdev(xs):
    if len(xs) < 2: return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5

def analyze(text):
    wl = words(text)
    wc = max(1, len(wl))
    sents = sentences(text)
    slens = [len(words(s)) for s in sents]
    per1k = 1000.0 / wc

    checks = []  # (name, severity, count, detail, points)  severity: FAIL/WARN/OK

    # 1. tell-words
    tw = [w for w in wl if w.lower() in TELL_WORDS]
    tw += [h for h in ["game changer", "game-changer"] if h in text.lower()]
    dens = len(tw) * per1k
    sev = "FAIL" if dens > 4 else ("WARN" if dens > 1.5 else "OK")
    checks.append(("tell-words", sev, len(tw), (", ".join(sorted(set(w.lower() for w in tw))[:8]) or "none"),
                   min(30, len(tw) * 4)))

    # 2. throat-clearing openers/transitions
    tc = find_all(THROAT_CLEARING, text)
    sev = "FAIL" if tc else "OK"
    checks.append(("throat-clearing", sev, len(tc), (", ".join(tc[:4]) or "none"), len(tc) * 12))

    # 3. signature constructions
    not_only = len(re.findall(r"not only\b.{0,60}?\bbut also\b", text, re.I | re.S))
    neg_pivot = len(re.findall(r"\b(it'?s|its)\s+not\s+(just\s+)?[^.,;]{1,40}[.,;]\s*(it'?s|its)\b", text, re.I))
    # same pivot with any subject: "the hard-won part was not adoption, it was proving X"
    neg_pivot += len(re.findall(r"\b(?:was|is)\s+not\s+[^,.;]{2,40},\s+it\s+(?:was|is)\b", text, re.I))
    whether = len(re.findall(r"whether you'?re\b[^.]{0,40}\bor\b", text, re.I))
    sig = not_only + neg_pivot + whether
    sev = "FAIL" if sig else "OK"
    detail = []
    if not_only: detail.append(f"'not only...but also' x{not_only}")
    if neg_pivot: detail.append(f"'it's not X, it's Y' x{neg_pivot}")
    if whether: detail.append(f"'whether you're...or' x{whether}")
    checks.append(("signature-constructions", sev, sig, (", ".join(detail) or "none"), sig * 15))

    # 4. sentence-length burstiness (low variance = machine-like)
    sd = stdev(slens)
    cv = sd / (sum(slens) / len(slens)) if slens else 0
    sev = "FAIL" if cv < 0.35 else ("WARN" if cv < 0.5 else "OK")
    checks.append(("rhythm-burstiness", sev, round(cv, 2),
                   f"stdev={sd:.1f}, cv={cv:.2f} (want cv>=0.5; higher=more varied)",
                   (20 if cv < 0.35 else (8 if cv < 0.5 else 0))))

    # 5. over-correction: too many ultra-short sentences (fragments as a NEW tell)
    frag = sum(1 for L in slens if L <= 3)
    frag_ratio = frag / len(slens) if slens else 0
    sev = "WARN" if frag_ratio > 0.25 else "OK"
    checks.append(("fragment-overuse", sev, frag,
                   f"{frag}/{len(slens)} sentences <=3 words ({frag_ratio:.0%}); overuse reads as engineered",
                   (10 if frag_ratio > 0.25 else 0)))

    # 6. hedging density
    hz = [h for h in HEDGES if re.search(r"\b" + re.escape(h) + r"\b", text, re.I)]
    hcount = sum(len(re.findall(r"\b" + re.escape(h) + r"\b", text, re.I)) for h in HEDGES)
    hdens = hcount * per1k
    sev = "WARN" if hdens > 8 else "OK"
    checks.append(("hedging", sev, hcount, (", ".join(hz[:6]) or "none") + f" ({hdens:.1f}/1k)",
                   (10 if hdens > 8 else 0)))

    # 7. vague attribution without specifics
    va = find_all(VAGUE_ATTRIB, text)
    sev = "WARN" if va else "OK"
    checks.append(("vague-attribution", sev, len(va), (", ".join(va[:4]) or "none"), len(va) * 8))

    # 8. em-dash clustering
    em = text.count("—") + text.count(" - ")
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    em_per_para = em / max(1, len(paras))
    sev = "WARN" if em_per_para > 1.5 else "OK"
    checks.append(("em-dash-clustering", sev, em, f"{em} em-dashes over {len(paras)} paragraphs", (8 if em_per_para > 1.5 else 0)))

    # 8b. colon-reveal overuse (the "statement: elaboration" construction, a tic when repeated)
    # a letter/paren, then ':', then whitespace and content. Skips URLs (http://) and times (5:20).
    cr = len(re.findall(r"[A-Za-z)]:\s+\S", text))
    cr_dens = cr / max(1, wc) * 100
    # density-aware: a few colons in a long piece is fine; a cluster in a short span is the tic.
    sev = "FAIL" if (cr_dens >= 4 or cr >= 6) else ("WARN" if cr >= 3 else "OK")
    pts = (min(20, cr * 3) if sev == "FAIL" else (min(6, (cr - 2) * 2) if sev == "WARN" else 0))
    checks.append(("colon-reveal", sev, cr,
                   f"{cr} 'statement: elaboration' colons ({cr_dens:.1f}/100 words); repeated colon-reveals read as an engineered tic",
                   pts))

    # 8c. characters you cannot see.
    #
    # Nothing to do with a statistical watermark, which lives in token choice
    # and cannot be found by looking at bytes. This is the crude kind: zero
    # width joiners, soft hyphens, bidi controls and non-breaking spaces that
    # ride along when text is pasted out of a chat window or a PDF.
    #
    # Three separate reasons to care, and only one of them is provenance.
    # They break an applicant tracking system's parsing, so a CV with a
    # non-breaking space inside a date range can lose the date range. They
    # survive copy and paste, so they travel into an email nobody inspected.
    # And they are trivially visible to anyone who looks, which makes them
    # worse than useless as a disguise and awkward to explain.
    #
    # Split by severity because a non-breaking space is often nobody's
    # decision, while a zero width joiner in prose is never an accident of
    # typing.
    _INVISIBLE = {
        "\u200b": "zero width space", "\u200c": "zero width non-joiner",
        "\u200d": "zero width joiner", "\ufeff": "zero width no-break space",
        "\u00ad": "soft hyphen", "\u2060": "word joiner",
        "\u202a": "bidi override", "\u202b": "bidi override",
        "\u202c": "bidi override", "\u202d": "bidi override",
        "\u202e": "bidi override", "\u2066": "bidi isolate",
        "\u2067": "bidi isolate", "\u2068": "bidi isolate",
        "\u2069": "bidi isolate",
    }
    _ODD_SPACE = {"\u00a0": "non-breaking space", "\u2007": "figure space",
                  "\u2009": "thin space", "\u202f": "narrow no-break space"}
    inv = Counter(ch for ch in text if ch in _INVISIBLE)
    odd = Counter(ch for ch in text if ch in _ODD_SPACE)
    if inv:
        detail = ", ".join(f"{_INVISIBLE[c]} x{n}" for c, n in inv.most_common(3))
        sev, pts = "FAIL", min(15, sum(inv.values()) * 3)
    elif odd:
        detail = ", ".join(f"{_ODD_SPACE[c]} x{n}" for c, n in odd.most_common(3))
        sev, pts = "WARN", 3
    else:
        detail, sev, pts = "none", "OK", 0
    checks.append(("invisible-characters", sev, sum(inv.values()) + sum(odd.values()),
                   detail, pts))

    # 8b-ii. negation-colon: a denial used as a drum-roll.
    #
    # "Not a trial: the team's daily output ships to customers."
    # "Not as a pilot: the team's daily output ships to customers."
    # "No small thing: it took two years."
    #
    # The colon-reveal check above is density-aware, so one of these scores
    # nothing and passes at 3/100. Density is the wrong test. One is already
    # the tic, because the thing before the colon is not a clause doing work,
    # it is a denial planted to make the clause after it land harder. It is
    # the same move as an em-dash aside and it survives every rule written
    # about em-dashes.
    #
    # Deliberately narrow. An earlier draft flagged any short verbless
    # fragment and caught "Responsibilities:" (a heading) and "What changed:"
    # (a clause whose verb was not in the list). Negation-led is the shape
    # that actually reads as engineered, so that is what this asks about, and
    # the content has to be on the same line, which a heading's is not.
    neg_hits = [m.group(1).strip() for m in re.finditer(
        r"(?:(?<=[.!?]\s)|(?<=\n)|^)((?:Not|No|Never|Nothing|Neither|Hardly)\b"
        r"[^.!?:\n]{0,44}):[ \t]+\S", text)]
    sev = "FAIL" if neg_hits else "OK"
    checks.append(("negation-colon", sev, len(neg_hits),
                   (f"{len(neg_hits)} denial-then-colon drum-roll(s): "
                    + "; ".join(f'"{h}:"' for h in neg_hits[:3])
                    if neg_hits else "none"),
                   min(15, len(neg_hits) * 8)))

    # 8a-ii. corrective antithesis: defining a thing by what it is not, in the
    #        same breath. "The opt-out is one line, not six." / "It prunes its
    #        own boards but never gains new ones." / "A feature, not a bug."
    #
    #        Same family as "it's not X, it's Y", which is already caught, but
    #        the trailing and mid-sentence forms slipped through and are the
    #        ones that actually get written. One is ordinary English. Two in a
    #        short piece is a habit, and it reads as rhetorical balancing
    #        rather than as someone saying what happened.
    corrective = 0
    corr_hits = []
    for pat in (r",\s+not\s+(?:just\s+|merely\s+|only\s+)?[^.;:!?\n]{1,34}(?=[.;!?\n]|$)",
                r"\bbut\s+(?:never|not|no longer)\s+[^.;:!?\n]{1,34}(?=[.;!?\n]|$)"):
        for m in re.finditer(pat, text, re.I):
            corrective += 1
            corr_hits.append(" ".join(m.group(0).split())[:40])
    c_dens = corrective / max(1, wc) * 100
    # Density only counts once there is enough text for it to mean anything.
    # Otherwise a single ordinary "X, not Y" in a two-line note reads as a
    # crisis.
    sev = ("FAIL" if (corrective >= 3 or (wc >= 120 and c_dens >= 1.0))
           else ("WARN" if corrective == 2 else "OK"))
    checks.append(("corrective-antithesis", sev, corrective,
                   (f"{corrective} 'X, not Y' / 'but never Y' construction(s): "
                    f"{'; '.join(corr_hits[:3])}. Say what it is and stop"
                    if corrective else "none"),
                   (min(14, corrective * 4) if sev == "FAIL"
                    else (4 if sev == "WARN" else 0))))

    # 8b-ii. the reveal opener: a sentence whose job is to spring a mild
    #        surprise before the fact arrives. "Turns out the hard part was
    #        paying for it." / "What shaped it was the token bill." / "The
    #        real problem was never the models."
    #
    #        These read as blog scaffolding because they withhold for a beat
    #        and then pay off, which is a structure rather than a thought. The
    #        cleft version ("What X was Y", "It was Y that X") is the same move
    #        in different clothes, which is why both are matched here: fixing
    #        one by rewriting into the other changes nothing a reader notices.
    reveal_pats = [
        r"\b(?:it )?turns out\b",
        r"\bas it turn(?:s|ed) out\b",
        r"^\s*(?:and )?(?:the )?(?:real|hard(?:est)?|interesting|surprising|tricky|actual|funny)\s+"
        r"(?:part|thing|problem|question|answer|bit|issue)\s+(?:was|is|turned out)",
        r"\bwhat (?:shaped|drove|made|killed|saved|changed|mattered)\b[^.?!]{0,40}\bwas\b",
        r"\bthe (?:real|actual|whole) (?:problem|point|trick|answer|reason)\b[^.?!]{0,30}\b(?:was|is)\b",
    ]
    rev = 0
    rev_hits = []
    for pat in reveal_pats:
        for m in re.finditer(pat, text, re.I | re.M):
            rev += 1
            rev_hits.append(" ".join(m.group(0).split())[:44])
    sev = "FAIL" if rev >= 2 else ("WARN" if rev == 1 else "OK")
    checks.append(("reveal-opener", sev, rev,
                   (f"{rev} surprise-reveal construction(s): "
                    f"{'; '.join(rev_hits[:3])}. State the fact instead; the "
                    f"cleft rewrite is the same move in different clothes"
                    if rev else "none"),
                   (10 if sev == "FAIL" else (5 if sev == "WARN" else 0))))

    # 8c. over-explain / significance-clause: a trailing appositive that restates why
    #     the thing matters, tacked on after a comma, often parroting the source
    #     ("...compliance content for PCI, the rules that keep organisations in control").
    #     Passes a linter, fails a human read. State the thing and trust the reader.
    over_pats = [
        r",\s+(?:the|a|an|those|these|the kind of|the sort of)\s+\w+(?:\s+\w+){0,3}?\s+(?:that|which)\s+(?:\w+\s+){0,3}?(?:keeps?|makes?|gives?|allows?|ensures?|helps?|lets?|drives?|enables?|powers?|brings?|underpins?|guarantees?|means?)\b",
        r",\s+which\s+is\s+(?:why|what|how|where)\b",
        r"\bthat\s+keeps?\b[\w\s,]{0,40}\bin\s+(?:check|control|line)\b",
    ]
    over = sum(len(re.findall(p, text, re.I)) for p in over_pats)
    sev = "FAIL" if over >= 3 else ("WARN" if over >= 1 else "OK")
    opts = (min(15, over * 5) if sev == "FAIL" else (over * 3 if sev == "WARN" else 0))
    checks.append(("over-explain", sev, over,
                   (f"{over} trailing 'significance' clause(s), e.g. ', the X that keeps…' — state it and trust the reader" if over else "none"),
                   opts))

    # 8d. polished-cadence: a tidy parallel list (often three gerund phrases) that lands
    #     on a neat payoff. Passes linters; it's the "too polished" ear-catch. Low-weight nudge,
    #     the real catch is the Tier-2 adversarial read (see ai-tells.md "polished-cadence").
    pc = len(re.findall(r"\b\w+ing\b[^,.;:!?]{2,70},\s+\w+ing\b[^,.;:!?]{2,70},\s+(?:and\s+)?\w+ing\b", text, re.I))
    # imperative variant: "Scope the problem, structure the context, anticipate how it fails, and you get X"
    # Must OPEN on a bare command verb, otherwise ordinary factual lists ("I hire, run X, and coach Y")
    # trip it — those are content, not cadence.
    _IMP = (r"scope|structure|anticipate|build|ship|make|take|give|start|stop|think|write|keep|let|find|focus|"
            r"pick|choose|treat|assume|expect|design|plan|measure|define|remove|reduce|automate|delegate")
    pc += len(re.findall(rf"(?:^|[.!?]\s+)(?:{_IMP})\s+[^,.;:!?]{{2,60}},\s+\w+\s+[^,.;:!?]{{2,60}},\s+(?:and\s+)?\w+\s+",
                         text, re.I))
    # a triad that resolves into a quotable kicker ("...and you get X rather than Y")
    pc += len(re.findall(r",\s+and\s+(?:you|it|that)\s+(?:get|gets|becomes?|means?)\b[^.!?]{0,60}\brather than\b", text, re.I))
    # antithesis-as-insight: "the hard part was X rather than Y", "it is not about X, it is about Y"
    pc += len(re.findall(r"\bthe (?:hard|real|tricky|difficult) (?:part|bit|thing)\b[^.!?]{0,60}\b(?:rather than|not)\b", text, re.I))
    sev = "WARN" if pc >= 1 else "OK"
    pcpts = min(6, pc * 4) if pc else 0
    checks.append(("polished-cadence", sev, pc,
                   (f"{pc} triadic parallel phrase list(s) or quotable payoff, e.g. 'doing X, keeping Y, and working Z' or 'do A, do B, do C, and you get X rather than Y' — break the pattern, let it end flat" if pc else "none"),
                   pcpts))

    # 8e. crafted-phrasing: stock idioms and neat constructions that dress a plain fact up
    #     as an insight. Individually small; as a habit they are the "sounds authored" tell.
    #     ("Hands-on where it earns its keep." / "the gates that let the team ship safely")
    CRAFTED = [
        r"\bearns? its keep\b", r"\bpunch(?:es|ing)? above\b", r"\bmoves? the needle\b",
        r"\bheavy lifting\b", r"\bsecret sauce\b", r"\bnorth star\b", r"\bforce multiplier\b",
        r"\bwhere the rubber meets\b", r"\bbread and butter\b", r"\bhit the ground running\b",
        r"\bstep change\b", r"\bat the coalface\b", r"\bsingle pane of glass\b",
        r"\bbest of both worlds\b", r"\braise the bar\b", r"\bmove(?:s|d)? the dial\b",
        # a purpose clause dressing up a plain noun: "the gates that let the team ship safely"
        r"\bthe \w+ that (?:lets?|keeps?|makes?|allows?|enables?|gives?)\s+\w+",
        # "turned X into something the team solves without me"
        r"\binto something (?:the|that|which)\b",
        # metaphor-as-placement: "Acceleration sits where it lands", "sits at the intersection of"
        r"\bsits (?:at the intersection|where|squarely)\b", r"\blives at the (?:intersection|heart)\b",
        # aphoristic generalisation: "Work that unblocks other teams tends to be under-owned"
        r"\b(?:work|teams?|people|systems?|things) that [^.,;]{5,50} (?:tends? to be|is usually|are usually|rarely gets?)\b",
    ]
    cf = find_all(CRAFTED, text)
    sev = "WARN" if cf else "OK"
    checks.append(("crafted-phrasing", sev, len(cf),
                   (", ".join(sorted(set(c.lower() for c in cf))[:5]) if cf else "none")
                   + (" — say the plain thing instead" if cf else ""),
                   min(12, len(cf) * 4)))

    # 8g. faux-insight setup: a line that casts the writer as the lone person who knows,
    #      then delivers an ordinary claim ("what most people get wrong", "here's what nobody
    #      tells you"). Also the rhetorical wind-up ("what if I told you", "plot twist:").
    #      Both are throat-clearing that flatters. Cut the setup; let the claim stand alone.
    faux_pats = [
        r"\b(?:here'?s |and )?what (?:most people|everyone|nobody|no one) (?:gets? wrong|misses?|tells? you|talks? about)\b",
        r"\bthe part (?:most people|everyone|nobody) (?:skips?|misses?|gets? wrong)\b",
        r"\bwhat (?:most people|everyone|nobody|no one) (?:don'?t|doesn'?t|won'?t) (?:tell|say|realize|realise|know)\b",
        r"\bwhat if i told you\b", r"\bplot twist\s*:", r"\bthink about it\s*:",
        r"\bhere'?s the (?:thing|kicker|secret|catch)\b",
        r"\blet that sink in\b", r"\bread that again\b",
    ]
    faux = find_all(faux_pats, text)
    sev = "FAIL" if len(faux) >= 2 else ("WARN" if faux else "OK")
    checks.append(("faux-insight", sev, len(faux),
                   ((", ".join(sorted(set(f.lower().strip() for f in faux))[:4])
                     + " — cut the setup, state the claim") if faux else "none"),
                   min(15, len(faux) * 7)))

    # 8h. puffery: telling the reader a thing is significant instead of showing it. Two shapes —
    #      the importance formula ("stands as a testament", "marks a pivotal moment") and the
    #      trailing -ing clause that pretends to analyse ("…, highlighting the team's commitment").
    #      Distinct from over-explain, which catches the appositive form (", the X that keeps…").
    puff_pats = [
        r"\b(?:stands?|serves?) as a testament\b", r"\bmarks? a (?:pivotal|defining|watershed) moment\b",
        r"\bplays? a (?:vital|crucial|key|pivotal) role\b", r"\bsolidif(?:ies|y|ying) its position\b",
        r"\bunderscor\w* (?:its|the) (?:significance|importance)\b",
        r"\bcements? (?:its|their) (?:position|status|place)\b",
        r",\s+(?:highlighting|underscoring|reflecting|showcasing|demonstrating|signal(?:l)?ing|emphasizing|emphasising|illustrating)\b",
    ]
    puff = find_all(puff_pats, text)
    sev = "FAIL" if len(puff) >= 3 else ("WARN" if puff else "OK")
    checks.append(("puffery", sev, len(puff),
                   ((", ".join(sorted(set(x.lower().strip(" ,") for x in puff))[:4])
                     + " — state the fact, let the reader judge") if puff else "none"),
                   min(15, len(puff) * 5)))

    # 8i. interpretive metadiscourse: stepping outside the subject to tell the reader how much
    #      weight to give what they just read. If the point is clear this is noise; if it isn't,
    #      the fix is more support, not a label. Borrowed from petergyang/no-ai-slop (MIT), which
    #      names this better than anything we had.
    meta_pats = [
        r"\bthat last (?:part|bit|point) matters\b", r"\bthis (?:distinction|difference|part) matters\b",
        r"\bthe key (?:point|thing|insight) (?:here )?is\b", r"\bas you can see\b",
        r"\bwhich is (?:the )?(?:important|crucial|key) (?:part|bit|point)\b",
        r"\bit'?s worth (?:noting|repeating|remembering)\b",
        r"\bmore than it (?:sounds|seems|appears)\b",
        r"\bin other words\b",
    ]
    meta = find_all(meta_pats, text)
    sev = "FAIL" if len(meta) >= 3 else ("WARN" if len(meta) >= 1 else "OK")
    checks.append(("metadiscourse", sev, len(meta),
                   ((", ".join(sorted(set(m.lower().strip() for m in meta))[:4])
                     + " — delete it, or replace with the support it is standing in for") if meta else "none"),
                   min(12, len(meta) * 4)))

    # 9. contractions present (their absence is a tell)
    contr = len(re.findall(r"\b\w+['’](t|s|re|ve|ll|d|m)\b", text, re.I))
    sev = "WARN" if contr == 0 and wc > 60 else "OK"
    checks.append(("contractions", sev, contr, ("none — formal/robotic" if contr == 0 else f"{contr} present"),
                   (10 if (contr == 0 and wc > 60) else 0)))

    # 10. list-heaviness (prose chopped into bullets)
    lines = [l for l in text.splitlines() if l.strip()]
    bullets = sum(1 for l in lines if re.match(r"\s*([-*•]|\d+\.)\s", l))
    ratio = bullets / max(1, len(lines))
    sev = "WARN" if ratio > 0.5 and len(lines) > 6 else "OK"
    checks.append(("list-heaviness", sev, bullets, f"{bullets}/{len(lines)} lines are bullets", (8 if (ratio > 0.5 and len(lines) > 6) else 0)))

    # 11. promotional gush (social/marketing slop the word-list misses: emoji,
    #     hashtag stacks, "thrilled to announce" openers, exclamation spray).
    promo_open = find_all([r"\b(thrilled|excited|humbled|honou?red|proud|delighted|pumped)\s+to\s+(announce|share|reveal)",
                           r"\bwithout further ado\b", r"\bhere'?s to\b", r"\bnext chapter\b"], text)
    emoji = re.findall(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤⭐✨]", text)
    hashtags = re.findall(r"(?:^|\s)#\w+", text)
    exclam = text.count("!")
    excl_dense = exclam * per1k > 6
    gush = len(promo_open) * 15 + min(12, len(emoji) * 4) + (10 if len(hashtags) >= 3 else 0) + (6 if excl_dense else 0)
    sev = "FAIL" if gush >= 15 else ("WARN" if gush > 0 else "OK")
    gdetail = []
    if promo_open: gdetail.append(f"opener '{promo_open[0]}'")
    if emoji: gdetail.append(f"{len(emoji)} emoji")
    if len(hashtags) >= 3: gdetail.append(f"{len(hashtags)} hashtags")
    if excl_dense: gdetail.append(f"{exclam} '!'")
    checks.append(("promotional-gush", sev, gush, (", ".join(gdetail) or "none"), min(30, gush)))

    # 12. low concreteness (generic abstraction with no anchors a real writer
    #     would have: numbers, names, or first-person). The essay-mill tell.
    digits = len(re.findall(r"\b\d[\d.,]*\b", text))
    firstp = len(re.findall(r"\b(I|I'?m|I'?ve|I'?ll|I'?d|my|me|we|our|us)\b", text))
    # proper nouns: capitalised word that is NOT the first token of its sentence
    propn = 0
    for s in sents:
        toks = re.findall(r"\b[A-Za-z][\w'-]*\b", s)
        for i, tk in enumerate(toks):
            if i > 0 and re.match(r"[A-Z][a-z]+", tk):
                propn += 1
    anchors = digits + firstp + propn
    density100 = anchors * 100.0 / wc
    lowconc = wc > 150 and density100 < 1.5
    sev = "FAIL" if lowconc else ("WARN" if (wc > 150 and density100 < 2.5) else "OK")
    checks.append(("low-concreteness", sev, round(density100, 2),
                   f"{anchors} anchors (nums+names+first-person) = {density100:.1f}/100 words; want >=2.5",
                   (18 if lowconc else (6 if (wc > 150 and density100 < 2.5) else 0))))

    score = min(100, sum(c[4] for c in checks))
    return {"word_count": wc, "sentences": len(sents), "checks": checks, "score": score}

def maybe_perplexity(text):
    try:
        import torch  # noqa
        from transformers import GPT2LMHeadModel, GPT2TokenizerFast
    except Exception:
        return None
    tok = GPT2TokenizerFast.from_pretrained("gpt2")
    model = GPT2LMHeadModel.from_pretrained("gpt2"); model.eval()
    def ppl(t):
        import torch
        ids = tok(t, return_tensors="pt").input_ids
        if ids.shape[1] < 2: return float("nan")
        with torch.no_grad():
            out = model(ids[:, :1024], labels=ids[:, :1024])
        return math.exp(out.loss.item())
    sents = sentences(text)
    sp = [ppl(s) for s in sents if len(words(s)) >= 3]
    sp = [x for x in sp if not math.isnan(x)]
    return {"mean_perplexity": round(ppl(text), 1),
            "sentence_ppl_burstiness": round(stdev(sp), 1),
            "note": "higher perplexity & burstiness = more human (GPT-2 proxy)"}

PASS_BAR = 20  # score at or below this = passes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--text")
    ap.add_argument("--perplexity", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.text is not None:
        text = a.text
    elif a.path:
        text = open(a.path, encoding="utf-8").read()
    else:
        text = sys.stdin.read()

    r = analyze(text)
    if a.perplexity:
        p = maybe_perplexity(text)
        if p: r["perplexity"] = p

    has_fail = any(s == "FAIL" for (_, s, _, _, _) in r["checks"])
    passed = (r["score"] <= PASS_BAR) and not has_fail
    r["passed"] = passed

    if a.json:
        r["checks"] = [{"name": n, "severity": s, "count": c, "detail": d, "points": pt}
                       for (n, s, c, d, pt) in r["checks"]]
        print(json.dumps(r, indent=2));
        sys.exit(0 if passed else 1)

    print(f"\n  natural-writing slop report  —  {r['word_count']} words, {r['sentences']} sentences")
    print("  " + "-" * 66)
    icon = {"OK": "✓", "WARN": "!", "FAIL": "✗"}
    for (n, s, c, d, pt) in r["checks"]:
        print(f"  [{icon[s]}] {n:24s} {s:5s}  {d}")
    print("  " + "-" * 66)
    verdict = "PASS" if passed else "NEEDS WORK"
    reason = "" if passed else ("  (has a FAIL)" if has_fail else "  (score over bar)")
    print(f"  SLOP SCORE: {r['score']}/100   (bar: <= {PASS_BAR}, and no FAILs)   ->  {verdict}{reason}")
    if "perplexity" in r:
        pp = r["perplexity"]
        print(f"  GPT-2 perplexity: {pp['mean_perplexity']}   burstiness: {pp['sentence_ppl_burstiness']}   (higher = more human)")
    print()
    print("  Fix the FAIL/WARN lines above, then re-run. Aim for no FAILs and score <= 20.")
    print("  Note: a PASS means no crude tells were found — not a guarantee of 'human'.")
    print("  The binding check is the adversarial read described in SKILL.md.\n")
    sys.exit(0 if passed else 1)

if __name__ == "__main__":
    main()
