"""What a new user is told, in order, on the one run that decides everything.

Three gaps, all of them about a person sitting in front of a terminal.

Setup fetched the seed and then announced an hour-long scan without ever
mentioning that the reader already had a working dashboard. They had 254
roles and no idea they could use them, so the honest reading of that screen
was "wait an hour".

The scan prints its own total before it starts and its own timing before each
pass, which is the ETA. That part was already right.

And the handover at the end read exactly the same whether the scan took three
seconds or eighty minutes. Somebody who walked away came back to a wall of
instructions with nothing saying the thing they were waiting for had
finished.
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import setup_wizard as w  # noqa: E402


def _run(stored=0, elapsed=0.0):
    """Drive `first_scan` with the scan itself stubbed out."""
    import io
    import contextlib
    from jobradar import cli

    out = io.StringIO()
    ticks = iter([0.0, elapsed * 60])
    with contextlib.redirect_stdout(out), \
            mock.patch.object(cli, "cmd_scan", lambda a: 0), \
            mock.patch.object(w, "_roles_already_stored", lambda p: stored), \
            mock.patch.object(w, "_sources_it_will_read", lambda p: 17_814), \
            mock.patch("time.monotonic", lambda: next(ticks)):
        w.first_scan(Path(tempfile.mkdtemp()) / "c.yaml")
    return out.getvalue()


def test_a_reader_holding_seeded_roles_is_told_they_can_use_them_now():
    """It used to say "open a second terminal and run `job-radar serve`",
    which is the tool asking a person to do the tool's job. The dashboard is
    started for them by `seed_first`, so this only has to say it is there."""
    text = _run(stored=254)
    assert "254 roles from the seed" in text
    assert "dashboard that is open now" in text
    assert "second terminal" not in text


def test_it_says_what_the_scan_adds_on_top_of_the_seed():
    """Otherwise the honest reading of an hour-long scan over a working
    dashboard is "why"."""
    text = _run(stored=254)
    assert "refreshes those and adds" in text


def test_a_reader_with_no_seed_is_not_told_about_one():
    text = _run(stored=0)
    assert "from the seed" not in text


def test_the_scan_ends_with_a_sentence_saying_it_ended():
    text = _run(stored=254, elapsed=80)
    assert "Scan finished" in text


def test_the_ending_says_how_long_it_took():
    # Minutes stay minutes up to an hour and a half, because "about 80
    # minutes" is a number somebody can plan around and "1.3 hours" is one
    # they have to convert.
    assert "about 80 minutes" in _run(stored=1, elapsed=80)
    assert "about 5 minutes" in _run(stored=1, elapsed=5)
    assert "about 1 minute" in _run(stored=1, elapsed=1)
    assert "well under a minute" in _run(stored=1, elapsed=0.2)
    assert "2.0 hours" in _run(stored=1, elapsed=120)


def test_the_ending_says_what_changed():
    """A number the reader can compare with the one they were given before
    the scan started."""
    import io
    import contextlib
    from jobradar import cli
    counts = iter([254, 1_402])
    out = io.StringIO()
    ticks = iter([0.0, 60.0])
    with contextlib.redirect_stdout(out), \
            mock.patch.object(cli, "cmd_scan", lambda a: 0), \
            mock.patch.object(w, "_roles_already_stored", lambda p: next(counts)), \
            mock.patch.object(w, "_sources_it_will_read", lambda p: 17_814), \
            mock.patch("time.monotonic", lambda: next(ticks)):
        w.first_scan(Path(tempfile.mkdtemp()) / "c.yaml")
    assert "254 roles to 1,402" in out.getvalue(), out.getvalue()


def test_counting_the_board_never_raises():
    """It decorates a message. A first run must not end in a traceback
    because a count could not be read."""
    assert w._roles_already_stored(Path("/nowhere/at/all/c.yaml")) == 0
    d = Path(tempfile.mkdtemp())
    (d / "data").mkdir()
    (d / "data" / "job-radar.db").write_text("not a database", encoding="utf-8")
    assert w._roles_already_stored(d / "c.yaml") == 0


def test_the_seed_opens_the_dashboard_rather_than_asking_for_a_terminal():
    """The whole point. A seed leaves several hundred usable roles on disk
    before the scan starts, and the scan's own dashboard does not open for
    another five minutes."""
    import inspect
    src = inspect.getsource(w.seed_first)
    assert "_open_board(config_path)" in src


def test_opening_the_board_never_takes_the_setup_down():
    """A dashboard that could not be started is not a reason to lose a setup
    that has just stored several hundred roles."""
    import io
    import contextlib
    from jobradar import serve as serve_mod
    out = io.StringIO()
    with contextlib.redirect_stdout(out), \
            mock.patch.object(serve_mod, "open_in_background",
                              side_effect=OSError("no sockets today")):
        assert w._open_board(Path(tempfile.mkdtemp()) / "c.yaml") == ""
    assert "job-radar serve" in out.getvalue()


def test_a_dashboard_already_running_is_not_started_twice():
    """Two servers on one database contend, and the loser prints a SQLite
    traceback at somebody who did nothing wrong."""
    import io
    import contextlib
    from jobradar import serve as serve_mod
    out = io.StringIO()
    with contextlib.redirect_stdout(out), \
            mock.patch.object(serve_mod, "open_in_background",
                              return_value=None):
        assert w._open_board(Path(tempfile.mkdtemp()) / "c.yaml") == ""
