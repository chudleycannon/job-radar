"""A slug we invented, answering 404, was reported as a broken board.

`discover mollie.com` finds the real answer (an Ashby board with 47 roles)
and then printed SEVEN blocks of

    [could not read] ... found, but could not be read: HTTP 404
                     not added; try again later

Every one of those was a token this tool made up by guessing spellings of the
company name. "Try again later" will never work: there is no board at that
address and there never was. It buries the one real result under noise.

The scope matters more than the fix. `count_jobs` is shared with
`validate --prune`, and teaching THAT to read 404 as dead is exactly how a
maintenance job opened a pull request deleting 17,171 of 17,810 sources. So
this applies only to candidates the guesser invented, and only to the two
statuses that actually mean "nothing here".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.discover import _is_absent  # noqa: E402


def test_404_and_410_mean_there_is_nothing_at_this_address():
    for err in ("HTTP 404", "HTTP 410", "HTTP 404 Not Found", "not found"):
        assert _is_absent(err) is True, err


def test_a_shut_door_is_not_an_absent_one():
    """A 403 exists and is closed, a 429 exists and is busy, a timeout is no
    answer at all. Reading any of them as absence is how a board that is
    merely blocked gets deleted."""
    for err in ("HTTP 403 Forbidden", "HTTP 429 rate limited",
                "HTTP 500", "HTTP 503", "timed out", "connection refused",
                "TLS handshake failed (TLSV1_ALERT_PROTOCOL_VERSION)"):
        assert _is_absent(err) is False, err


def test_no_error_at_all_is_not_absence():
    for err in ("", None):
        assert _is_absent(err) is False


def test_the_guess_path_is_the_only_one_that_drops_anything():
    """The guard that keeps this away from `validate --prune`.

    A board the company actually published a link to must still be reported
    when it 404s, because that is a real board that has gone, and somebody
    should look at it.
    """
    import inspect
    from jobradar import discover
    src = inspect.getsource(discover.discover)
    assert "api in guessed and _is_absent(err)" in src, \
        "the absence check is no longer scoped to guessed candidates"
    assert "guessed = {api for" in src, "nothing records which were guessed"
