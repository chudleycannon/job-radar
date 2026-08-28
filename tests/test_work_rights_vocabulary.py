"""A bar on working here, stated the way it is stated outside the UK and US.

`work_rights` knew refusals ("we cannot sponsor a visa") and missed
requirements. Outside the UK and the US the requirement IS the normal
wording, and none of it mentions sponsorship at all:

    Applicants must be Singapore Citizens or Permanent Residents.
    Candidates must already hold a valid Employment Pass.
    You must hold a valid UAE residency visa and an NOC.

All three read as "not stated", so a reader who needs a visa was shown roles
they cannot apply for with nothing to tell them apart from the ones they can.
The gate itself was always properly config-driven; it was the vocabulary that
was UK and US only.

The danger in fixing it is over-firing. "citizens or permanent residents"
appears in equal-opportunity boilerplate on adverts from employers who
sponsor perfectly happily, and matching that would hide exactly the roles
this reader most needs. So the patterns are tied to a requirement verb, and
the boilerplate cases are tested as hard as the bars.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Job          # noqa: E402
from jobradar.screen import work_rights  # noqa: E402

BARS = (
    "Applicants must be Singapore Citizens or Permanent Residents.",
    "Candidates must already hold a valid Employment Pass.",
    "You must hold a valid UAE residency visa and an NOC.",
    "Applicants must be US citizens.",
    "You will need to hold the right to work in Singapore.",
)

OFFERS = (
    "Visa sponsorship and relocation are provided.",
    "We provide visa sponsorship for this role.",
    "Relocation and visa support offered.",
    "We are happy to sponsor the right candidate.",
)

# Adverts from employers who sponsor. If any of these reads as a bar, the
# reader who needs sponsorship stops seeing the roles that would take them.
BOILERPLATE = (
    "We are an equal opportunity employer and welcome applications from "
    "citizens and permanent residents of all countries.",
    "All qualified applicants will receive consideration without regard to "
    "citizenship status.",
    "Our team includes citizens of over thirty countries.",
    "We are hiring a platform engineer to work on our payments systems.",
)


def _rights(text):
    return work_rights(Job(company="Acme", title="Engineer",
                           url="https://x/1", platform="ashby",
                           description=text))


def test_a_requirement_to_be_a_citizen_or_resident_is_a_bar():
    wrong = [t for t in BARS if _rights(t) != "no sponsorship"]
    assert not wrong, f"read as something other than a bar: {wrong}"


def test_equal_opportunity_boilerplate_is_not_a_bar():
    """The over-fire direction, and the worse of the two.

    A miss shows a reader one role they cannot take. A false positive hides
    every role from an employer who would have sponsored them.
    """
    wrong = [t for t in BOILERPLATE if _rights(t) == "no sponsorship"]
    assert not wrong, f"boilerplate read as a bar: {wrong}"


def test_an_offer_split_across_a_clause_is_still_an_offer():
    """"Visa sponsorship and relocation are provided" read as not stated,
    because the pattern wanted the noun next to the verb. A miss in the safe
    direction, but it is an employer saying yes to the reader who needs the
    answer most."""
    assert _rights("Visa sponsorship and relocation are provided.") \
        == "sponsorship offered"


def test_every_offer_still_reads_as_one():
    wrong = [t for t in OFFERS if _rights(t) != "sponsorship offered"]
    assert not wrong, f"an offer stopped reading as one: {wrong}"


def test_an_advert_saying_nothing_still_says_nothing():
    assert _rights("We are hiring an engineer to build our platform.") == ""
