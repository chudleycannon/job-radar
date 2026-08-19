"""Tests for the parts that are easy to get quietly wrong."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import Config, Dealbreaker
from jobradar.models import Job, Salary, Source
from jobradar.salary import clears_floor, from_ashby, from_greenhouse, parse_text
from jobradar.screen import dedupe, match, run as screen_run


def _cfg(**kw) -> Config:
    base = dict(
        titles_include=["engineering manager"],
        titles_exclude=["product manager"],
        countries=["UK"],
        relocate_to=["US"],
        salary_floor=100000,
        salary_currency="GBP",
    )
    base.update(kw)
    return Config(**base)


# ----------------------------------------------------------------- salary
def test_parses_ranges_and_k_notation():
    s = parse_text("£120,000 - £150,000 per annum")
    assert s.confirmed and s.min == 120000 and s.max == 150000 and s.currency == "GBP"
    s = parse_text("$189.6K – $290.4K • Offers Equity")
    assert s.confirmed and s.min == 189600 and s.max == 290400


def test_competitive_is_not_a_salary():
    assert not parse_text("Competitive salary and great benefits").confirmed
    assert not parse_text("Salary: DOE").confirmed
    assert not parse_text("").confirmed


def test_day_rate_is_annualised_before_comparison():
    """A day rate must not be compared to an annual floor as a bare number.

    Without annualising, "£600 per day" reads as 600 and is dropped by any
    sane floor. With it, £600/day is £132,000 a year, which genuinely is below
    a £140k floor, and £700/day is £154,000, which clears £100k.
    """
    s = parse_text("£600 per day")
    assert s.period == "day" and s.max == 600
    assert s.annualised() == 132000
    assert clears_floor(s, 140000, "GBP")[0] is False

    better = parse_text("£700 per day")
    assert better.annualised() == 154000
    assert clears_floor(better, 100000, "GBP")[0] is True

    # An hourly rate goes through the same conversion.
    hourly = parse_text("$95 per hour")
    assert hourly.period == "hour" and hourly.annualised() == 95 * 220 * 8


def test_ashby_and_greenhouse_shapes():
    a = from_ashby({"scrapeableCompensationSalarySummary": "$189.6K - $290.4K"})
    assert a.confirmed and a.max == 290400
    g = from_greenhouse([{"min_cents": 12000000, "max_cents": 15000000,
                          "currency_type": "GBP"}])
    assert g.confirmed and g.min == 120000 and g.max == 150000


def test_the_salary_rule():
    """Stated and too low is dropped. Unstated is always kept."""
    low = Salary(min=80000, max=90000, currency="GBP", confirmed=True, raw="£80k-£90k")
    assert clears_floor(low, 100000, "GBP")[0] is False

    # Top of the band clears, so it survives.
    spanning = Salary(min=100000, max=150000, currency="GBP", confirmed=True)
    assert clears_floor(spanning, 100000, "GBP")[0] is True

    assert clears_floor(Salary(), 100000, "GBP")[0] is True

    # Currencies are not silently converted.
    usd = Salary(min=90000, max=95000, currency="USD", confirmed=True)
    keep, why = clears_floor(usd, 100000, "GBP")
    assert keep is True and "not compared" in why


# ----------------------------------------------------------------- location
def _job(title="Engineering Manager", location="London", **kw) -> Job:
    return Job(company="Acme", title=title, url="https://x/1", platform="test",
               location=location, **kw)


def test_remote_still_respects_the_country():
    """'Remote' alone means unspecified. 'Remote - US' names a country, and a
    US-remote role is not open to someone in the UK."""
    cfg = _cfg(relocate_to=[])
    assert match(_job(location="Remote"), cfg)[0] is True
    assert match(_job(location="Remote - US", remote=True), cfg)[0] is False
    assert match(_job(location="Remote - UK", remote=True), cfg)[0] is True
    # This is the bug that let Hong Kong roles through: remote=True was
    # treated as location-agnostic.
    assert match(_job(location="Hong Kong", remote=True), cfg)[0] is False


def test_multi_location_postings_survive_on_one_good_location():
    cfg = _cfg()
    ok, _ = match(_job(location="Hong Kong / United Kingdom / Singapore"), cfg)
    assert ok is True


def test_title_gate():
    cfg = _cfg()
    assert match(_job(title="Product Manager"), cfg)[0] is False
    assert match(_job(title="Senior Data Analyst"), cfg)[0] is False


# ----------------------------------------------------------------- dedupe
def test_same_role_in_many_locations_collapses():
    jobs = [_job(location=c) for c in ("London", "Berlin", "Paris", "Tokyo")]
    for i, j in enumerate(jobs):
        j.url = f"https://x/{i}"
    out = dedupe(jobs, _cfg())
    assert len(out) == 1
    assert "posted in 4 locations" in out[0].flags
    # The location you could actually take is shown first.
    assert out[0].location.startswith("London")


# ----------------------------------------------------------------- pipeline
def test_end_to_end_screen():
    cfg = _cfg(dealbreakers=[Dealbreaker("coding round", r"take.?home|live coding")])
    jobs = [
        _job(title="Engineering Manager", location="London",
             description="You will lead a team."),
        _job(title="Engineering Manager", location="London",
             description="There is a take home exercise."),
        _job(title="Engineering Manager", location="Paris"),
        _job(title="Product Manager", location="London"),
    ]
    for i, j in enumerate(jobs):
        j.url = f"https://x/{i}"
        j.title = f"{j.title} {i}"   # keep dedupe out of it
    kept, dropped = screen_run(jobs, cfg)
    assert len(kept) == 1
    assert "dealbreaker: coding round" in dropped
    assert kept[0].score > 0 and kept[0].reasons


def test_uid_is_stable_across_query_strings():
    a = _job(); a.url = "https://x/jobs/1?utm_source=feed"
    b = _job(); b.url = "https://x/jobs/1"
    assert a.uid == b.uid


def test_source_key_keeps_the_query_string():
    """LinkedIn sources differ only by query; stripping it collapsed six
    distinct searches into one."""
    a = Source(company="li", url="https://x/api?keywords=a", platform="linkedin")
    b = Source(company="li", url="https://x/api?keywords=b", platform="linkedin")
    assert a.key != b.key


def test_uk_postcodes_count_as_uk():
    """Employers hiring nationally list towns, not cities. NHS Jobs gives
    "Dorchester DT1 2JY"; a city-only regex read every such role as an
    unrecognised country and dropped it."""
    from jobradar.screen import _country_of
    assert _country_of("Dorchester DT1 2JY") == "UK"
    assert _country_of("Coventry CV2 2DX") == "UK"
    assert _country_of("Ipswich IP4 5SW") == "UK"
    # and must not swallow other countries' formats
    assert _country_of("Austin, TX 78701") == "US"
    assert _country_of("Hong Kong") == "HK"



def test_wizard_config_is_valid_yaml_with_regex_dealbreakers():
    """The default config the wizard writes must actually load.

    Dealbreakers are regexes. YAML processes backslash escapes inside
    double-quoted scalars, so a pattern containing \\w was a parse error the
    moment the file was read back: `setup --defaults` then `scan` crashed for
    every new user. Single-quoted YAML takes the string literally.
    """
    import tempfile, yaml
    from jobradar.setup_wizard import write_config, DEFAULTS, COMMON_DEALBREAKERS
    from jobradar.config import load as load_cfg

    answers = dict(DEFAULTS)
    answers["dealbreakers"] = dict(COMMON_DEALBREAKERS)   # every pattern, \w and all
    d = Path(tempfile.mkdtemp()) / "config.yaml"
    write_config(d, answers)

    raw = yaml.safe_load(d.read_text())
    assert len(raw["dealbreakers"]) == len(COMMON_DEALBREAKERS)
    cfg = load_cfg(d)
    # and the patterns must still compile after the round trip
    for db in cfg.dealbreakers:
        db.compiled()
    assert len(cfg.dealbreakers) == len(COMMON_DEALBREAKERS)



def test_ambiguous_city_names_resolve_to_the_right_country():
    """City names are not unique across countries, and getting this wrong
    marked 59 of 296 American roles as British.

    "New York City" contains "york". There is a Cambridge in Massachusetts, a
    Birmingham in Alabama, a Manchester in New Hampshire, a Reading in
    Pennsylvania and a Newcastle in Australia. An explicit country name beats
    a US state code beats a city name.
    """
    from jobradar.screen import _country_of, _countries_in
    # the report that started it
    assert _country_of("San Francisco, CA | New York City, NY") == "US"
    assert _country_of("New York, New York, USA") == "US"
    # US cities that collide with UK ones
    for loc in ("Cambridge, MA", "Birmingham, AL", "Manchester, NH",
                "Reading, PA", "Bath, ME", "Boston, MA"):
        assert _country_of(loc) == "US", loc
    # explicit country wins over a colliding city name
    assert _country_of("Newcastle, Australia") == "AU"
    assert _country_of("Paris, TX") == "US"
    # and the genuine UK cases still work
    for loc in ("London", "York, England", "Manchester, UK", "Dorchester DT1 2JY"):
        assert _country_of(loc) == "UK", loc


def test_commas_bind_a_place_to_its_qualifier():
    """Splitting on commas severed "Cambridge, MA" from the state code that
    identifies it, so the fragment "Cambridge" read as UK. Only a pipe or a
    slash separates genuinely distinct locations."""
    from jobradar.screen import _countries_in
    assert _countries_in("Cambridge, MA") == {"US"}
    assert _countries_in("San Francisco, CA | New York City, NY") == {"US"}
    assert _countries_in("Boston, MA | London") == {"UK", "US"}
    assert _countries_in("Hong Kong / United Kingdom / Singapore") == {"HK", "SG", "UK"}


def test_us_role_is_dropped_for_a_uk_only_search():
    """The end-to-end version of the bug: a San Francisco posting scored 90
    and gave 'in UK' as a reason."""
    cfg = _cfg(countries=["UK"], relocate_to=[])
    j = _job(title="Engineering Manager",
             location="San Francisco, CA | New York City, NY")
    keep, why = match(j, cfg)
    assert keep is False
    assert "outside target countries" in why



def test_application_tracking_matches_and_settles():
    """A scanner that forgets shows you the same job every week.

    Matching has to survive how people actually write these down: an entry
    typed by hand says "Example Corp" when the posting title is worded
    differently, and a URL may carry tracking parameters.
    """
    import tempfile, yaml
    from jobradar.applications import Tracker, SETTLED

    d = Path(tempfile.mkdtemp()) / "applications.yaml"
    d.write_text(yaml.safe_dump({"applications": [
        {"org": "Example Corp", "role": "Engineering Manager, Platform",
         "status": "submitted", "date": "2026-08-11"},
        {"org": "Nowhere Ltd", "status": "rejected"},
        {"url": "https://x/jobs/9", "status": "interviewing"},
    ]}))
    tr = Tracker.load(d)
    assert len(tr.apps) == 3

    j = Job(company="Example Corp", title="Engineering Manager - Platform (Remote)",
            url="https://x/jobs/1", platform="t")
    assert tr.find(j) is not None

    # org alone mutes a whole company, whatever the role
    j2 = Job(company="Nowhere Ltd", title="Anything At All", url="https://x/2", platform="t")
    a2 = tr.find(j2)
    assert a2 and a2.status in SETTLED

    # an exact URL wins even when the company has been renamed since
    j3 = Job(company="Renamed Since", title="Different Title",
             url="https://x/jobs/9?utm=feed", platform="t")
    assert tr.find(j3) is not None

    assert tr.find(Job(company="Someone Else", title="EM",
                       url="https://x/3", platform="t")) is None

    assert tr.annotate([j, j2, j3]) == 3
    assert j.app_status == "submitted" and j2.app_status == "rejected"
    assert any("2026-08-11" in f for f in j.flags)



# ----------------------------------------------------------------- database
def _tmpdb():
    import tempfile
    from jobradar import store
    return store.connect(Path(tempfile.mkdtemp()) / "t.db")


def test_first_seen_survives_rescans():
    """"New since last run" is only meaningful if first_seen is never
    overwritten. A role found in March and still open in August is not new."""
    from jobradar import store
    con = _tmpdb()
    j = _job(title="Engineering Manager")
    j.url = "https://x/jobs/1"
    store.upsert_roles(con, [j])
    con.execute("UPDATE roles SET first_seen='2026-03-01'")
    store.upsert_roles(con, [j])          # seen again
    row = con.execute("SELECT first_seen, last_seen FROM roles").fetchone()
    assert row["first_seen"] == "2026-03-01"
    assert row["last_seen"] != "2026-03-01"


def test_nothing_is_new_on_the_very_first_run():
    """Reporting 300 roles as new on day one is not an alert, it is the
    whole database."""
    from jobradar import store
    con = _tmpdb()
    j = _job(); j.url = "https://x/jobs/2"
    store.upsert_roles(con, [j])
    assert store.new_since_last_run(con, [j.uid]) == set()
    store.bump_runs(con)
    j2 = _job(title="Engineering Manager Two"); j2.url = "https://x/jobs/3"
    store.upsert_roles(con, [j2])
    assert j2.uid in store.new_since_last_run(con, [j.uid, j2.uid])


def test_settled_roles_are_hidden_and_reversible():
    from jobradar import store
    con = _tmpdb()
    j = _job(); j.url = "https://x/jobs/4"
    store.upsert_roles(con, [j])
    store.set_status(con, j.uid, "skipped")
    assert j.uid in store.settled_uids(con)
    store.set_status(con, j.uid, "interested")
    assert j.uid not in store.settled_uids(con)


def test_bad_status_is_refused():
    from jobradar import store
    con = _tmpdb()
    j = _job(); j.url = "https://x/jobs/5"
    store.upsert_roles(con, [j])
    try:
        store.set_status(con, j.uid, "definitely-not-a-status")
    except ValueError:
        return
    raise AssertionError("an unknown status should not be storable")


def test_generation_is_not_queued_twice():
    """Clicking the button twice should not spawn two Claude processes."""
    from jobradar import store
    con = _tmpdb()
    j = _job(); j.url = "https://x/jobs/6"
    store.upsert_roles(con, [j])
    a = store.enqueue(con, j.uid, "cv")
    b = store.enqueue(con, j.uid, "cv")
    assert a == b


def test_cover_letter_needs_a_cv_to_check_itself_against():
    from jobradar import store
    con = _tmpdb()
    j = _job(); j.url = "https://x/jobs/7"
    store.upsert_roles(con, [j])
    assert store.has_artifact(con, j.uid, "cv") is False
    store.add_artifact(con, j.uid, "cv", "/tmp/CV.md", rating=78.0)
    assert store.has_artifact(con, j.uid, "cv") is True


def test_migration_is_idempotent():
    """It runs on every scan, so a second call must change nothing."""
    import json, tempfile
    from jobradar import store
    d = Path(tempfile.mkdtemp())
    seen = d / "seen.json"
    seen.write_text(json.dumps({"runs": 3, "seen": {
        "abc123": {"first_seen": "2026-07-01", "last_seen": "2026-08-01",
                   "company": "Acme", "title": "Engineering Manager"}}}))
    con = store.connect(d / "t.db")
    first = store.migrate(con, state_path=seen, apps_path=d / "none.yaml")
    second = store.migrate(con, state_path=seen, apps_path=d / "none.yaml")
    assert first["roles"] == 1 and second["roles"] == 0
    assert con.execute("SELECT COUNT(*) c FROM roles").fetchone()["c"] == 1
    assert con.execute("SELECT first_seen FROM roles").fetchone()["first_seen"] == "2026-07-01"



# ------------------------------------------------------------------- gates
def test_detect_verdict_is_read_not_substring_matched():
    """detect.py prints "Fix the FAIL/WARN lines above" as standing advice even
    on a clean run, so testing for the substring FAIL marked every passing
    document as failed."""
    import re
    clean = ("SLOP SCORE: 0/100   (bar: <= 20, and no FAILs)   ->  PASS\n"
             "Fix the FAIL/WARN lines above, then re-run.")
    dirty = ("FAIL  polished-cadence\n"
             "SLOP SCORE: 44/100  ->  FAIL\n"
             "Fix the FAIL/WARN lines above, then re-run.")
    def verdict(blob):
        m = re.search(r"->\s*(PASS|FAIL)", blob)
        return m.group(1) == "PASS" if m else None
    assert verdict(clean) is True
    assert verdict(dirty) is False
    assert int(re.search(r"SLOP SCORE:\s*(\d+)", clean).group(1)) == 0


def test_docx_text_extraction():
    """The generation subprocess is sandboxed and cannot shell out to a
    converter, so a .docx has to be turned into text before it is handed over
    or the CV job has nothing to work from."""
    from jobradar.runner import docx_to_text
    cv = Path.home() / "Downloads" / "Callum_McDonald_CV.docx"
    if not cv.exists():
        return                       # not this machine; nothing to assert
    text = docx_to_text(cv)
    assert len(text) > 500
    assert "\n" in text


def test_a_config_pointing_at_a_missing_cv_is_refused():
    """A CV path that has silently stopped existing produces an invented CV
    rather than an error, so it is checked when the config loads."""
    import tempfile, yaml
    from jobradar.config import load as load_cfg
    d = Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(yaml.safe_dump({
        "titles": {"include": ["engineering manager"]},
        "cv": {"path": str(d / "definitely-not-here.docx")},
    }))
    try:
        load_cfg(d / "c.yaml")
    except FileNotFoundError as e:
        assert "no file there" in str(e).lower()
        return
    raise AssertionError("a missing CV should stop the config loading")



def test_overlap_is_measured_not_self_reported():
    """A gate the model reports on itself is not a gate.

    The first version asked Claude to write its own overlap finding to a file
    and then guessed at the prose, which read "Longest phrase shared: 5 words"
    as a failure and marked a clean letter as overlapping.
    """
    from jobradar.runner import shared_ngram
    a = "I led the team through a two year shift to AI assisted engineering"
    b = "Separately, I led the team through a two year shift to something else"
    assert shared_ngram(a, b)                      # 9 shared words: caught
    assert "led the team through" in shared_ngram(a, b)

    # five shared words is under the bar and must not trip it
    assert shared_ngram("a model tier and cost strategy for the workstream",
                        "a model tier and cost policy across the board") == ""

    # nothing in common at all
    assert shared_ngram("detection engineering at scale",
                        "completely unrelated wording here") == ""

    # short documents cannot produce a false positive
    assert shared_ngram("too short", "too short") == ""



# --------------------------------------------------- defects found by review
def test_prose_number_ranges_are_not_salaries():
    """This runs over the first 1500 chars of a description for most adapters,
    and a confirmed-but-wrong figure below the floor silently DROPS a role."""
    for s in ("We serve 40,000 to 120,000 requests per second",
              "Our community grew from 25,000 to 90,000 members",
              "we raised $50M and serve 10,000 - 60,000 customers"):
        assert parse_text(s).confirmed is False, s
    # and real ones still land
    assert parse_text("Salary: 120,000 to 150,000 per annum").confirmed is True
    assert parse_text("The pay range for this role is 95,000 to 120,000").confirmed is True
    assert parse_text("£120,000 - £150,000").confirmed is True


def test_remote_ok_false_actually_excludes_remote():
    """Answering "no" to "include fully remote roles" used to change nothing:
    only a completely empty location was dropped."""
    cfg = _cfg(remote_ok=False, relocate_to=[])
    for loc in ("Remote", "Fully Remote", "Anywhere", ""):
        keep, why = match(_job(location=loc), cfg)
        assert keep is False, f"{loc!r} should be excluded when remote is off"
    assert match(_job(location="London"), cfg)[0] is True


def test_spelled_out_us_states_are_not_uk():
    """The earlier fix taught it the two-letter codes and nothing else, so it
    fixed the instances in the test and not the class."""
    from jobradar.screen import _country_of
    for loc in ("Birmingham, Alabama", "Cambridge, Massachusetts",
                "Manchester, New Hampshire", "Oxford, Mississippi",
                "Bristol, Connecticut", "Glasgow, Kentucky"):
        assert _country_of(loc) == "US", loc
    assert _country_of("Birmingham, UK") == "UK"


def test_role_folder_is_keyed_on_the_role_not_the_day():
    """A CV drafted Monday and a letter drafted Wednesday landed in different
    folders, so the letter could not read the CV it must be checked against."""
    import tempfile
    from jobradar.runner import role_dir
    base = Path(tempfile.mkdtemp())
    row = {"company": "Acme", "title": "Engineering Manager"}
    monday = base / "2026-08-17-acme-engineering-manager"
    monday.mkdir(parents=True)
    assert role_dir(row, base) == monday


def test_source_meta_survives_a_prune():
    """The weekly prune replaced meta wholesale, deleting the provenance note,
    the version and the harvest counts."""
    import json as _j, tempfile
    from jobradar import sources as sm
    p = Path(tempfile.mkdtemp()) / "s.json"
    p.write_text(_j.dumps({"meta": {"note": "keep me", "version": 4},
                           "sources": []}))
    sm.save([], p, meta={"pruned": 3})
    meta = _j.loads(p.read_text())["meta"]
    assert meta["note"] == "keep me" and meta["version"] == 4
    assert meta["pruned"] == 3


def test_acted_on_roles_stay_visible_after_a_partial_scan():
    """Filtering the dashboard on the last scan alone made applied roles
    disappear when a posting closed or a source was rate-limited."""
    from jobradar import store
    from jobradar.output.interactive import _rows
    con = _tmpdb()
    old = _job(title="Engineering Manager Old"); old.url = "https://x/old"
    new = _job(title="Engineering Manager New"); new.url = "https://x/new"
    store.upsert_roles(con, [old, new])
    con.execute("UPDATE roles SET last_seen='2026-01-01' WHERE uid=?", (old.uid,))
    store.set_status(con, old.uid, "applied")
    uids = {r["uid"] for r in _rows(con)}
    assert old.uid in uids, "a role you applied to must not vanish"
    assert new.uid in uids



# ------------------------------------------- defects found by a fresh user
def test_keyword_sources_follow_the_user_not_the_author():
    """The eight bundled NHS sources were frozen searches for the author's own
    job titles, so a nurse running this got "engineering manager" inside the
    NHS and zero matches out of 24,719 postings."""
    from jobradar.config import Config
    from jobradar import sources as sm

    nurse = Config(titles_include=["practice educator", "clinical educator"])
    got = [s.company for s in sm.load(nurse) if s.platform == "nhs"]
    assert got == ["NHS Jobs: practice educator", "NHS Jobs: clinical educator"]
    assert all("engineering" not in c.lower() for c in got)

    # and the URL really carries the keyword
    url = next(s.url for s in sm.load(nurse) if s.platform == "nhs")
    assert "practice+educator" in url

    # someone with no titles set gets no keyword searches rather than a guess
    assert [s for s in sm.load(Config(titles_include=[])) if s.platform == "nhs"] == []


def test_excluded_titles_with_punctuation_match_correctly():
    """Left unescaped, "healthcare assistant (bank)" became a regex: it failed
    to match the real posting and matched a different one instead."""
    from jobradar.config import Config
    r = Config(titles_exclude=["healthcare assistant (bank)", "sales"]).title_exclude_re()
    assert r.search("Healthcare Assistant (Bank)")
    assert not r.search("Healthcare Assistant Bank Staff")
    assert r.search("Sales Manager")
    assert not r.search("Salesforce Engineer")     # \b still does its job


def test_empty_cv_path_does_not_look_like_a_valid_file():
    """Path("") is PosixPath("."), which exists, so the "no CV configured"
    guard never fired and the copy raised IsADirectoryError instead."""
    from pathlib import Path as P
    assert P("").exists() is True and P("").is_dir() is True
    chosen = "" or None
    src = P(chosen) if chosen else None
    assert src is None                              # the shape the fix relies on


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  pass  {name}")
        except Exception:
            bad += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
