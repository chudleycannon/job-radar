"""Description coverage: which platforms produce a role nobody can screen.

A role with no description is not a lead, it is a blank. `rank` skips it,
`generate` refuses it, every dealbreaker passes it by default and the salary
floor has no figure to act on. So a gap in this module is not a missing
feature, it is a role the owner never hears about and never knows they never
heard about.

The numbers quoted in these tests and in enrich.py come from one measured
scan: 244 bundled boards sampled across every platform in the list, 14,082
postings parsed, 5,488 stored. Every fixture here is real bytes off the wire,
trimmed, with the URL and the date it was recorded in a comment at the top of
the file. Nothing in here touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import enrich, store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str = "", status: int = 200, payload=None):
        self.text = text
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records every URL asked for and answers from a dict.

    A fetcher that quietly asks for the wrong URL is the failure this module
    keeps having: the SmartRecruiters regex, the Workday `/apply` suffix and
    the iCIMS login wall were all a healthy-looking request to an address that
    was never going to carry the advert. Asserting on `asked` is the only way
    a test sees that.
    """

    def __init__(self, answers: dict):
        self.answers = answers
        self.asked: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.asked.append(url)
        got = self.answers.get(url)
        if got is None:
            return FakeResponse("", 404)
        if isinstance(got, (dict, list)):
            return FakeResponse(json.dumps(got), 200, got)
        return FakeResponse(got, 200)


def _add(con, uid, platform, url, description=""):
    con.execute(
        "INSERT INTO roles (uid,company,title,url,location,platform,"
        "description,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, "C", "Engineering Manager", url, "London", platform,
         description, "2026-08-25", "2026-08-25"))


# ----------------------------------------------------------- SmartRecruiters
def test_smartrecruiters_reads_the_url_its_own_parser_now_produces():
    """The single largest hole measured, and it was a regex disagreeing with
    the parser next door.

    `parse_smartrecruiters` was corrected to stop emitting
    `jobs.smartrecruiters.com/<co>/postings/<id>`, because that path 404s and
    every link the tool offered for the platform was dead. The enricher's URL
    pattern still required the `/postings/` segment it had just been told does
    not exist, so it matched nothing and returned "" before making a request.

    Measured: 269 of 269 SmartRecruiters roles in the scan arrived with no
    description at all (the list endpoint carries no `jobAd`), and 0 of 25
    sampled were enriched. 910 bundled boards, 5.1% of the list.
    """
    payload = json.loads(
        (FIXTURES / "smartrecruiters_posting.json").read_text(encoding="utf-8"))
    api = ("https://api.smartrecruiters.com/v1/companies/ScalableGmbH"
           "/postings/744000141680580")
    session = FakeSession({api: payload})

    text = enrich._from_smartrecruiters(
        "https://jobs.smartrecruiters.com/ScalableGmbH/744000141680580",
        session)

    assert session.asked == [api], session.asked
    assert len(text) > 2000, len(text)
    # The qualifications section is where the must-haves are, which is what
    # the dealbreakers are actually judged against, and it is a separate
    # section from the advert body on this platform.
    assert "You have completed a degree" in text
    assert "You will develop, implement" in text
    # And the sections arrive in the documented order, not dictionary order.
    assert text.index("You will develop") < text.index("You have completed")
    assert "<p>" not in text, "markup is stripped, not handed to the scorer"


def test_smartrecruiters_still_reads_a_url_stored_before_the_parser_was_fixed():
    """Roles recorded under the old link shape are still in the database and
    still need their advert. Both shapes resolve to the same API call."""
    payload = json.loads(
        (FIXTURES / "smartrecruiters_posting.json").read_text(encoding="utf-8"))
    api = ("https://api.smartrecruiters.com/v1/companies/ScalableGmbH"
           "/postings/744000141680580")
    session = FakeSession({api: payload})

    text = enrich._from_smartrecruiters(
        "https://jobs.smartrecruiters.com/ScalableGmbH/postings/744000141680580",
        session)
    assert session.asked == [api]
    assert len(text) > 2000


