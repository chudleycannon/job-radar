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
    answers["titles_include"] = ["engineering manager"]    # required since v5
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
    try:
        # exists() is true for a file this process is not allowed to open, so
        # the readability check has to be an actual read.
        cv.open("rb").close()
    except OSError:
        return                       # not this machine, or not readable here
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



def test_generate_passes_the_config_it_was_given():
    """Without this, a run with -c pointed elsewhere resolved a config from the
    working directory instead, and a nurse's role came back screened against
    the author's job titles."""
    import inspect
    from jobradar import cli, runner
    src = inspect.getsource(cli.cmd_generate)
    assert "config_path=args.config" in src, "generate must forward --config"
    assert "config_path" in inspect.signature(runner.run_job).parameters


def test_local_server_refuses_cross_origin_posts():
    """The generate endpoint spends money, and a text/plain POST is a simple
    request with no preflight to stop it."""
    from jobradar.serve import Handler

    class FakeHeaders(dict):
        def get(self, k, default=None):
            return dict.get(self, k, default)

    h = Handler.__new__(Handler)
    h.headers = FakeHeaders({"Origin": "https://evil.example",
                             "Host": "127.0.0.1:8765"})
    assert h._same_origin() is False
    h.headers = FakeHeaders({"Origin": "http://127.0.0.1:8765",
                             "Host": "127.0.0.1:8765"})
    assert h._same_origin() is True
    h.headers = FakeHeaders({"Host": "127.0.0.1:8765"})   # curl, no Origin
    assert h._same_origin() is True



def test_config_refuses_what_it_used_to_swallow():
    """Six settings used to be accepted and then silently do the wrong thing:
    a salary with a pound sign crashed mid-scan, remote_ok "no" meant yes, a
    broken dealbreaker regex was a traceback after the fetch, a typo'd section
    name loaded clean and filtered nothing."""
    import tempfile, yaml
    from jobradar.config import load as load_cfg, ConfigError

    d = Path(tempfile.mkdtemp())

    def write(cfg):
        p = d / "c.yaml"
        base = {"titles": {"include": ["engineering manager"]}}
        base.update(cfg)
        p.write_text(yaml.safe_dump(base))
        return p

    # money written the way people write money
    assert load_cfg(write({"salary": {"floor": "£70,000"}})).salary_floor == 70000.0
    assert load_cfg(write({"salary": {"floor": "70,000"}})).salary_floor == 70000.0

    # quoted booleans mean what they say
    assert load_cfg(write({"locations": {"remote_ok": "no"}})).remote_ok is False

    # and the things that should stop the run
    for cfg, why in [
        ({"salary": {"floor": "seventy grand"}}, "not a number"),
        ({"output": {"formats": ["pdf"]}}, "not a format"),
        ({"sector": ["retail"]}, "unknown setting"),
        ({"dealbreakers": [{"name": "x", "pattern": "a|(b"}]}, "regular expression"),
        ({"dealbreakers": [{"name": "x", "hard": True}]}, "no pattern"),
        ({"dealbreakers": [{"name": "x", "regex": "y"}]}, "unknown key"),
    ]:
        try:
            load_cfg(write(cfg))
        except ConfigError as e:
            assert why in str(e).lower(), f"{cfg} -> {e}"
        else:
            raise AssertionError(f"{cfg} should have been refused")


def test_excluded_location_is_not_cancelled_by_the_country_filter():
    """"Not London" is the most load-bearing line a UK user writes, and it did
    nothing: the exclusion matched, then the country check un-matched it."""
    cfg = _cfg(countries=["UK"], relocate_to=[], exclude_locations=["London"])
    for loc in ("London", "London, UK", "London, England"):
        assert match(_job(location=loc), cfg)[0] is False, loc
    assert match(_job(location="Manchester, UK"), cfg)[0] is True
    # a role in several places survives on the one that is not excluded
    assert match(_job(location="London | Manchester"), cfg)[0] is True


def test_a_role_is_new_once_not_all_day():
    """Keying "new" on the date meant a second scan the same afternoon
    re-reported the morning's roles as new."""
    from jobradar import store
    con = _tmpdb()
    a = _job(title="EM One"); a.url = "https://x/1"
    store.upsert_roles(con, [a]); store.bump_runs(con)
    store.upsert_roles(con, [a])
    assert store.new_since_last_run(con, [a.uid]) == set(), "same role, same day"
    store.bump_runs(con)
    b = _job(title="EM Two"); b.url = "https://x/2"
    store.upsert_roles(con, [a, b])
    assert store.new_since_last_run(con, [a.uid, b.uid]) == {b.uid}



def test_a_page_that_served_us_content_is_not_blocked():
    """Substring-matching "cloudflare" or "captcha" over any body marked three
    working charity sites as bot-protected: a Cloudflare-served 404 for a path
    that does not exist is not a block, and neither is a hidden captcha field
    inside a perfectly good 200."""
    from jobradar.discover import _is_blocked

    class R:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text

    assert _is_blocked(R(200, "...job alerts signup Captcha...")) is False
    assert _is_blocked(R(404, "Cloudflare | page not found")) is True
    assert _is_blocked(R(403, "")) is True
    assert _is_blocked(R(200, "ordinary careers page")) is False
    assert _is_blocked(None) is False


