"""Flags nobody documented, and one that could not be given at all.

Twenty-five arguments across nine subcommands carried no help text. `serve
--help` described none of `--db`, `--host`, `--port` or `--no-browser`, and
never named the default port the README quotes, so the one page that says what
`serve` does had to be read against a number written down somewhere else.
`list --help` said nothing about `--status` and, unlike `applied`, did not
name the values it accepts.

Undocumented is not cosmetic here. `--host` on this command publishes a
database of somebody's application history and private notes to their network,
and the flag said nothing at all.

The last one is a different failure: `setup --defaults` is the only path that
works without a terminal, so it is the whole of the story for scripts, CI and
anyone setting up over ssh, and it wrote `countries: [UK]` and
`currency: GBP` with no flag able to say otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import cli, setup_wizard
from jobradar.cli import build_parser


def _walk(parser, name="job-radar"):
    """Every argument on every subcommand, as (command, flags, action)."""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for sub, sp in sorted(a.choices.items()):
                yield from _walk(sp, f"{name} {sub}")
            continue
        if isinstance(a, argparse._HelpAction):
            continue
        yield name, "/".join(a.option_strings) or a.dest, a


def test_every_argument_on_every_subcommand_has_help():
    """The check that stops this coming back one flag at a time. It walks the
    real parser rather than a list somebody maintains, so a subcommand added
    next year is covered on the day it is added."""
    missing = [f"{cmd} {flags}" for cmd, flags, a in _walk(build_parser())
               if not (a.help or "").strip()]
    assert not missing, "arguments with no help: " + ", ".join(missing)


def test_the_help_says_what_the_defaults_are_where_they_matter():
    """A default nobody can see is a default nobody can override on purpose.
    The README quotes 8765 and `serve --help` did not."""
    p = build_parser()
    helps = {(cmd, flags): a.help for cmd, flags, a in _walk(p)}
    assert "8765" in helps[("job-radar serve", "--port")]
    assert "127.0.0.1" in helps[("job-radar serve", "--host")]
    for cmd in ("list", "applied", "rescreen", "enrich", "rank", "serve",
                "generate"):
        assert "data/job-radar.db" in helps[(f"job-radar {cmd}", "--db")], cmd


def test_list_names_the_statuses_the_way_applied_does():
    """Two commands take the same value and only one of them said which
    values exist, so `list --status open` came back as an error naming the set
    that `--help` could have."""
    from jobradar import store
    helps = {(cmd, flags): a.help for cmd, flags, a in _walk(build_parser())}
    text = helps[("job-radar list", "--status")]
    for s in store.STATUSES:
        assert s in text, f"{s} is a valid --status and is not in the help"


def test_a_read_only_command_says_it_will_not_create_the_database():
    """The behaviour changed, and a flag whose help does not mention it leaves
    the first person to mistype a path guessing."""
    helps = {(cmd, flags): a.help for cmd, flags, a in _walk(build_parser())}
    assert "will not create" in helps[("job-radar list", "--db")]


# ------------------------------------------------------- setup --defaults

def _answers(argv, wizard_takes_them=True):
    """Run `setup` against a stand-in wizard and give back the answers dict.

    A stand-in rather than the real one because the real one writes a config
    and, with `--scan`, starts a scan. What is under test is the flag reaching
    the answers, which is exactly the hop that did not exist.
    """
    seen = {}

    def fake(path, non_interactive=False, cv=None, titles=None, scan=False,
             countries=None, currency=None, seed=True):
        a = dict(setup_wizard.DEFAULTS)
        a["cv_path"] = cv
        a["titles_include"] = [titles]
        if countries:
            a["countries"] = countries
        if currency:
            a["salary_currency"] = currency
        a["seed"] = seed
        seen.update(a)
        return 0

    def fake_without(path, non_interactive=False, cv=None, titles=None,
                     scan=False):
        seen.update(setup_wizard.DEFAULTS)
        return 0

    real = setup_wizard.run
    setup_wizard.run = fake if wizard_takes_them else fake_without
    try:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
    finally:
        setup_wizard.run = real
    return code, seen, buf.getvalue()


def test_setup_defaults_can_be_told_the_country_and_the_currency():
    """It wrote UK and GBP for everybody, and `--defaults` is the only path a
    script has. Somebody in Austin got a config filtering to the wrong
    continent and pricing in the wrong money."""
    code, seen, _ = _answers(
        ["-c", "/dev/null", "setup", "--defaults", "--cv", "cv.docx",
         "--titles", "engineering manager", "--countries", "US,IE",
         "--currency", "usd"])
    assert code == 0
    assert seen["countries"] == ["US", "IE"], seen["countries"]
    assert seen["salary_currency"] == "USD", seen["salary_currency"]
    # And every other key the wizard collects still arrives. Building the
    # `--defaults` answers by hand rather than from DEFAULTS is how a setting
    # ends up written by the interactive path and missing from the scripted
    # one, which is the half nobody watches.
    for k in setup_wizard.DEFAULTS:
        assert k in seen, f"{k} never reached the answers"


def test_the_country_list_takes_spaces_as_well_as_commas():
    """`--countries "US IE"` and `--countries US,IE` are the same request, and
    reading the first as one country code called "US IE" produces a config
    whose fault nobody can find."""
    _, seen, _ = _answers(
        ["-c", "/dev/null", "setup", "--defaults", "--cv", "cv.docx",
         "--titles", "em", "--countries", "US IE"])
    assert seen["countries"] == ["US", "IE"], seen["countries"]


def test_a_flag_the_wizard_cannot_apply_is_refused_rather_than_ignored():
    """The two halves of these flags live in different modules, and a
    namespace that does not match has shipped four times in this CLI already.
    Dropping `--countries US` quietly would write `countries: [UK]` to
    somebody who typed the opposite, which is worse than not running."""
    code, _, out = _answers(
        ["-c", "/dev/null", "setup", "--defaults", "--cv", "cv.docx",
         "--titles", "em", "--countries", "US"],
        wizard_takes_them=False)
    assert code == 1, out
    assert "--countries" in out, out


def test_setup_fetches_the_seed_unless_told_not_to():
    """A new user's first scan takes over an hour and the seed lands in about
    a minute, and setup never mentioned it. So it is fetched by default, and
    `--no-seed` is the way out."""
    _, on, _ = _answers(["-c", "/dev/null", "setup", "--defaults",
                         "--cv", "cv.docx", "--titles", "engineering manager"])
    assert on["seed"] is True

    _, off, _ = _answers(["-c", "/dev/null", "setup", "--defaults", "--no-seed",
                          "--cv", "cv.docx", "--titles", "engineering manager"])
    assert off["seed"] is False


def test_a_wizard_that_cannot_fetch_a_seed_says_so_rather_than_guessing():
    """The same signature check the country and currency flags get. A build
    whose wizard predates the flag must not silently skip the fetch, nor
    silently make one nobody asked for."""
    code, _, out = _answers(
        ["-c", "/dev/null", "setup", "--defaults", "--cv", "cv.docx",
         "--titles", "engineering manager"], wizard_takes_them=False)
    assert code == 1
    assert "--seed" in out or "--countries" in out, out