# ------------------------------------------------------------------ Workday
def test_workday_apply_links_are_trimmed_back_to_the_requisition():
    """CXS answers 406 for a `/apply` suffix or a trailing slash, with a 104
    byte body that says nothing, which is indistinguishable from a dead
    requisition.

    `parse_workday` builds clean URLs, so the boards never hit this. The URLs
    that arrive from somewhere else do: 1,562 of 1,882 Phenom roles measured
    point at the employer's own Workday tenant and carry the `/apply` form.
    Live, the same Thales requisition answered 406 with the suffix and 200
    with 8,035 characters of advert without it.
    """
    base = "https://thales.wd3.myworkdayjobs.com"
    want = (f"{base}/wday/cxs/thales/Careers/job/Glasgow"
            "/Senior-Engineering-Manager_R0319054")
    for given in (f"{base}/Careers/job/Glasgow/Senior-Engineering-Manager_R0319054",
                  f"{base}/Careers/job/Glasgow/Senior-Engineering-Manager_R0319054/",
                  f"{base}/Careers/job/Glasgow/Senior-Engineering-Manager_R0319054/apply",
                  f"{base}/en-US/Careers/job/Glasgow/Senior-Engineering-Manager_R0319054/apply"):
        assert enrich._workday_api(given) == want, given


def test_a_workday_url_that_is_only_an_apply_link_is_not_requested():
    """`/job//apply` trims to nothing, and a CXS call to an empty path is a
    request that cannot succeed. Better to make none."""
    assert enrich._workday_api(
        "https://x.wd3.myworkdayjobs.com/Careers/job//apply") == ""


# ------------------------------------------------------------------ Jobvite
def test_jobvite_reads_a_tenant_that_publishes_no_json_ld():
    """Jobvite and Breezy shared one fetcher on the strength of Breezy always
    carrying a JobPosting block. Jobvite does not always: `savers` and
    `monarchinvestment` serve zero script blocks of any type on a healthy 200,
    so the shared reader returned "" and the role stayed unscreenable. 3 of 13
    sampled Jobvite postings that answered 200 were in that state. 257 bundled
    boards, 1.4% of the list.
    """
    page = (FIXTURES / "jobvite_posting.html").read_text(encoding="utf-8")
    assert not enrich._LD_BLOCK.findall(page), \
        "the fixture must be a tenant that serves no JSON-LD block"
    url = "https://jobs.jobvite.com/savers/job/oJVFAfwD"
    session = FakeSession({url: page})

    text = enrich._from_jobvite(url, session)
    assert len(text) > 3000, len(text)
    assert "Financial" in text and "Analyst" in text


def test_the_jobvite_advert_is_found_by_counting_not_by_a_lazy_match():
    """The advert is a chain of nested divs. A lazy `(.*?)</div>` stops at the
    first inner close and returns 104 characters of the 5,894 in this posting,
    which is the dangerous size of wrong: under the 200 floor it looks like a
    failed fetch, and just over it would be stored and screened as the whole
    advert."""
    import re

    page = (FIXTURES / "jobvite_posting.html").read_text(encoding="utf-8")
    lazy = re.search(r'jv-job-detail-description[^>]*>(.*?)</div>', page, re.S)
    assert lazy is not None
    assert len(enrich._strip_blocks(lazy.group(1))) < 200

    url = "https://jobs.jobvite.com/savers/job/oJVFAfwD"
    counted = enrich._from_jobvite(url, FakeSession({url: page}))
    assert len(counted) > 3000


def test_jobvite_still_prefers_the_shared_json_ld_reader():
    """The fallback is a fallback. A tenant that does publish the block must
    be read through the same code Breezy is, or the two drift again."""
    url = "https://jobs.jobvite.com/ness/job/oTeeAfwF"
    page = ('<script type="application/ld+json">'
            '{"@type":"JobPosting","description":"<p>' + "advert " * 60
            + '</p>"}</script>'
            '<div class="jv-job-detail-description">fallback text</div>')
    text = enrich._from_jobvite(url, FakeSession({url: page}))
    assert text.startswith("advert")
    assert "fallback" not in text