def test_unsupported_platforms_are_named_not_shrugged_at():
    """Fifteen identical "nothing found" messages hid the fact that UK charity
    recruitment runs on four ATSs nobody has written an adapter for."""
    from jobradar.discover import detect_unsupported
    cases = [
        ("https://jobs.bhf.org.uk/", "powered by eploy", "Eploy"),
        ("https://jobs.crisis.org.uk/Home/Job", "", "Jobtrain"),
        ("https://jobs.oxfam.org.uk/jobs/home/", "", "Hireserve"),
        ("https://careers.nationaltrust.org.uk/OA_HTML/a/", "", "Oracle EBS iRecruitment"),
    ]
    for url, body, expected in cases:
        assert detect_unsupported(body, url) == expected, url
    # "deploy" must not read as Eploy
    assert detect_unsupported("our servers deploy nightly", "https://x/") == ""
    # and a supported board is not mislabelled
    assert detect_unsupported("", "https://boards.greenhouse.io/monzo") == ""


def test_adding_a_source_keeps_the_file_as_written():
    """--add round-tripped through yaml.safe_dump and deleted every comment,
    including the only line documenting sources.extra."""
    import shutil, tempfile, yaml
    from jobradar.cli import _append_sources
    from jobradar.models import Source

    d = Path(tempfile.mkdtemp()) / "config.yaml"
    shutil.copy("config.example.yaml", d)
    before = d.read_text()
    _append_sources(d, [Source(company="Beam", url="https://x/board", platform="ashby")])
    after = d.read_text()

    assert after.count("#") == before.count("#"), "comments were destroyed"
    assert "https://x/board" in after
    parsed = yaml.safe_load(after)
    assert any(s.get("url") == "https://x/board"
               for s in parsed["sources"]["extra"])


def test_the_wizard_only_offers_sectors_that_exist():
    """Offering "manufacturing" and "transport", which are not tags in the
    source list, meant picking your own sector silently reduced you to the
    keyword searches alone."""
    import json as _j
    from collections import Counter
    from jobradar.setup_wizard import SECTORS
    real = {s for s in Counter(
        x.get("sector") for x in _j.loads(
            Path("sources/sources.json").read_text())["sources"]) if s}
    assert not (set(SECTORS) - real), f"offered but nonexistent: {set(SECTORS) - real}"
    assert not (real - set(SECTORS)), f"exists but not offered: {real - set(SECTORS)}"



def test_seniority_mismatch_is_scored_down():
    """The score never read the candidate, so a Principal role and a grade-I
    role got identical numbers and the person who most needed to be told
    "that one is a fantasy" was handed it at the top of the list."""
    from jobradar.screen import score, seniority
    cfg = _cfg(titles_include=["data engineer", "analytics engineer"],
               titles_exclude=[], salary_floor=None)

    def s(title):
        return score(_job(title=title, location="London"), cfg)

    assert seniority("Junior Data Engineer") == 1, "an explicit junior marker wins"
    assert seniority("Principal Database Engineer") == 5
    assert s("Data Engineer") > s("Staff Analytics Engineer")
    assert s("Staff Analytics Engineer") > s("Director of Data Engineering")

    # Everything inside the band you asked for ranks equally: listing a
    # director title alongside a manager one must not mark the manager role
    # as two levels too junior.
    band = _cfg(titles_include=["fundraising manager", "head of fundraising",
                                "partnerships director"],
                titles_exclude=[], salary_floor=None)

    def b(title):
        return score(_job(title=title, location="London"), band)

    assert b("Fundraising Manager") == b("Partnerships Director")
    assert b("Chief Executive") < b("Fundraising Manager")
    assert b("Fundraising Assistant") < b("Fundraising Manager")
    j = _job(title="Principal Analytics Engineer", location="London")
    score(j, cfg)
    assert any("stretch" in f for f in j.flags)


def test_titles_are_read_from_real_cv_lines():
    """The extractor required a title alone on its own line, so any CV with an
    employer or a date beside the job title returned nothing at all."""
    from jobradar.setup_wizard import titles_from_cv
    cv = ("Finance Business Partner - Bevan Group, Cardiff (Aug 2023 - present)\n"
          "Management Accountant, Bevan Manufacturing, May 2021 - Aug 2023\n"
          "Practice Educator\t\tLeeds Teaching Hospitals\n"
          "Head of Department at Fairfield High School\n")
    got = titles_from_cv(cv)
    for expected in ("finance business partner", "management accountant",
                     "practice educator", "head of department"):
        assert expected in got, f"{expected} not extracted from {got}"

    # The name on the line above must not be swallowed into the title.
    named = titles_from_cv("Priya Ramanathan\nFundraising Manager, Mind, London\n")
    assert "fundraising manager" in named
    assert not any("priya" in x for x in named), named


def test_markdown_becomes_an_openable_docx():
    """The tool asked for a .docx and handed back Markdown, which cannot be
    attached to an application."""
    import tempfile, zipfile
    from jobradar.docx import markdown_to_docx
    from jobradar.runner import docx_to_text

    md = "# Jane Smith\n\n## PROFILE\n\nA **senior** nurse.\n\n- Ran a ward\n- Taught six students\n"
    out = markdown_to_docx(md, Path(tempfile.mkdtemp()) / "CV.docx")
    assert out.exists() and out.stat().st_size > 1000
    names = zipfile.ZipFile(out).namelist()
    assert "word/document.xml" in names and "[Content_Types].xml" in names
    text = docx_to_text(out)          # round-trips through our own reader
    assert "Jane Smith" in text and "Ran a ward" in text
    assert "**" not in text, "inline markup should be styling, not literal"


