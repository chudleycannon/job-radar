"""A section written as a list, and a number written as a word, both escaped as
raw Python.

Every other bad value in `config.py` produces a sentence naming the setting and
saying what to write instead. These two did not:

  * `titles:` or `locations:` written as a YAML list -- which is the obvious
    shape, because what goes under `titles:` IS a list of titles -- reached
    `.get` and came back as "'list' object has no attribute 'get'". It names
    no section, no file and no line, and it reads like a bug in the tool
    rather than a typo in the config. `_check_keys` had already looked at the
    section and skipped it, because it tested `isinstance(block, dict)` and a
    list is not one, so the one check that could have caught it stayed quiet.
  * `fetch.concurrency: loads` raised "invalid literal for int() with base 10:
    'loads'" from inside `load`, with no mention of `fetch`, `concurrency`, or
    the file it was reading.

Both are `ConfigError` now, which is what the CLI catches and prints as a
message rather than a traceback.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import config  # noqa: E402

TITLES = "titles:\n  include: [engineering manager]\n"
NO_SOURCES = "sources:\n  use_bundled: false\n"


def _load(text: str):
    p = Path(tempfile.mkdtemp()) / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return config.load(p)


def _refused(text: str) -> str:
    try:
        _load(text)
    except config.ConfigError as e:
        return str(e)
    except Exception as e:
        raise AssertionError(
            f"raised {type(e).__name__} rather than ConfigError: {e}")
    raise AssertionError("accepted a config that cannot work")


def test_titles_written_as_a_list_says_titles():
    why = _refused("titles:\n  - engineering manager\n  - head of engineering\n")
    assert "titles" in why, why
    assert "list" in why, why
    # The keys that were meant to go under it, so the fix is in the message.
    assert "include" in why, why


def test_locations_written_as_a_list_says_locations():
    why = _refused(TITLES + "locations:\n  - UK\n  - IE\n")
    assert "locations" in why and "countries" in why, why


def test_every_section_of_the_file_names_itself_when_it_is_the_wrong_shape():
    """Not just the two that were reported. They all took the same route into
    `.get`, and a section nobody has typed wrong yet is a section that has not
    been typed wrong yet."""
    for section in sorted(config.KNOWN_KEYS):
        text = TITLES + f"{section}:\n  - something\n"
        if section == "titles":
            text = f"{section}:\n  - something\n"
        why = _refused(text)
        assert section in why, f"{section}: {why}"


def test_a_number_written_as_a_word_names_the_setting():
    for key, value in (("concurrency", "loads"), ("timeout", "soon"),
                       ("retries", "a few")):
        why = _refused(TITLES + NO_SOURCES + f"fetch:\n  {key}: {value}\n")
        assert f"fetch.{key}" in why, why
        assert value in why, why


def test_a_number_written_as_true_or_false_is_refused_rather_than_counted():
    """`retries: yes` is a YAML boolean and `int(True)` is 1. That is a
    number, so nothing would have complained, and the setting would have meant
    something nobody asked for."""
    why = _refused(TITLES + NO_SOURCES + "fetch:\n  retries: yes\n")
    assert "fetch.retries" in why, why


def test_the_numbers_still_load_when_they_are_numbers():
    cfg = _load(TITLES + NO_SOURCES +
                "fetch:\n  concurrency: 8\n  timeout: 30\n  retries: 1\n")
    assert (cfg.concurrency, cfg.timeout, cfg.retries) == (8, 30, 1)


def test_a_section_left_empty_is_still_fine():
    """An empty block parses as None and always meant "use the defaults"."""
    cfg = _load(TITLES + NO_SOURCES + "salary:\noutput:\nlocations:\n")
    assert cfg.salary_floor is None and cfg.formats == ["html", "json"]
