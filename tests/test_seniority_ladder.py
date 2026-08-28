"""The ladder was engineering-shaped, and it inverted a designer's list.

Two faults compounding.

The level-2 vocabulary was a list of engineering job nouns, so every title
outside engineering scored 0: "Product Designer", "Data Scientist" and "Nurse
Practitioner" had no level at all. A target list of 0s and 3s then made every
senior posting look like a leap.

And `staff` and `principal` sat ABOVE `manager`, which reads the individual
and the management tracks as a single ladder. They are parallel. Staff is the
rung above Senior on the IC side, roughly level with Manager; Principal is
the one above that.

Together: a remote-only product designer searching for "Product Designer" and
"Senior Product Designer" saw "Staff Product Designer", the correct next role
for them, scored "2 levels above your targets" and docked 25 points, landing
it below a plain junior posting. Their ranked list came out upside down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.screen import seniority  # noqa: E402


def test_a_bare_professional_title_outside_engineering_has_a_level():
    for title in ("Product Designer", "Data Scientist", "UX Researcher",
                  "Nurse Practitioner", "Management Consultant",
                  "Technical Writer", "Solutions Architect"):
        assert seniority(title) >= 2, f"{title} still reads as no level"


def test_staff_does_not_outrank_manager():
    """One is the senior IC rung and the other is the first management rung.
    Neither is above the other, and treating them as a single ladder is what
    inverted the designer's list."""
    assert seniority("Staff Engineer") == seniority("Engineering Manager")
    assert seniority("Staff Product Designer") == seniority("Design Manager")


def test_the_ic_track_still_goes_up_in_the_right_order():
    ladder = ["Product Designer", "Senior Product Designer",
              "Staff Product Designer", "Principal Product Designer"]
    levels = [seniority(t) for t in ladder]
    assert levels == sorted(levels), levels
    assert len(set(levels)) == len(levels), f"two rungs collapsed: {levels}"


def test_a_staff_role_is_no_longer_two_levels_above_a_senior_one():
    """The specific arithmetic that cost 25 points. `score` docks a role
    `2` or more levels above the reader's targets."""
    assert seniority("Staff Product Designer") - \
        seniority("Senior Product Designer") == 1


def test_junior_still_wins_outright_over_the_noun():
    """"Junior Data Engineer" must not score as mid-level because "engineer"
    outranks "junior"."""
    assert seniority("Junior Data Engineer") == 1
    assert seniority("Graduate Product Designer") == 1


def test_the_management_track_is_unchanged_above_manager():
    assert seniority("Head of Design") > seniority("Design Manager")
    assert seniority("Director of Design") > seniority("Head of Design")
    assert seniority("VP Engineering") == seniority("Director of Engineering")


def test_an_unrecognisable_title_still_has_no_level():
    """0 means "we cannot tell", and `score` skips the comparison entirely
    when it sees one. Inventing a level would start docking points on a
    guess."""
    assert seniority("Scrum Master") == 0
    assert seniority("") == 0