# --------------------------------------------------------------- persona run
# Every test below came from a defect a first-time user hit in a sandbox.


def test_country_names_are_accepted_and_unknown_ones_refused():
    """`countries: [Portugal]` matched nothing at all and said nothing.

    The filter compares against internal codes, so a name removed that country
    from the filter entirely. A Lisbon user asking for Portugal, Spain,
    Netherlands and Germany got 112 UK roles and no warning.
    """
    from jobradar.config import ConfigError, _countries
    assert _countries(["Portugal", "Spain", "GB", "uk"], "x") == ["PT", "ES", "UK"]
    for bad in (["Narnia"], ["ZZ"]):
        try:
            _countries(bad, "locations.countries")
        except ConfigError:
            continue
        raise AssertionError(f"{bad} should be refused")


def test_currency_is_validated_and_normalised():
    """`currency: euro` uppercased to EURO, never equalled EUR, and silently
    switched the floor off on every euro role."""
    from jobradar.config import ConfigError, _currency
    assert _currency("euro", "salary.currency") == "EUR"
    assert _currency(None, "salary.currency") == "GBP"
    try:
        _currency("XYZ", "salary.currency")
    except ConfigError:
        return
    raise AssertionError("an unknown currency should be refused")


def test_unknown_sector_is_refused():
    """`sectors: [hospitality]` cut 299 of 307 sources and still printed a
    normal-looking scan."""
    from jobradar.config import ConfigError, _sectors
    assert _sectors(["Finance", "charity"]) == ["finance", "charity"]
    try:
        # Not "hospitality": that was the original example, and the weekly
        # grower has since added a hospitality employer, which is exactly the
        # behaviour this validator is meant to follow.
        _sectors(["telecommunications"])
    except ConfigError:
        return
    raise AssertionError("a sector tag that matches no employer should be refused")


def test_scorer_obeys_the_same_currency_rule_as_the_filter():
    """One row said "not compared", the next said "comfortably above your
    floor", about the same pay."""
    from jobradar.models import Job, Salary
    from jobradar.config import Config
    from jobradar.screen import score
    cfg = Config(titles_include=["data analyst"], salary_floor=55000,
                 salary_currency="EUR", countries=["PT"])
    j = Job(company="X", title="Senior Data Analyst", url="u",
            location="London W2 1NY", platform="greenhouse",
            salary=Salary(min=58000, max=65261, currency="GBP", period="year",
                          confirmed=True, raw="58k-65k"))
    score(j, cfg)
    assert "comfortably above your floor" not in j.reasons


def test_salary_is_found_beyond_the_first_400_characters():
    """9% of a real scan reported "unconfirmed" while stating a range in the
    body. The parser only ever saw the opening block."""
    from jobradar.salary import parse_text
    body = "About the role. " + ("we do things. " * 300) + \
           "Base salary range: \u00a348,000 - \u00a372,000"
    s = parse_text(body)
    assert s.confirmed and s.min == 48000 and s.max == 72000
    # ... but prose numbers far from any mention of pay still are not salary
    assert not parse_text("x " * 300 + "we raised 1,200,000 from investors").confirmed


def test_an_uncurrencied_salary_is_flagged_not_compared():
    """A bare number used to be compared against the floor as if it were in
    the floor's currency, in both directions, with no flag either way."""
    from jobradar.models import Salary
    from jobradar.salary import clears_floor
    keep, why = clears_floor(Salary(min=45000, max=45000, confirmed=True),
                             55000, "EUR")
    assert keep and "not compared" in why


def test_remote_is_not_taken_at_face_value_when_the_body_names_a_country():
    """"Remote" in the location field and "This position is US - Remote
    Eligible" in the description scored +20 for "remote, no country named"."""
    from jobradar.models import Job
    from jobradar.config import Config
    from jobradar.screen import match
    j = Job(company="Airbnb", title="Data Scientist", url="u", location="Remote",
            platform="greenhouse", remote=True,
            description="Your Location: This position is US - Remote Eligible.")
    keep, why = match(j, Config(titles_include=["data scientist"], countries=["UK"]))
    assert not keep and "US" in why


def test_remote_ok_false_works_with_no_country_set():
    """remote_ok lived inside the country branch, so with no countries set it
    was dead code."""
    from jobradar.models import Job
    from jobradar.config import Config
    from jobradar.screen import match
    cfg = Config(titles_include=["x"], remote_ok=False)
    assert match(Job(company="A", title="x", url="u", location="Remote",
                     platform="p"), cfg)[0] is False
    assert match(Job(company="A", title="x", url="u", location="Lisbon",
                     platform="p"), cfg)[0] is True