# ------------------------------------------------------------------ Avature
def test_avature_reads_the_older_template_through_its_microdata():
    """Avature ships two page templates and only one of them was readable.

    jobs.colorado.edu is an ordinary Avature tenant serving
    `/jobs/JobDetail/<slug>/<id>` with no JSON-LD and none of the field-value
    divs the first fallback looks for, so both existing readers returned "" on
    a healthy 200. What it does carry is schema.org as microdata: eleven
    `itemprop="description"` sections, one per accordion panel. All of them
    are read, because the qualifications panel is a separate one from the
    summary and dropping it drops the must-haves.
    """
    page = (FIXTURES / "avature_microdata.html").read_text(encoding="utf-8")
    assert not enrich._LD_BLOCK.findall(page)
    assert not enrich._AV_FIELD.findall(page), \
        "the fixture must be the template the first fallback cannot read"
    url = "https://jobs.colorado.edu/jobs/JobDetail/Director-of-Protective-Services/74468"
    session = FakeSession({url: page})

    text = enrich._from_avature(url, session)
    assert len(text) > 2000, len(text)
    assert "Protective Services" in text
    # More than one panel made it in: the fixture keeps four of the eleven.
    assert text.count("\n\n") >= 3


def test_avature_does_not_lose_the_json_ld_tenant_to_the_new_fallback():
    """Tesco's Avature board does publish a JobPosting block, and that path
    must stay first: the microdata fallback exists for the tenants that do
    not, not instead of the block."""
    url = "https://careers.tesco.com/en_GB/careersmarketplace/JobDetail/X/1"
    page = ('<script type="application/ld+json">'
            '{"@type":"JobPosting","description":"<p>' + "block " * 80
            + '</p>"}</script>'
            '<div itemprop="description">microdata text</div>')
    text = enrich._from_avature(url, FakeSession({url: page}))
    assert text.startswith("block")
    assert "microdata" not in text


# -------------------------------------------------------------------- iCIMS
def test_icims_drops_the_login_wall_before_asking_for_the_posting():
    """`.../job/login` is the sign-in page in front of the same requisition.
    It answers 200 with a 28KB page and no JobPosting block, so it reads as a
    healthy page with no advert on it rather than as a wall.

    This is not getting past bot protection: the requisition itself is public
    and `in_iframe=1` is the same parameter iCIMS serves its own search
    results with. It is a URL shape, arriving from Phenom boards whose apply
    link points at the employer's iCIMS tenant. Measured live: 0 characters at
    `/job/login`, 4,746 at `/job`.
    """
    posting = ("https://careers-orange.icims.com/jobs/27273"
               "/customer-service-engineer---voice/job")
    page = ('<script type="application/ld+json">'
            '{"@type":"JobPosting","description":"<p>' + "advert " * 80
            + '</p>"}</script>')
    session = FakeSession({posting + "?in_iframe=1": page})

    text = enrich._from_icims(posting + "/login", session)
    assert session.asked == [posting + "?in_iframe=1"], session.asked
    assert len(text) > 400


# ------------------------------------------------------- URL-shape dispatch
def test_a_role_is_enriched_by_the_shape_of_its_url_when_its_platform_has_none():
    """The `platform` column says which BOARD a role came off, not which
    system publishes the advert, and for two bundled platforms those differ.

    Atlassian's two `custom` boards hand back iCIMS posting URLs: all 111
    roles measured, every one with an empty description, and "custom" is not a
    key in FETCHERS so nothing was ever going to fetch them. Phenom is the
    same shape at scale: of 1,882 roles measured, 1,562 point at a Workday
    tenant, 73 at iCIMS and 33 at a SuccessFactors jobs2web host.
    """
    assert enrich.fetcher_for(
        "https://careers-americas.icims.com/jobs/26656/senior-em/job?mode=apply",
        "custom") == [enrich._from_icims]
    assert enrich.fetcher_for(
        "https://thales.wd3.myworkdayjobs.com/Careers/job/Glasgow/EM_R1/apply",
        "phenom") == [enrich._from_workday]
    assert enrich.fetcher_for(
        "https://sercolimited.jobs2web.com/job/London/Engineer_1",
        "phenom") == [enrich._from_rmk]


def test_a_url_that_belongs_to_no_known_system_is_left_alone():
    """The fallback is a list of hosts a fetcher above is already written
    against. It is not a guess at an unknown platform, and a Greenhouse board
    URL or a placeholder apply link must still produce no fetcher at all
    rather than a wasted request."""
    assert enrich.fetcher_for("https://boards.greenhouse.io/z", "greenhouse") == []
    # Actalent's Phenom board publishes this literal string as every job's
    # apply link. There is nothing behind it and there never was.
    assert enrich.fetcher_for("https://www.ats.com?jobId=123", "phenom") == []
    assert enrich.fetcher_for("", "") == []


