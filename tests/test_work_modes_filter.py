"""A remote-only reader had no way to say so.

`locations.remote_ok` is a boolean answering a three-way question. True shows
remote AND everything else; False hides remote; nothing said "hide anything
that is not remote". A designer who will only take fully remote work got 30
of their 40 matches as office and hybrid roles in cities they had already
said they would not move to, and the dashboard's own mode facet is
client-side, so it could not narrow the scan either.

`locations.work_modes` is an allow-list. Empty, the default, keeps
everything, which is the old behaviour exactly.

The important part is what happens to a posting that does not state an
arrangement. Half of them do not, and reading "we cannot tell" as "not
remote" would hide more real remote roles than it removed office ones. So an
unstated role is kept and flagged, and the reader can see which is which.
That is the same rule as the seed's `unplaced` shard and the `multi` source
tag, and it is written down in CLAUDE.md because this codebase keeps getting
it wrong in the other direction.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config, ConfigError, _work_modes  # noqa: E402
from jobradar.models import Job                               # noqa: E402
from jobradar.screen import match                             # noqa: E402

BODY = "We are hiring an engineer to build and run our payments platform."


def _cfg(modes):
    c = Config()
    c.titles_include = ["engineer"]
    c.work_modes = list(modes)
    return c


def _job(location="", description=BODY, remote=None):
    return Job(company="Acme", title="Engineer", url="https://x/1",
               platform="ashby", location=location, description=description,
               remote=remote)


def test_remote_only_drops_an_office_role():
    keep, why = match(_job("New York",
                           BODY + " This role is on-site in our NYC office."),
                      _cfg(["remote"]))
    assert keep is False and "office" in why


def test_remote_only_drops_a_hybrid_role():
    keep, why = match(_job("New York",
                           BODY + " Hybrid: three days a week in the office."),
                      _cfg(["remote"]))
    assert keep is False and "hybrid" in why


def test_remote_only_keeps_a_remote_role():
    keep, _ = match(_job("Remote - US", remote=True), _cfg(["remote"]))
    assert keep is True


def test_a_posting_that_does_not_say_is_kept_and_flagged():
    """The half of the market that states nothing.

    Dropping these would hide more real remote roles than it removed office
    ones, and the reader would never know which.
    """
    job = _job("New York")
    keep, _ = match(job, _cfg(["remote"]))
    assert keep is True
    assert any("not stated" in f for f in job.flags), job.flags


def test_an_empty_list_changes_nothing():
    for desc in (BODY, BODY + " On-site in our NYC office.",
                 BODY + " Hybrid: three days in the office."):
        assert match(_job("New York", desc), _cfg([]))[0] is True


def test_two_modes_can_be_allowed_at_once():
    cfg = _cfg(["remote", "hybrid"])
    assert match(_job("New York", BODY + " Hybrid: three days in."), cfg)[0]
    assert not match(_job("New York", BODY + " On-site in our NYC office."),
                     cfg)[0]


def test_on_site_is_accepted_as_a_spelling_of_office():
    assert _work_modes(["on-site"], "x") == ["office"]
    assert _work_modes(["Remote", "REMOTE"], "x") == ["remote"]


def test_an_arrangement_that_is_not_one_is_refused_not_ignored():
    for bad in (["flexible"], ["wfh"], ["anywhere"]):
        try:
            _work_modes(bad, "locations.work_modes")
        except ConfigError as exc:
            assert "not a working arrangement" in str(exc)
        else:
            raise AssertionError(f"{bad} was accepted and would filter nothing")


def test_unstated_is_refused_with_a_reason_rather_than_silently_doing_nothing():
    """Listing it would change nothing, and a setting that changes nothing is
    a setting the reader believes is working."""
    try:
        _work_modes(["unstated"], "locations.work_modes")
    except ConfigError as exc:
        assert "always kept" in str(exc)
    else:
        raise AssertionError("'unstated' was accepted and does nothing")