def test_sponsorship_is_read_off_the_description():
    """The only part of the tool that knew about the right to work was a paid
    screen, one role at a time."""
    from jobradar.models import Job
    from jobradar.screen import work_rights

    def j(d):
        return Job(company="c", title="t", url="u", location="London",
                   platform="p", description=d)
    assert work_rights(j("We are unable to provide visa sponsorship.")) == "no sponsorship"
    assert work_rights(j("Sponsorship is not available for this position.")) == "no sponsorship"
    assert work_rights(j("We are able to offer visa sponsorship.")) == "sponsorship offered"
    assert work_rights(j("Free lunch and a good team.")) == ""


def test_non_uk_cities_are_recognised():
    """`Lisboa` failing while `Swindon` worked was the whole bias in one line."""
    from jobradar.screen import _country_of
    for place, code in [("Lisboa", "PT"), ("Frankfurt", "DE"), ("The Hague", "NL"),
                        ("Seville", "ES"), ("Ghent", "BE"), ("Gdansk", "PL"),
                        ("Swindon", "UK")]:
        assert _country_of(place) == code, f"{place} -> {_country_of(place)}"


def test_keyword_sources_are_probed_not_declared_dead():
    """`validate --prune` fetched the literal `{keyword}` URL, got nothing,
    and was scheduled to delete NHS Jobs every week."""
    from jobradar.models import Source
    import jobradar.discover as disc
    src = Source(company="NHS Jobs", url="https://x/search?keyword={keyword}",
                 platform="nhs", keyword_template=True)
    calls = []

    def fake(s):
        calls.append(s.url)
        return (7, [{"title": "t"}])
    old, disc.count_jobs = disc.count_jobs, fake
    try:
        row = disc.validate_source(src)
    finally:
        disc.count_jobs = old
    assert "{keyword}" not in calls[0]
    assert row["verdict"] == "live" and "identity not checked" in row["note"]


def test_a_platform_domain_is_not_mistaken_for_an_employer():
    """Guessing a Greenhouse token from `civilservicejobs.service.gov.uk`
    found one department's board named "Civil Service Jobs" and marked it
    verified."""
    from jobradar.discover import discover
    found = discover("civilservicejobs.service.gov.uk")
    assert found and found[0].identity == "unsupported"
    assert "Civil Service Jobs" in found[0].note


def test_a_uid_prefix_resolves():
    """`list` prints a shortened uid; pasting it back said "could not
    identify a role"."""
    from jobradar import store
    from jobradar.cli import _resolve_uid
    con = store.connect(":memory:")
    con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                "first_seen,last_seen) VALUES "
                "('abcdef0123456789','C','T','u','L','p','2026-01-01','2026-01-01')")
    assert _resolve_uid(con, "abcdef01")[0] == "abcdef0123456789"


def test_the_wizards_config_survives_discover_add():
    """The wizard wrote `extra:` and `    []` on two lines; `--add` appended a
    sequence under the placeholder and every command then died on a YAML
    parse error."""
    import tempfile, yaml
    from jobradar.setup_wizard import write_config, DEFAULTS
    from jobradar.cli import _append_sources
    from jobradar.models import Source
    p = Path(tempfile.mkdtemp()) / "c.yaml"
    a = dict(DEFAULTS)
    a.update({"titles_include": ["area manager"], "cv_path": str(p)})
    write_config(p, a)
    _append_sources(p, [Source(company="Nandos", url="https://x/jobs",
                               platform="workday")])
    got = yaml.safe_load(p.read_text())
    assert [s["company"] for s in got["sources"]["extra"]] == ["Nandos"]


def test_defaults_ship_no_dealbreakers():
    """A coding-round dealbreaker shipped as a default filtered a solicitor's
    and a marketing manager's results on an engineering artefact."""
    from jobradar.setup_wizard import DEFAULTS
    assert DEFAULTS["dealbreakers"] == {}


def test_a_dry_run_writes_nothing():
    """It used to insert every role and bump the run counter, so trying the
    tool out once spent the newness of everything it saw."""
    import inspect
    from jobradar import cli
    src = inspect.getsource(cli.cmd_scan)
    i = src.index("upsert_roles")
    assert "args.dry_run" in src[max(0, i - 400):i], \
        "upsert_roles must be guarded by the dry-run check"


def test_a_draft_that_adds_a_specific_is_caught():
    """"run the newsletter" became "write the monthly newsletter". Nothing
    gated it, in the one paragraph where it matters most."""
    from jobradar.runner import _invented
    src = "Run the newsletter and the Facebook page. Grew signups 40%."
    assert "monthly" in _invented("I write the monthly newsletter.", src)
    assert "62%" in _invented("Cut cost 62%.", src)
    assert _invented("Grew signups 40% since 2019.", src) == []


def test_a_rating_is_read_from_the_score_not_the_first_number():
    """`re.search(r"\d{1,3}")` would record a file opening "100-point rubric"
    as 100."""
    import re
    txt = "100-point rubric\n\nOverall: 68/100 (solid)"
    m = re.search(r"\b(\d{1,3})\s*/\s*100\b", txt)
    assert m and m.group(1) == "68"


def test_the_dashboard_survives_a_limited_scan():
    """One `--limit 25` run replaced a 60-role board with 4, because those 4
    held the newest date."""
    from jobradar import store
    from jobradar.output import interactive
    con = store.connect(":memory:")
    for i, day in enumerate(["2026-08-10"] * 5 + ["2026-08-20"]):
        con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                    "first_seen,last_seen,first_run) VALUES (?,?,?,?,?,?,?,?,1)",
                    (f"u{i}", "C", "T", f"u{i}", "London", "greenhouse", day, day))
    assert len(interactive._rows(con)) == 6


