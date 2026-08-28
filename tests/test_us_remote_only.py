"""`work_modes` had two ways out, and both of them needed a country set.

`test_work_modes_filter.py` covers the gate properly, but every one of its
configs leaves `locations.countries` empty. That is the one shape of config
where the gate is reachable at all: `match` only enters its location branch
`if allowed`, and both escapes below live inside that branch. So a remote-only
reader in the United States, which is the whole point of the setting, was
running code no test had ever entered.

Found by importing the published seed as a senior product designer in Austin,
Texas, with `countries: [US]` and `work_modes: [remote]`. 38 of the 472 stored
roles were hybrid or office, which is 8% of a list whose single job is to
contain no such thing. 33 of them printed "arrangement not stated; you asked
for remote" and "4 days a week in the office" on the same row, one flag
directly under the other.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config                              # noqa: E402
from jobradar.models import Job                                 # noqa: E402
from jobradar.screen import enrich, match, stated_work_mode     # noqa: E402

BODY = ("We are hiring a product designer to own our reporting surface, "
        "the design system and the research that feeds both. ")


def _cfg():
    c = Config()
    c.titles_include = ["product designer"]
    c.countries = ["US"]
    c.work_modes = ["remote"]
    return c


def _job(location, description):
    return Job(company="Acme", title="Senior Product Designer",
               url="https://acme.example/jobs/1", platform="ashby",
               location=location, description=description, remote=True)


def test_a_bare_remote_location_does_not_skip_the_work_modes_gate():
    """The commonest location string there is was the way past the filter.

    `match` took "Remote" on its own to mean the employer had named no
    country, checked the body for a country restriction, and then returned
    True from inside that branch -- above the `work_modes` block at the end of
    the function, which therefore never ran.

    Jump App's "Sr. UX Designer (US)" is the real one. Its location is the
    single word "Remote" and its advert says "Remote or hybrid (must reside in
    the USA)", so `work_mode` calls it hybrid. It was stored for a remote-only
    reader with `work_mode: hybrid` on the row and "remote, body says US" in
    its own reasons, which is the row disagreeing with itself.
    """
    job = _job("Remote", BODY + "Remote or hybrid, and you must reside in "
                                "the USA. We would love you to fly in.")
    assert stated_work_mode(job)[0] == "hybrid"
    keep, why = match(job, _cfg())
    assert keep is False, "a hybrid role reached a remote-only reader"
    assert "hybrid" in why


def test_a_stated_office_day_count_is_not_an_unstated_arrangement():
    """`match` and `enrich` were answering the same question differently.

    The gate called bare `work_mode()`, which reads the title, the location
    and the advert for the words remote and hybrid. A posting located "San
    Francisco, CA" whose advert says "4 days a week in the office" uses
    neither word, so the gate called it unstated, kept it and flagged it as
    such. `enrich` ran on the very next line, read the day count out of that
    same advert, and stored the role as hybrid.
    """
    job = _job("San Francisco, CA",
               BODY + "You will be in our SoMa office 4 days a week.")
    keep, why = match(job, _cfg())
    assert keep is False, "a 4-day-a-week office role passed as 'not stated'"
    assert "hybrid" in why
    assert not any("not stated" in f for f in job.flags), \
        "told the reader the arrangement was unstated and then read it out"


def test_five_days_a_week_in_the_office_is_an_office_role():
    job = _job("Pittsburgh, PA",
               BODY + "This role is in our Pittsburgh office 5 days a week.")
    keep, why = match(job, _cfg())
    assert keep is False and "office" in why


def test_what_survives_the_gate_still_agrees_with_it_after_enrich():
    """The invariant the two escapes both broke.

    `enrich` is what the dashboard, the facets and the stored row are built
    from, and it runs after `match`. Whatever mode it lands on has to be one
    the reader asked for, or genuinely unstated. Anything else is the tool
    filing a role under a label that contradicts the filter it just passed.
    """
    cases = [
        ("Remote", BODY + "Remote or hybrid, you must reside in the USA."),
        ("San Francisco, CA", BODY + "In the office 4 days a week."),
        ("New York, NY", BODY + "Hybrid: three days per week in the office."),
        ("Pittsburgh, PA", BODY + "On-site 5 days a week."),
        ("Remote - US", BODY + "Fully remote, anywhere in the US."),
        ("Austin, TX", BODY + "We have not decided the arrangement yet."),
    ]
    cfg = _cfg()
    kept = 0
    for location, body in cases:
        job = _job(location, body)
        keep, _why = match(job, cfg)
        if not keep:
            continue
        kept += 1
        enrich(job)
        assert job.work_mode in ("remote", "unstated"), (
            f"{location!r} was kept for a remote-only reader and then "
            f"stored as {job.work_mode!r}")
    # Without this the loop body is skippable end to end: a gate that tightened
    # to drop all six would report a pass having asserted nothing, which is the
    # failure that renders identically to a success. Two of the six survive
    # today -- "Remote - US" and the undecided Austin role -- and the other
    # four are dropped as hybrid or office, which is the gate working.
    assert kept == 2, f"{kept} of {len(cases)} survived the gate, not 2"


def test_an_unstated_arrangement_is_still_kept_and_flagged():
    """The half of the market that says nothing, which must not regress.

    Both fixes above tighten a filter, and the failure this codebase keeps
    finding is the tightening that goes one step too far: reading "we cannot
    tell" as "not remote" hides more real remote roles than it removes office
    ones, and the reader never learns which.
    """
    job = _job("Austin, TX", BODY + "A great team and a great product.")
    keep, _why = match(job, _cfg())
    assert keep is True
    assert any("not stated" in f for f in job.flags)
