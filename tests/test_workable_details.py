"""apply.workable.com only sends the advert when it is asked to.

The bundled list held 2,094 widget URLs with no `details=true` on any of them,
and the response without it carries no `description` key at all. So the
slowest phase of the whole scan, fifty minutes against one host, was storing
roles that `rank` refuses to score (it requires 200 characters), that no
dealbreaker could be checked against, and that had no salary, because
`parse_text` was handed an empty string. On 2026-08-27 that was 219 of 219
stored Workable roles, while `workable_search`, reading the same company's
adverts through a different host, had 96 of 97.

The two fixtures here are the same real board read both ways, trimmed. They
exist so the difference is a thing a test can see rather than a thing a
comment claims.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters                      # noqa: E402
from jobradar.adapters import platforms            # noqa: E402
from jobradar.sources import Source                # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
WIDGET = "https://apply.workable.com/api/v1/widget/accounts/1000heads"


def _src(url=WIDGET):
    return adapters.prepare(Source.from_dict(
        {"company": "1000Heads", "url": url, "platform": "workable"}))


def test_prepare_asks_the_widget_for_details():
    assert _src().url.endswith("?details=true")


def test_a_trailing_slash_does_not_produce_a_double_question_mark():
    assert _src(WIDGET + "/").url == WIDGET + "?details=true"


def test_it_is_not_added_twice():
    once = _src().url
    assert adapters.prepare(Source.from_dict(
        {"company": "x", "url": once})).url == once


def test_other_workable_hosts_are_left_alone():
    for u in ("https://jobs.workable.com/api/v1/jobs?query=engineer",
              "https://jobs.workable.com/api/v1/companies/acme",
              "https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
        assert _src(u).url == u


def test_every_bundled_workable_board_is_asked_for_details():
    """The pipeline, not the helper.

    A normalisation that works in isolation and is not reached by `load` is
    the failure this whole file is about, so this reads the shipped list
    through the same call the scan uses.
    """
    from jobradar import sources as S
    raw = json.loads((Path(__file__).resolve().parent.parent /
                      "sources" / "sources.json").read_text(encoding="utf-8"))
    items = raw.get("sources", raw) if isinstance(raw, dict) else raw
    widgets = [d for d in items
               if "apply.workable.com/api" in str(d.get("url", ""))
               and "/widget/accounts/" in str(d.get("url", ""))]
    assert widgets, "the bundled list no longer has any Workable widget boards"
    prepared = [adapters.prepare(Source.from_dict(dict(d))) for d in widgets]
    missing = [s.url for s in prepared if "details=true" not in s.url]
    assert not missing, f"{len(missing)} bundled Workable boards ask for no advert"


def test_the_plain_response_really_does_carry_no_description():
    payload = json.loads((FIX / "workable_widget_plain.json")
                         .read_text(encoding="utf-8"))
    jobs = list(platforms.parse_workable(payload, _src()))
    assert jobs, "fixture parsed to nothing"
    assert all(not (j.description or "").strip() for j in jobs)


def test_the_details_response_carries_one_and_the_parser_keeps_it():
    payload = json.loads((FIX / "workable_widget_details.json")
                         .read_text(encoding="utf-8"))
    jobs = list(platforms.parse_workable(payload, _src()))
    assert jobs, "fixture parsed to nothing"
    assert all(len((j.description or "").strip()) >= 200 for j in jobs), \
        "a description under 200 characters is one `rank` will refuse to score"
    assert all(j.title and j.url for j in jobs)