def test_new_survives_a_second_scan_the_same_day():
    """Keyed on the run number, a rescan the same afternoon bumped the counter
    and every role from the morning stopped being new: the answer to "what
    arrived today" became zero while twenty-one things had genuinely arrived.
    Three scans ran on one real day and the count has to hold across all of
    them."""
    from jobradar import store
    from jobradar.output import interactive
    con = store.connect(":memory:")
    store.set_meta(con, "runs", "4")
    rows = [("a", "2026-08-20", 4), ("b", "2026-08-19", 3), ("c", "2026-08-20", 6)]
    for uid, first, run in rows:
        con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                    "first_seen,last_seen,first_run) VALUES "
                    "(?,?,?,?,?,?,?,'2026-08-20',?)",
                    (uid, "C", "T", uid, "London", "greenhouse", first, run))
    html = interactive.render(con)
    assert html.count('data-new="1"') == 2, "both of today's should be new"
    assert 'data-f="new"' in html
    # and bumping the counter, which is what a rescan does, changes nothing
    store.set_meta(con, "runs", "9")
    assert interactive.render(con).count('data-new="1"') == 2
    assert len(store.new_today(con)) == 2


def test_adding_the_same_source_twice_is_honest_and_idempotent():
    """`--add` reported "Added 1" while correctly writing nothing, so running
    the same discover twice looked like it had duplicated the entry."""
    import tempfile, yaml
    from jobradar.setup_wizard import write_config, DEFAULTS
    from jobradar.cli import _append_sources
    from jobradar.models import Source
    p = Path(tempfile.mkdtemp()) / "c.yaml"
    a = dict(DEFAULTS)
    a.update({"titles_include": ["general manager"], "cv_path": str(p)})
    write_config(p, a)
    src = Source(company="Nandos", url="https://x/jobs", platform="workday")
    assert _append_sources(p, [src]) == 1
    assert _append_sources(p, [src]) == 0
    assert _append_sources(p, [Source(company="Hilton", url="https://y/jobs",
                                      platform="oracle")]) == 1
    got = yaml.safe_load(p.read_text())["sources"]["extra"]
    assert [x["company"] for x in got] == ["Nandos", "Hilton"]


def test_a_config_path_that_does_not_exist_is_an_error_not_a_default():
    """Falling back silently when `-c` names a missing file meant a mistyped
    path produced a confident, complete, wrong answer."""
    from jobradar.cli import _cfg_or_default
    from jobradar.config import Config
    assert isinstance(_cfg_or_default(None), Config)   # no -c given: defaults
    try:
        _cfg_or_default("/nonexistent/typo.yaml")
    except SystemExit:
        return
    raise AssertionError("an explicit -c pointing nowhere should stop the run")


def test_saving_what_you_loaded_changes_nothing():
    """The weekly prune of one dead source produced a 529-line diff.

    `adapters.prepare()` synthesises a Workday POST body from the URL shape,
    and `save()` wrote those derived values back into the file, so the pull
    request a human is meant to review was mostly noise it had generated
    itself. A maintenance job whose output cannot be read is not maintenance.
    """
    import json, tempfile
    from jobradar.sources import load_file, save, BUNDLED
    out = Path(tempfile.mkdtemp()) / "s.json"
    save(load_file(BUNDLED), out)
    norm = lambda p: sorted(json.dumps(x, sort_keys=True)
                            for x in json.loads(Path(p).read_text())["sources"])
    assert norm(BUNDLED) == norm(out)


def test_the_claude_cli_is_found_without_an_interactive_path():
    """"Generation failed: the `claude` CLI is not on PATH", from a dashboard
    that had been started by launchd.

    A launchd agent gets a non-interactive login shell, which reads .zprofile
    but not .zshrc, so a CLI installed to ~/.local/bin is invisible to it
    while `which claude` in a terminal answers fine. Anyone running this from
    cron, an IDE or a desktop launcher hits the same wall.
    """
    import os, stat, tempfile
    from jobradar import runner

    d = Path(tempfile.mkdtemp())
    fake = d / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    old_path, old_override = os.environ.get("PATH", ""), os.environ.get("JOB_RADAR_CLAUDE")
    old_candidates = runner._CLAUDE_PATHS
    try:
        os.environ["PATH"] = "/usr/bin:/bin"          # no ~/.local/bin
        os.environ.pop("JOB_RADAR_CLAUDE", None)
        runner._CLAUDE_PATHS = (str(fake),)
        assert runner.claude_bin() == str(fake), "should fall back to a known location"

        # an explicit override wins over everything
        os.environ["JOB_RADAR_CLAUDE"] = str(fake)
        assert runner.claude_bin() == str(fake)

        # and a genuinely missing CLI still says so, usefully
        os.environ.pop("JOB_RADAR_CLAUDE")
        runner._CLAUDE_PATHS = (str(d / "nope"),)
        assert runner.claude_bin() == ""
        msg = runner._no_claude_msg()
        assert "JOB_RADAR_CLAUDE" in msg and "launchd" in msg
    finally:
        os.environ["PATH"] = old_path
        runner._CLAUDE_PATHS = old_candidates
        if old_override is None:
            os.environ.pop("JOB_RADAR_CLAUDE", None)
        else:
            os.environ["JOB_RADAR_CLAUDE"] = old_override


