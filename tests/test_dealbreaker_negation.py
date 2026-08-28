"""An advert saying the thing is ABSENT was the first one thrown away.

    There is no night shift and no on-call rota.
    We do not set take-home exercises.

Both hid the role, silently, with no flag: the pattern matched and nothing
looked at what came before it. Those are employers going out of their way to
advertise the absence of the thing the reader is avoiding, so they were
exactly the roles that reader most wanted, and they were the ones deleted.

The fix is deliberately not "ignore negated mentions". Getting this wrong the
other way lets a real take-home through unseen, and the whole point of a hard
dealbreaker is that it does not. A negated mention is downgraded to a soft
flag: shown, labelled, and the reader decides. Shown-and-labelled is the safe
answer to "we are not certain what this sentence means".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config, Dealbreaker  # noqa: E402
from jobradar.models import Job                  # noqa: E402
from jobradar.screen import screen               # noqa: E402

BODY = ("We are hiring an engineer to run our payments platform and own it "
        "end to end. The team is small, ships often, and looks after what it "
        "builds. We offer a competitive package and a generous holiday "
        "allowance. Our stack is Python and Postgres running on AWS. ")


def _screen(sentence, name="on-call", pattern=r"on.?call"):
    cfg = Config()
    cfg.dealbreakers = [Dealbreaker(name=name, pattern=pattern, hard=True)]
    job = Job(company="Acme", title="Engineer", url="https://x/1",
              platform="ashby", location="London, UK",
              description=BODY + sentence)
    keep, _ = screen(job, cfg)
    return keep, job.flags


ABSENT = (
    "There is no on-call rota for this role.",
    "This role has no on-call.",
    "You will never be on-call.",
    "The team works without an on-call rota.",
)

PRESENT = (
    "You will join the on-call rota, shared across six engineers.",
    "On-call is one week in six and is paid.",
    "The role includes on-call responsibilities.",
)


def test_an_advert_promising_the_thing_is_absent_is_not_hidden():
    for s in ABSENT:
        keep, flags = _screen(s)
        assert keep is True, f"hidden by its own promise: {s!r}"
        assert any("does NOT have" in f for f in flags), (s, flags)


def test_a_real_requirement_is_still_hidden():
    """The direction that must not break. A hard dealbreaker that stops
    hiding things is not a dealbreaker."""
    for s in PRESENT:
        keep, _ = _screen(s)
        assert keep is False, f"a real on-call requirement came through: {s!r}"


def test_it_works_for_a_take_home_too():
    keep, _ = _screen("We do not set take-home exercises.",
                      name="take-home", pattern=r"take.?home")
    assert keep is True
    keep, _ = _screen("There is a take home exercise in the process.",
                      name="take-home", pattern=r"take.?home")
    assert keep is False


def test_a_negation_in_an_earlier_sentence_does_not_carry_over():
    """"We have no dress code. You will join the on-call rota." is two facts,
    and reading the first as qualifying the second would let a real
    requirement through."""
    keep, _ = _screen("We have no dress code. You will join the on-call rota.")
    assert keep is False


def test_one_negated_mention_does_not_excuse_a_real_one():
    keep, _ = _screen("There is no on-call at weekends. Weekday on-call is "
                      "one week in four.")
    assert keep is False


def test_the_title_still_wins_outright():
    cfg = Config()
    cfg.dealbreakers = [Dealbreaker(name="on-call", pattern=r"on.?call",
                                    hard=True)]
    job = Job(company="Acme", title="On-Call Support Engineer",
              url="https://x/1", platform="ashby", location="London",
              description=BODY + "There is no on-call rota.")
    assert screen(job, cfg)[0] is False
