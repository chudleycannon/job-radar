"""A refusal to sponsor was being reported as an offer to sponsor.

`_WILL_SPONSOR` opened with `(?:can|able to|will|do|happy to|willing to)\\s+
(?:\\w+\\s+){0,2}?sponsor`, which matches the negated form as readily as the
positive one: "will not sponsor", "do not sponsor", "unable to sponsor".
`_NO_SPONSOR` did not save it, because that pattern wanted a noun --
visa, sponsorship, work permit -- after the verb, and these phrasings have
none.

So a posting that says

    Employer will not sponsor applicants for this position.

came back as `sponsorship offered`, was flagged to the reader as such, and was
given +12 in `score` for "says it will sponsor". That is the exact opposite of
the truth, on the single fact that decides whether an application is possible
at all, and the sentence is standard American boilerplate: it is on the path
of any reader looking at US roles, not only one who needs a visa.

Three of six real refusal phrasings did this at the commit before the fix.

The examples here are the phrasings that were wrong, plus the ones that were
already right, because the risk in fixing it is over-firing: a pattern that
swallows genuine offers would hide every role a sponsoring employer posts,
which is the same damage in the other direction.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Job          # noqa: E402
from jobradar.screen import work_rights  # noqa: E402

REFUSALS = (
    # The three that read as an offer before the fix.
    "Employer will not sponsor applicants for this position.",
    "We are unable to sponsor candidates at this time.",
    "We do not sponsor work passes.",
    # The three that were already read correctly, kept so a change that fixes
    # the first three by breaking these cannot pass.
    "We are not able to sponsor visas for this role.",
    "This employer will not sponsor an employment visa.",
    "We are unable to provide visa sponsorship.",
    "No visa sponsorship is available for this position.",
)

OFFERS = (
    "We are happy to sponsor the right candidate.",
    "We will sponsor a skilled worker visa for this role.",
    "Visa sponsorship is available for exceptional candidates.",
)


def _rights(text):
    return work_rights(Job(company="Acme", title="Engineer",
                           url="https://x/1", platform="ashby",
                           description=text))


def test_every_way_of_refusing_reads_as_a_refusal():
    wrong = [t for t in REFUSALS if _rights(t) != "no sponsorship"]
    assert not wrong, f"read as something other than a refusal: {wrong}"


def test_no_refusal_is_ever_reported_as_an_offer():
    """Stated separately from the test above because this is the damage.

    "Not stated" is a miss and the reader can go and look. "Offered" when the
    advert says the opposite is a lie the reader has no reason to check.
    """
    lies = [t for t in REFUSALS if _rights(t) == "sponsorship offered"]
    assert not lies, f"reported as an offer to sponsor: {lies}"


def test_a_genuine_offer_is_still_an_offer():
    """The over-fire direction, which would be just as bad.

    A pattern that catches "will not sponsor" by catching "sponsor" would hide
    every role a sponsoring employer posts from the readers who need them
    most.
    """
    missed = [t for t in OFFERS if _rights(t) != "sponsorship offered"]
    assert not missed, f"a genuine offer stopped reading as one: {missed}"


def test_a_refusal_quoted_about_another_role_is_not_this_advert():
    """A careers page describing another team's policy must not hide this
    team's role, and must not be read as an offer either.

    This used to assert only `!= "sponsorship offered"`, which "no
    sponsorship" satisfies, so the failure it was named after passed here
    unseen. `work_rights` did return "no sponsorship" on the text below: the
    incidental guard knew "works with X" and "e.g." and nothing about a
    clause naming a separate programme.

    Fixing that exposed a worse one. Once the refusal is ruled incidental the
    text falls through to `_WILL_SPONSOR`, which matches "will not sponsor" as
    readily as "will sponsor", so the same advert then reported that this
    employer WOULD sponsor: the opposite of the truth, on the one fact the
    gate exists for. Both branches carry the guard now, and this asserts the
    exact answer rather than the absence of one wrong one.
    """
    text = ("We are hiring a Platform Engineer in London. Note: our US "
            "graduate scheme is a separate programme and that employer will "
            "not sponsor applicants for those positions.")
    assert _rights(text) == "", _rights(text)


def test_a_refusal_that_really_is_this_advert_is_still_a_refusal():
    """The half that keeps the guard honest. A pattern loose enough to excuse
    a real refusal shows somebody a role that will reject them."""
    assert _rights("We cannot sponsor visas for other roles but can for this "
                   "one.") == "no sponsorship"
    for t in REFUSALS:
        assert _rights(t) == "no sponsorship", t