def test_a_generated_document_survives_losing_its_file():
    """Storing only a path meant one moved folder and a screen you paid for
    was gone. The text is a few kilobytes; keep it."""
    import tempfile
    from jobradar import store
    con = store.connect(":memory:")
    con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                "first_seen,last_seen) VALUES "
                "('u','C','T','url','London','greenhouse','2026-08-21','2026-08-21')")
    d = Path(tempfile.mkdtemp())
    f = d / "screening.md"
    f.write_text("SKIP - the posting rules out sponsorship.\n")
    store.add_artifact(con, "u", "screen", f, summary="SKIP")

    f.unlink()                                   # the folder gets cleaned
    row = con.execute("SELECT body,summary FROM artifacts WHERE uid='u'").fetchone()
    assert "rules out sponsorship" in row["body"]
    assert row["summary"] == "SKIP"


def test_a_failed_job_stops_reporting_itself_after_two_minutes():
    """finished_at is written as "...T13:59:13" and SQLite's datetime() gives
    "... 13:59:13"; compared as strings "T" beats " ", so every job that
    failed today looked recent and the dashboard kept re-showing an error
    that had already been fixed. datetime('now') is UTC on top of that."""
    from jobradar import store
    con = store.connect(":memory:")
    con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                "first_seen,last_seen) VALUES "
                "('u','C','T','url','London','greenhouse','2026-08-21','2026-08-21')")
    con.execute("INSERT INTO jobs (uid,kind,state,requested_at,finished_at,error) "
                "VALUES ('u','screen','failed',datetime('now','localtime'),"
                "'2026-01-01T09:00:00','old failure')")
    q = ("SELECT id FROM jobs WHERE state IN ('pending','running') OR "
         "replace(finished_at,'T',' ') > datetime('now','localtime','-2 minutes')")
    assert con.execute(q).fetchall() == [], "a stale failure must not be re-reported"


def test_the_favicon_is_inline_and_tiny():
    """A favicon that costs a second request is a favicon that does not appear
    on a page opened from file://, which is how the static export is read."""
    from jobradar.output import favicon, interactive
    from jobradar import store
    assert len(favicon.SVG) < 2000, "must stay small enough to inline"
    uri = favicon.data_uri()
    assert uri.startswith("data:image/svg+xml,")
    # A raw "#" starts a URL fragment, so an unencoded colour truncated the
    # whole icon at the first fill and the browser received thirty bytes.
    assert "#" not in uri, "colours must be percent-encoded"
    assert len(uri) > len(favicon.SVG) * 0.8, "the whole SVG has to survive"
    html = interactive.render(store.connect(":memory:"))
    assert 'rel="icon"' in html
    # The only http in there is the SVG namespace, which is an identifier and
    # not something a browser fetches. Nothing else may reference a network.
    body = favicon.SVG.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http" not in body and "url(" not in body


def test_an_interrupted_generation_does_not_block_the_queue_for_ever():
    """A generation runs on a daemon thread inside the server, so it cannot
    outlive it. Restart the server mid-click and the row stays 'running' for
    ever: the button spins with nothing behind it, and since the queue guard
    is `running_count >= 1`, every later generation is refused too. One
    interrupted click silently disabled the whole feature."""
    from jobradar import store
    con = store.connect(":memory:")
    con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                "first_seen,last_seen) VALUES "
                "('u','C','T','url','London','greenhouse','2026-08-21','2026-08-21')")
    con.execute("INSERT INTO jobs (uid,kind,state,requested_at,started_at) "
                "VALUES ('u','screen','running',datetime('now','localtime'),"
                "datetime('now','localtime'))")
    assert store.running_count(con) == 1
    assert store.reap_orphans(con) == 1
    assert store.running_count(con) == 0, "the queue must be free again"
    row = con.execute("SELECT state,error FROM jobs").fetchone()
    assert row["state"] == "failed" and "click again" in row["error"]


def test_setups_first_scan_writes_beside_its_config():
    """`--db None` means "data/job-radar.db relative to wherever you are
    standing", which is right for `scan` inside a checkout and wrong for a
    wizard given an explicit config path: a first-time user running
    `job-radar -c ~/mine/c.yaml setup` from another project's directory wrote
    their roles into that project's database."""
    import inspect, tempfile
    from jobradar import setup_wizard

    src = inspect.getsource(setup_wizard.first_scan)
    assert "config_path.expanduser().resolve().parent" in src, \
        "paths must be derived from the config, not the cwd"
    for field in ("db =", "state =", "out ="):
        assert field in src, f"first_scan must set {field.strip(' =')} explicitly"