def test_the_platform_fetcher_is_tried_before_the_url_one():
    """A role on a platform with its own fetcher keeps it, because that
    fetcher is the one written against that board's stored URL shape."""
    chain = enrich.fetcher_for(
        "https://x.wd3.myworkdayjobs.com/Careers/job/London/EM_R1", "icims")
    assert chain[0] is enrich._from_icims
    assert chain[1] is enrich._from_workday
    assert len(chain) == 2, "at most two requests for one role"


def test_a_role_whose_url_shape_is_known_is_selected_even_off_an_odd_platform():
    """The dispatch is worth nothing if the candidate query never offers the
    role. Both halves are derived from URL_FETCHERS so they cannot drift."""
    con = store.connect(":memory:")
    _add(con, "custom1", "custom",
         "https://careers-americas.icims.com/jobs/26656/senior-em/job?mode=apply")
    _add(con, "phenom1", "phenom",
         "https://thales.wd3.myworkdayjobs.com/Careers/job/Glasgow/EM_R1/apply")
    _add(con, "gh1", "greenhouse", "https://boards.greenhouse.io/acme/jobs/1")
    con.commit()

    got = {r["uid"] for r in enrich.candidates(con)}
    assert got == {"custom1", "phenom1"}, got


def test_every_url_pattern_has_a_matching_sql_like():
    """The regex decides what gets fetched and the LIKE decides what gets
    offered. If they disagree the fetcher is either never called or called on
    rows it cannot read, and both failures are silent."""
    # One real URL per pattern, in the same order as the table. Every one of
    # these was measured coming off a live board during the scan.
    live = [
        "https://thales.wd3.myworkdayjobs.com/Careers/job/Glasgow/EM_R1",
        "https://wd5.myworkdaysite.com/en-US/recruiting/t/s/job/L/EM_R1",
        "https://jobs.smartrecruiters.com/ScalableGmbH/744000141680580",
        "https://careers-americas.icims.com/jobs/26656/senior-em/job",
        "https://sercolimited.jobs2web.com/job/London/Engineer_1",
        "https://vanoord.avature.net/en_US/careers/JobDetail/Engineer/3018",
        "https://cinfin.taleo.net/careersection/ex/jobdetail.ftl?job=1",
        "https://blenderbox.breezy.hr/p/9ab41534900b-senior-engineer",
        "https://ipgroupplc.bamboohr.com/careers/123",
        "https://acme.applytojob.com/apply/abc/engineering-manager",
        "https://jobs.jobvite.com/savers/job/oJVFAfwD",
        "https://fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    ]
    assert len(live) == len(enrich.URL_FETCHERS), \
        "a pattern was added without a real URL to check it against"
    for (pat, like, fn), url in zip(enrich.URL_FETCHERS, live):
        assert like.startswith("%") and like.endswith("%"), like
        assert "_" not in like, f"{like}: _ is a wildcard in SQL LIKE"
        assert callable(fn)
        assert pat.search(url), f"{like} regex does not match {url}"
        # The same URL has to satisfy the SQL half, or the row is fetched by
        # nothing because it was never offered.
        needle = like.strip("%")
        assert needle in url, f"{like} LIKE does not match {url}"


# ---------------------------------------------------- teasers, not adverts
def test_a_teaser_long_enough_to_clear_the_floor_is_still_fetched():
    """The worst of the three states. Phenom's `descriptionTeaser` averages
    290 characters and Oracle's `ShortDescriptionStr` runs 200 to 1,000, both
    comfortably over a 200 character floor, so the role looked described, the
    candidate query never offered it, and the dealbreakers ran against a
    paragraph of marketing.

    Measured: 1,714 of 1,882 Phenom roles cleared the old floor on the teaser
    alone, and 185 of 483 Oracle roles did. Fetching the full requisition for
    20 of the Oracle ones returned a median of 6,400 characters, every one
    between 3.8x and 16.2x longer than what was stored.
    """
    con = store.connect(":memory:")
    _add(con, "orc", "oracle",
         "https://fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
         "x" * 650)
    _add(con, "phe", "phenom",
         "https://thales.wd3.myworkdayjobs.com/Careers/job/Glasgow/EM_R1/apply",
         "x" * 290)
    # A greenhouse role with a real 650 character advert is not a teaser and
    # must not become a request.
    _add(con, "gh", "greenhouse", "https://boards.greenhouse.io/a/jobs/1",
         "x" * 650)
    con.commit()

    got = {r["uid"] for r in enrich.candidates(con)}
    assert got == {"orc", "phe"}, got


def test_a_full_advert_on_a_stub_platform_is_not_fetched_again():
    """The stub floor is 1,200, above every teaser measured and below every
    real advert measured. A role that already has the advert is done."""
    con = store.connect(":memory:")
    _add(con, "orc", "oracle",
         "https://fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
         "x" * 6400)
    con.commit()
    assert enrich.candidates(con) == []


def test_enrichment_never_replaces_a_longer_description_with_a_shorter_one():
    """The stub floors make this reachable for the first time: before them
    every candidate had under 200 characters, so anything fetched was an
    improvement by definition. Now a role can arrive holding a 650 character
    Oracle teaser, and a fetch that comes back with 300 characters of parse
    failure would overwrite the advert with less than it replaced.
    """
    con = store.connect(":memory:")
    _add(con, "orc", "oracle",
         "https://fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
         "TEASER " * 100)
    con.commit()
    rows = enrich.candidates(con)
    assert len(rows) == 1

    old = dict(enrich.FETCHERS)
    enrich.FETCHERS["oracle"] = lambda u, s=None, timeout=20: "short " * 40
    try:
        got, tried = enrich.run(con, None, rows, pause=0.0, concurrency=1)
    finally:
        enrich.FETCHERS.clear()
        enrich.FETCHERS.update(old)

    assert (got, tried) == (0, 1), "a shorter answer is not an improvement"
    kept = con.execute("SELECT description FROM roles WHERE uid='orc'").fetchone()
    assert kept["description"].startswith("TEASER")


def test_a_longer_fetch_does_replace_the_teaser():
    """The other half of the same rule, or the stub floors buy nothing."""
    con = store.connect(":memory:")
    _add(con, "orc", "oracle",
         "https://fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
         "TEASER " * 100)
    con.commit()
    rows = enrich.candidates(con)

    old = dict(enrich.FETCHERS)
    enrich.FETCHERS["oracle"] = lambda u, s=None, timeout=20: "ADVERT " * 900
    try:
        got, tried = enrich.run(con, None, rows, pause=0.0, concurrency=1)
    finally:
        enrich.FETCHERS.clear()
        enrich.FETCHERS.update(old)

    assert (got, tried) == (1, 1)
    kept = con.execute("SELECT description FROM roles WHERE uid='orc'").fetchone()
    assert kept["description"].startswith("ADVERT")


def test_run_still_works_on_rows_that_carry_no_stored_length():
    """`run` takes whatever rows it is handed. A caller with its own query
    should not have to know about a column added for the stub floors."""
    con = store.connect(":memory:")
    _add(con, "u1", "workday", "https://x.wd1.myworkdayjobs.com/s/job/L/EM_R1")
    con.commit()
    rows = con.execute("SELECT uid, url, platform, salary_confirmed "
                       "FROM roles").fetchall()

    old = dict(enrich.FETCHERS)
    enrich.FETCHERS["workday"] = lambda u, s=None, timeout=20: "y" * 400
    try:
        got, tried = enrich.run(con, None, rows, pause=0.0, concurrency=1)
    finally:
        enrich.FETCHERS.clear()
        enrich.FETCHERS.update(old)
    assert (got, tried) == (1, 1)


def test_a_stub_floor_only_applies_to_the_platform_it_was_measured_on():
    """1,200 is a number taken off two platforms' teaser fields. Applying it
    everywhere would refetch every genuinely short advert on every board on
    every scan, which is thousands of requests bought with nothing."""
    assert set(enrich.STUB_FLOORS) == {"phenom", "oracle"}
    assert all(n > enrich.MIN_DESC for n in enrich.STUB_FLOORS.values())
    sql = enrich._floor_sql()
    for platform, floor in enrich.STUB_FLOORS.items():
        assert f"WHEN '{platform}' THEN {floor}" in sql
    assert f"ELSE {enrich.MIN_DESC}" in sql