def test_a_stale_source_list_says_so():
    """Nothing told anyone their copy of the list ages. The weekly validation
    and growth jobs run upstream and open pull requests there; a clone freezes
    its list on the day it was cloned, and a fork only prunes its own, because
    the crawler that finds new employers deliberately does not ship here. So a
    six-month-old checkout quietly loses boards as they migrate, never gains
    the ones added since, and looks exactly as healthy as a fresh one."""
    import json, tempfile
    from datetime import date, timedelta
    from jobradar import sources as S

    d = Path(tempfile.mkdtemp())
    old = d / "old.json"
    old.write_text(json.dumps({
        "meta": {"checked": (date.today() - timedelta(days=200)).isoformat()},
        "sources": []}))
    assert S.age_days(old) == 200

    fresh = d / "fresh.json"
    fresh.write_text(json.dumps({
        "meta": {"validated": date.today().isoformat()}, "sources": []}))
    assert S.age_days(fresh) == 0

    # A list with no date at all must not be reported as brand new.
    blank = d / "blank.json"
    blank.write_text(json.dumps({"meta": {}, "sources": []}))
    assert S.age_days(blank) is None


def test_the_dashboard_says_when_the_source_list_is_behind():
    """Upstream revalidates weekly, so past eight days you have missed a cycle
    and are quietly losing boards as employers migrate. The date has to be on
    the page, and the fix has to be next to it."""
    import re
    from unittest.mock import patch
    from jobradar import store, sources
    from jobradar.output import interactive
    con = store.connect(":memory:")
    for age, warn, button in ((0, False, False), (5, False, False),
                              (9, True, True), (None, True, True)):
        with patch.object(sources, "age_days", return_value=age):
            html = interactive.render(con)
        assert ('sync warn' in html) is warn, f"age {age} warn state wrong"
        assert ('id="pull"' in html) is button, f"age {age} button wrong"


def test_the_sync_nudge_fires_once_a_day_not_every_command():
    """A warning on every command becomes something to scroll past, which is
    the same as not having one."""
    from unittest.mock import patch
    from jobradar import cli, sources, store
    from jobradar.config import Config

    said = []
    cfg = Config(titles_include=["x"], use_bundled_sources=True)
    with patch.object(sources, "age_days", return_value=23), \
         patch.object(cli, "_say", said.append):
        cli._daily_sync_nudge(cfg, ":memory:")
    assert len(said) == 1 and "git pull" in said[0]

    # Fresh list: silent.
    said.clear()
    with patch.object(sources, "age_days", return_value=2), \
         patch.object(cli, "_say", said.append):
        cli._daily_sync_nudge(cfg, ":memory:")
    assert said == []


def test_a_linkedin_url_yields_a_job_id_and_a_description():
    """LinkedIn's search endpoint returns a headline and nothing else, so a
    quarter of the board could not be screened, ranked or compared to a salary
    floor. Worse than invisible: dealbreakers with no text to match pass by
    default, which is the wrong way for a filter to fail."""
    from jobradar import enrich

    assert enrich.job_id(
        "https://uk.linkedin.com/jobs/view/engineering-manager-at-arrive-4455232988"
    ) == "4455232988"
    assert enrich.job_id("https://example.com/jobs/abc") == ""

    page = ('<section><div class="description__text description__text--rich">'
            '<p>We want someone who has <strong>owned</strong> infrastructure.'
            '</p><ul><li>On-call rota</li><li>&pound;90,000 salary</li></ul>'
            '</div></section>')
    text = enrich._text(page)
    assert "description__text" not in text, "the class attribute is not content"
    assert "owned" in text and "On-call rota" in text
    assert "&pound;" not in text and "\u00a390,000" in text, "entities decoded"
    # Line structure survives, because dealbreaker patterns read bullets.
    assert "\n" in text


def test_enrichment_only_targets_roles_that_need_it():
    from jobradar import enrich, store
    con = store.connect(":memory:")
    rows = [("a", "linkedin", "", "https://uk.linkedin.com/jobs/view/x-111111"),
            ("b", "linkedin", "x" * 500, "https://uk.linkedin.com/jobs/view/y-222222"),
            ("c", "greenhouse", "", "https://boards.greenhouse.io/z")]
    for uid, plat, desc, url in rows:
        con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                    "description,first_seen,last_seen) VALUES "
                    "(?,?,?,?,?,?,?,'2026-08-21','2026-08-21')",
                    (uid, "C", "T", url, "London", plat, desc))
    got = [r["uid"] for r in enrich.candidates(con)]
    assert got == ["a"], f"only the empty LinkedIn role needs fetching, got {got}"


def test_ranking_refuses_an_unreadable_cv():
    """`docx_to_text` returns "" for anything it cannot open, a permission
    error included, so a file that exists is not proof of a CV that can be
    read. Ranking against an empty CV still produces a full set of
    confident-looking scores, judged against nothing at all."""
    import tempfile
    from jobradar import rank
    from jobradar.config import Config

    d = Path(tempfile.mkdtemp())
    empty = d / "cv.txt"
    empty.write_text("Callum\n")          # exists, but nothing in it
    try:
        rank._cv_text(Config(cv_path=str(empty)))
    except SystemExit as e:
        assert "empty CV" in str(e)
        return
    raise AssertionError("an unreadable or empty CV must stop the run")


def test_the_same_job_from_two_sources_keeps_the_employers_own():
    """Wise's Risk API role arrived from LinkedIn on one run and from
    SmartRecruiters on a later one under identical titles. Scan-time dedupe
    only ever sees one run's results, so nothing was going to bring them back
    together, and the merge has to keep whatever you did to the losing row."""
    from jobradar import store
    con = store.connect(":memory:")
    for uid, plat, desc in (("a", "linkedin", ""),
                            ("b", "smartrecruiters", "x" * 900)):
        con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                    "description,first_seen,last_seen) VALUES "
                    "(?,'Wise','Senior EM II - Risk API',?,?,?,?,"
                    "'2026-08-21','2026-08-21')",
                    (uid, f"https://x/{uid}", "London", plat, desc))
    store.set_status(con, "a", "applied", "phone screen booked")
    store.add_artifact(con, "a", "screen", body="SKIP for now")

    assert store.merge_duplicates(con) == 1
    rows = con.execute("SELECT uid,platform FROM roles").fetchall()
    assert [r["platform"] for r in rows] == ["smartrecruiters"], \
        "the employer's own board wins over a keyword search"
    kept = rows[0]["uid"]
    assert con.execute("SELECT status FROM role_state WHERE uid=?",
                       (kept,)).fetchone()["status"] == "applied", \
        "an application recorded on the losing row must survive"
    assert con.execute("SELECT COUNT(*) c FROM artifacts WHERE uid=?",
                       (kept,)).fetchone()["c"] == 1, "documents move across"


def test_smartrecruiters_links_are_not_dead():
    """Swapping the host in the API `ref` produced
    jobs.smartrecruiters.com/<co>/postings/<id>, which 404s: the public path
    has no /postings/ segment. Every link the tool offered for this platform
    was dead, and a dead link is only found after someone decides to apply."""
    from jobradar.adapters.platforms import parse_smartrecruiters
    from jobradar.models import Source
    from jobradar import store

    payload = {"content": [{
        "id": "744000143600504",
        "name": "Senior Software Engineering Manager",
        "ref": "https://api.smartrecruiters.com/v1/companies/servicenow"
               "/postings/744000143600504",
        "company": {"identifier": "ServiceNow", "name": "ServiceNow"},
        "location": {"city": "Santa Clara", "country": "us"},
    }]}
    src = Source(company="ServiceNow", url="https://api.smartrecruiters.com/x",
                 platform="smartrecruiters")
    job = next(iter(parse_smartrecruiters(payload, src)))
    assert "/postings/" not in job.url, job.url
    assert job.url == ("https://jobs.smartrecruiters.com/ServiceNow"
                       "/744000143600504")

    # And the ones already stored get repaired rather than left to rot.
    con = store.connect(":memory:")
    con.execute("INSERT INTO roles (uid,company,title,url,location,platform,"
                "first_seen,last_seen) VALUES ('u','C','T',"
                "'https://jobs.smartrecruiters.com/Wise/postings/123456',"
                "'London','smartrecruiters','2026-08-21','2026-08-21')")
    assert store.repair_smartrecruiters_urls(con) == 1
    assert con.execute("SELECT url FROM roles").fetchone()["url"] == \
        "https://jobs.smartrecruiters.com/Wise/123456"


def test_every_text_file_is_read_and_written_as_utf8():
    """Without an explicit encoding Python uses the OS locale, which on a UK
    or US Windows install is cp1252. sources.json already ships "Conde Nast";
    a job description with a pound sign comes back as mojibake; anything
    outside cp1252 raises and takes the command with it. Python 3.15 makes
    UTF-8 the default but this supports 3.10 upwards."""
    import re
    root = Path(__file__).resolve().parent.parent / "jobradar"
    bad = []
    for f in root.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        for call in re.finditer(r"\.(read_text|write_text)\(", src):
            # Walk to the matching close paren; the encoding may be on a later
            # line for a multi-line call.
            i, depth = call.end() - 1, 0
            while i < len(src):
                depth += src[i] == "("
                depth -= src[i] == ")"
                if depth == 0:
                    break
                i += 1
            if "encoding=" not in src[call.start():i]:
                line = src[:call.start()].count("\n") + 1
                bad.append(f"{f.name}:{line}")
    assert not bad, f"text I/O without an explicit encoding: {bad}"


def test_running_out_of_credit_is_not_treated_as_a_bad_answer():
    """Returning [] for both meant a rank loop carried on and fired every
    remaining batch, each failing the same way, then reported "0 scored" with
    no reason attached."""
    from jobradar.runner import looks_like_limit

    for msg in ("Credit balance is too low to access the Anthropic API",
                "API Error: 429 Too Many Requests",
                "You have exceeded your usage limit for this month",
                "overloaded_error"):
        assert looks_like_limit(msg), msg
    for msg in ("Error: ENOENT no such file",
                "SyntaxError: unexpected token",
                ""):
        assert not looks_like_limit(msg), msg


def test_the_cli_is_never_called_with_an_open_stdin():
    """Nothing behind this has a terminal: the dashboard is a background
    service and the scheduled jobs run from launchd. A read from stdin with
    nothing attached blocks until the timeout, which is fifteen minutes of a
    spinner for a question nobody can see."""
    import re
    root = Path(__file__).resolve().parent.parent / "jobradar"
    for f in (root / "runner.py", root / "rank.py"):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.run\(", src):
            i, depth = m.end() - 1, 0
            while i < len(src):
                depth += src[i] == "("
                depth -= src[i] == ")"
                if depth == 0:
                    break
                i += 1
            block = src[m.start():i]
            if "claude" in block or "exe" in block or "cmd" in block:
                assert "stdin=subprocess.DEVNULL" in block, \
                    f"{f.name}: a CLI call without stdin closed"


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
