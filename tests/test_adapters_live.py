"""Regressions for defects found by probing real boards, 2026-08-25.

Every case here was a board that answered 200 with a full payload, that the
parser read without raising, and that produced rows a reader would have
believed. None of them would have failed a test written from the fixture the
parser was built against, because the fixture matched the parser: it was the
BOARD that had a shape the parser did not have, and only a live request could
say so.

The payloads below are trimmed captures of real responses taken on
2026-08-25, kept verbatim apart from dropping rows and truncating advert text.
They are files rather than live calls on purpose. A test that fetches is a
test that fails on a train, and a test that fetches a job board is a test that
goes red the week the employer fills the role.

What each one pins:

  personio    the posting URL was built out of `src.company`, the label a
              human typed into the source list, so "Auxmoney Gmbh" produced a
              hostname with a space in it. HTTP 400, on every one of the 1,258
              Personio boards whose display name is not identical to its
              subdomain.
  personio    the same feed came back mojibake, because Personio serves
              `text/xml` with no charset and requests then assumes Latin-1.
  recruitee   `careers_url` is the employer's vanity domain and it outlives
              the board: Makersite's is a 404 while the recruitee.com address
              for the same posting is a 200.
  rmk         the location was reconstructed from the href slug and, whenever
              the title contained punctuation the slug drops, came out as the
              whole slug with the title in it. The page has a location column.
  rmk         and a date column, which nothing was reading, so all 93 boards
              on this platform produced undated postings.
  phenom      an employer that never configured an apply URL gets Phenom's
              demo placeholder, `https://www.ats.com?jobId=123`, on every
              posting: a 403, and one `Job.uid` shared by the whole board.
  avature     `location` was hard coded to the empty string, and the posted
              date in the card was dropped.
  custom      the generic parser did not know WordPress calls its publish
              date `date`, so every bespoke WordPress board was undated.
  greenhouse  `location.name` is free text and some employers write the work
              pattern in it; the city was sitting unread in `offices`.
  greenhouse  `pay_input_ranges` states no period, so an hourly rate of $54
              was read as a salary of $54 a year.
  workday     `postedOn` states an age, "Posted 19 Days Ago", not a date, so
              every posting on the platform arrived undated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import adapters
from jobradar.models import Source

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _src(platform: str, company: str, url: str) -> Source:
    return adapters.prepare(Source(company=company, url=url, platform=platform))


# ------------------------------------------------------------------ personio
PERSONIO_URL = "https://auxmoney-gmbh.jobs.personio.de/xml"


def _personio_jobs(company: str = "Auxmoney Gmbh"):
    src = _src("personio", company, PERSONIO_URL)
    return adapters.parse(_read("personio_positions.xml"), src)


def test_a_personio_posting_links_to_the_subdomain_that_served_the_feed():
    """The feed states no URL at all -- there is no `<url>` element in a
    Personio `/xml` response -- so the link has to be built, and the only
    trustworthy source for the subdomain is the address we just fetched.

    Built from `src.company` instead, "Auxmoney Gmbh" became
    `https://Auxmoney Gmbh.jobs.personio.de/job/2727726`. A hostname cannot
    contain a space: that URL is HTTP 400, checked live. Three of the four
    boards probed were broken this way and the fourth only worked by
    coincidence, its display name happening to equal its subdomain.
    """
    jobs = _personio_jobs()
    assert jobs, "the fixture holds two positions"
    for j in jobs:
        assert j.url.startswith("https://auxmoney-gmbh.jobs.personio.de/job/"), j.url
        assert " " not in j.url, (
            f"{j.url!r} has a space in the hostname, which is a 400, not a "
            f"job advert")


def test_a_personio_url_does_not_change_when_the_display_name_does():
    """The URL must be a function of the board, not of the label.

    Renaming a source in `sources.json` -- which is an editorial act, done to
    make a list readable -- silently repointed every posting on that board at
    a different hostname, and `Job.uid` is keyed on the URL, so it also
    re-alerted the whole board as new.
    """
    a = [j.url for j in _personio_jobs("Auxmoney Gmbh")]
    b = [j.url for j in _personio_jobs("auxmoney GmbH (Düsseldorf)")]
    assert a == b, f"the label changed the link: {a} vs {b}"


def test_a_charsetless_text_response_is_decoded_as_the_utf_8_it_is():
    """Personio serves the feed as bare `text/xml`.

    requests then falls back to ISO-8859-1, which is what RFC 2616 told it to
    do and is wrong here: the feed's own declaration says UTF-8. Every German
    board arrived with "Remote / Düsseldorf" spelled "Remote / DÃ¼sseldorf",
    and a location filter matches neither that nor "München" spelled
    "MÃ¼nchen", so those roles were quietly unfilterable.
    """
    from jobradar import fetch as fetch_mod

    body = (FIXTURES / "personio_positions.xml").read_bytes()

    class R:
        status_code = 200
        headers = {"Content-Type": "text/xml"}
        content = body
        # What requests itself would have chosen for a charsetless text/*.
        encoding = "ISO-8859-1"

        @property
        def text(self):
            return self.content.decode(self.encoding)

    class Session:
        def mount(self, prefix, adapter): pass

        def get(self, url, headers=None, timeout=None):
            return R()

    src = Source(company="Auxmoney Gmbh", url=PERSONIO_URL, platform="personio")
    res = fetch_mod.fetch_one(src, session=Session(),
                              limiter=fetch_mod.HostLimiter(rps=0))
    assert res.ok, res.error
    assert "Düsseldorf" in res.payload, (
        "the umlaut came back as mojibake, which no location filter matches")
    assert "DÃ¼sseldorf" not in res.payload

    jobs = adapters.parse(res.payload, src)
    assert any("Düsseldorf" in j.location for j in jobs), \
        [j.location for j in jobs]


def test_a_latin_1_board_is_not_forced_through_a_decoder_it_would_fail():
    """The override is only safe because it checks first.

    A body that is genuinely Latin-1 has bytes that are not valid UTF-8, and
    forcing UTF-8 on it would replace them with question marks. So the
    decision is made by attempting the decode, not by assuming.
    """
    from jobradar import fetch as fetch_mod

    class R:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        # 0xE9 is "é" in Latin-1 and an illegal lead byte in UTF-8.
        content = b"<html>caf\xe9 latte</html>"
        encoding = "ISO-8859-1"

        @property
        def text(self):
            return self.content.decode(self.encoding)

    class Session:
        def mount(self, prefix, adapter): pass

        def get(self, url, headers=None, timeout=None):
            return R()

    res = fetch_mod.fetch_one(
        Source(company="x", url="https://example.com/jobs", platform="custom"),
        session=Session(), limiter=fetch_mod.HostLimiter(rps=0))
    assert res.ok, res.error
    assert "café" in res.payload, res.payload


# ----------------------------------------------------------------- recruitee
def test_a_recruitee_posting_links_to_the_host_that_answered():
    """`careers_url` is whatever vanity domain the employer once pointed at
    Recruitee, and it outlives the board.

    Makersite's offers all state `https://makersite.io/o/<slug>`, which is a
    404 today, while `https://makersitegmbh.recruitee.com/o/<slug>` -- the
    host we had just fetched the offers from -- is a 200 with the advert on
    it. Both checked live. The published field was the broken one, so the link
    is now rebuilt on the host with evidence behind it.
    """
    payload = json.loads(_read("recruitee_offers.json"))
    src = _src("recruitee", "Makersite",
               "https://makersitegmbh.recruitee.com/api/offers/")
    jobs = adapters.parse(payload, src)
    assert jobs, "the fixture holds two published offers"
    for j in jobs:
        assert j.url.startswith("https://makersitegmbh.recruitee.com/o/"), j.url
    # The slug is still the offer's own, not something invented from the title.
    assert any(j.url.endswith("/senior-data-scientist-mfx-1") for j in jobs), \
        [j.url for j in jobs]


# ----------------------------------------------------------------------- rmk
def _rmk_jobs():
    src = _src("rmk", "Adidas",
               "https://jobs.adidas-group.com/search/?q=&sortColumn=referencedate")
    return adapters.parse(_read("rmk_search.html"), src)


def test_an_rmk_location_is_a_place_and_not_the_whole_href_slug():
    """The old rule was to find the title inside the slug and keep whatever
    came before it.

    That fails the moment the title contains a character the slug drops, which
    is most titles: "ALTERNANCE - Vendeur Polyvalent adidas (H/F/D)" is slugged
    "ALTERNANCE-Vendeur-Polyvalent-adidas-(HFD)", so the match failed and the
    location became the entire slug. Live, before this changed, adidas
    reported a location of "Ile Saint Denis ALTERNANCE Vendeur Polyvalent
    adidas (HFD)" and Scotiabank one of "Toronto Senior Manager, Global
    Connectivity, International Wealth Management Toronto, ON ON M5H 0B4".
    """
    jobs = _rmk_jobs()
    assert jobs, "the fixture holds three rows"
    first = jobs[0]
    assert first.location == "Ile Saint-Denis, FR", first.location
    for j in jobs:
        assert j.title.lower() not in j.location.lower(), (
            f"{j.location!r} is the job title wearing a location's clothes")


def test_an_rmk_row_keeps_the_date_the_page_prints_next_to_it():
    """The result table has a date column and nothing was reading it, so every
    posting from all 93 boards on this platform arrived undated and could
    never score for recency.
    """
    jobs = _rmk_jobs()
    dated = [j for j in jobs if j.posted_at]
    assert dated, "the adidas rows both carry `Aug 25, 2026` in a colDate cell"
    assert all(j.posted_at == "2026-08-25" for j in dated), \
        [j.posted_at for j in dated]


def test_a_tenant_with_no_location_column_still_gets_a_place_from_the_slug():
    """The columns are configured per tenant, the same way Taleo's are.

    Burberry emit neither a location nor a date column, so the slug is all
    there is, and the fallback has to survive an href escaped twice --
    `Women&amp;apos;s` -- which one unescaping pass leaves as the four letters
    "apos" and which used to stop the title matching its own slug.
    """
    src = _src("rmk", "Burberry Group", "https://burberrycareers.com/search/?q=")
    jobs = adapters.parse(_read("rmk_search_slug_only.html"), src)
    assert len(jobs) == 1, [j.url for j in jobs]
    j = jobs[0]
    assert j.title == "Director, Merchandising Women's", j.title
    assert j.location == "New York", j.location
    assert j.posted_at is None, "there is no date on this board to invent one from"


def test_an_rmk_row_with_no_readable_location_says_nothing_rather_than_furniture():
    """When the slug's shape is not the one assumed, no location is the honest
    answer.

    Handing back the raw slug filled the location field with page furniture,
    which reads as a place, filters as a place, and is not one.
    """
    from jobradar.adapters.platforms import _rmk_slug_location

    assert _rmk_slug_location("/job/Some-Slug-That-Is-Not-The-Title/12/",
                              "Head of Engineering") == ""


# -------------------------------------------------------------------- phenom
def _phenom_jobs():
    src = _src("phenom", "Aston Carter",
               "https://careers.astoncarter.com/gb/en/search-results?s=1")
    return adapters.parse(json.loads(_read("phenom_widgets.json")), src)


def test_a_phenom_board_that_never_set_an_apply_url_still_links_to_the_advert():
    """Phenom lets the employer type in the apply URL, and some have not:
    Aston Carter's board answers with Phenom's own demo placeholder,
    `https://www.ats.com?jobId=123`, on every posting.

    That URL is a 403. Phenom always serves its own advert page as well, at
    `<board>/job/<jobSeqNo>/<title-slug>`, and that page carries the whole
    advert rather than an apply form -- verified 200 for Aston Carter, Honda
    and Advance Auto Parts.
    """
    jobs = _phenom_jobs()
    assert len(jobs) == 2, [j.url for j in jobs]
    assert not any("ats.com" in j.url for j in jobs), [j.url for j in jobs]
    aston = [j for j in jobs if "astoncarter.com" in j.url]
    assert len(aston) == 2, [j.url for j in jobs]
    assert aston[0].url == (
        "https://careers.astoncarter.com/gb/en/job/"
        "ASCAUSJP006210688ENGB/Marketing-Manager"), aston[0].url


def test_two_phenom_postings_never_share_one_url():
    """`Job.uid` is keyed on the URL, so a board where every posting states the
    same link does not report six roles: it reports one, and the other five can
    never be alerted on at all.
    """
    jobs = _phenom_jobs()
    urls = [j.url for j in jobs]
    assert len(set(urls)) == len(urls), urls


def _honda_jobs():
    src = _src("phenom", "American Honda Motor Company",
               "https://careers.honda.com/us/en/search-results?s=1")
    return adapters.parse(json.loads(_read("phenom_widgets_partial.json")), src)


def test_a_phenom_posting_with_a_real_apply_url_keeps_it():
    """The rebuild is a fallback, not a replacement.

    Eight of Honda's postings do state a genuine per-posting link, and that is
    the one worth following, so a rule that always rewrote the URL would be
    throwing away a better answer than the one it substituted.
    """
    kept = [j for j in _honda_jobs() if "sapsf.com" in j.url]
    assert len(kept) == 1, [j.url for j in _honda_jobs()]
    assert kept[0].url.endswith("career_job_req_id=7409"), kept[0].url


def test_a_phenom_posting_with_no_apply_url_at_all_is_still_reported():
    """Honda state an apply URL on eight of their 188 postings and none on the
    other 180.

    Those 180 were dropped on the spot, because a row with no URL fails the
    `title and url` guard in the parser and again in `adapters.parse`. The
    board reported eight open roles and had 188.
    """
    jobs = _honda_jobs()
    assert len(jobs) == 2, [j.url for j in jobs]
    honda = [j for j in jobs if j.url.startswith("https://careers.honda.com/")]
    assert honda, [j.url for j in jobs]
    assert honda[0].url == (
        "https://careers.honda.com/us/en/job/AHMAHMUS10993EXTERNALENUS/"
        "Engineering-Co-op-Intern-Spring-2027"), honda[0].url


# ------------------------------------------------------------------- avature
def _avature_jobs():
    src = _src("avature", "Auspost",
               "https://jobs.auspost.com.au/en_GB/careers/SearchJobs/"
               "?jobRecordsPerPage=50")
    return adapters.parse(_read("avature_search.html"), src)


def test_an_avature_card_that_states_a_location_is_believed():
    """`location` was hard coded to "" for every Avature posting, on the
    grounds that the location was only ever in the slug.

    It is in the markup on the boards that emit the column: Australia Post
    print `<span class="list-item-location">Richmond, VIC</span>`. An empty
    location is what a location filter sees when a role has no location at
    all, so those roles were being judged placeless.
    """
    jobs = _avature_jobs()
    assert jobs, "the fixture holds three cards"
    placed = [j for j in jobs if j.location]
    assert len(placed) == 2, [(j.title, j.location) for j in jobs]
    assert placed[0].location == "Richmond, VIC", placed[0].location


def test_an_avature_card_keeps_its_posted_date():
    """"Posted 21-Aug-2026" sits in the card's subtitle strip, and dropping it
    left every posting on all 95 Avature boards undated and unable to score
    for recency."""
    jobs = _avature_jobs()
    dates = [j.posted_at for j in jobs if j.posted_at]
    assert dates, [(j.title, j.posted_at) for j in jobs]
    assert "2026-08-21" in dates, dates


def test_an_avature_row_does_not_borrow_the_next_row_s_location():
    """The Broad Institute's cards state a posted date and no location.

    The strip is read from the window after the title link and cut at the next
    card, so a board that omits a field gets nothing rather than the
    neighbouring row's answer -- which is the failure that makes a scraped
    location worse than none, because it is confidently wrong.
    """
    src = _src("avature", "Broad Institute",
               "https://broadinstitute.avature.net/en_US/careers/SearchJobs/"
               "?jobRecordsPerPage=50")
    jobs = adapters.parse(_read("avature_search.html"), src)
    broad = [j for j in jobs if "broadinstitute" in j.url]
    assert broad, [j.url for j in jobs]
    assert broad[0].location == "", broad[0].location
    assert broad[0].posted_at == "2026-08-13", broad[0].posted_at


# -------------------------------------------------------- custom / WordPress
def test_a_wordpress_board_keeps_the_date_wordpress_calls_date():
    """The commonest bespoke board is a WordPress site exposing its `job` post
    type at /wp-json/wp/v2/job, and Roke's is one, with 34 live roles.

    The generic parser looked for `postedDate`, `posted_at`, `updated_at` and
    `publishedAt`, none of which WordPress emits: it calls the publish date
    `date`. So every posting from every board of that shape arrived undated
    and could never score for recency.
    """
    payload = json.loads(_read("custom_wordpress_jobs.json"))
    src = _src("", "Roke", "https://www.roke.co.uk/wp-json/wp/v2/job?per_page=100")
    assert src.platform == "custom", src.platform
    jobs = adapters.parse(payload, src)
    assert len(jobs) == 2, [j.title for j in jobs]
    assert jobs[0].title == "Security Advisor", jobs[0].title
    assert jobs[0].url == "https://www.roke.co.uk/careers/current-roles/402/", jobs[0].url
    assert jobs[0].posted_at == "2026-08-21", jobs[0].posted_at


# ---------------------------------------------------------------- greenhouse
def test_a_greenhouse_row_that_states_a_work_pattern_still_gets_a_place():
    """`location.name` is a free-text box and some employers put the working
    arrangement in it instead of a city.

    Cloudflare are the clearest case: 247 of their 306 open roles say "Hybrid"
    or "In-Office" there and nothing else, and Stripe put "N/A" on 21. The
    `offices` list on those same rows names the city, and it was only consulted
    when `location` was empty -- which "Hybrid" is not. So four in five
    Cloudflare roles reached the country filter carrying a work pattern where
    their location should be, and a search for UK roles could neither keep
    them nor rule them out.
    """
    payload = json.loads(_read("greenhouse_jobs.json"))
    src = _src("greenhouse", "Cloudflare",
               "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs"
               "?content=true&pay_transparency=true")
    jobs = adapters.parse(payload, src)
    by_title = {j.title: j for j in jobs}
    assert by_title["Account Executive, FedCiv"].location == \
        "Hybrid, Washington, DC", by_title["Account Executive, FedCiv"].location
    assert by_title["Accounting Intern (Fall 2026)"].location == \
        "In-Office, Austin, TX"
    # "N/A" says nothing at all, so it is replaced rather than kept.
    assert by_title["Backend Engineer, Billing/Tax"].location == \
        "Canada Locations"


def test_a_greenhouse_row_that_names_a_city_is_left_alone():
    """The repair is for rows with no place in them.

    A stated city is the employer's own answer and more specific than the
    office list it belongs to -- Stripe's London role sits in an office group
    called "United Kingdom Locations" -- so overwriting it would trade a city
    for a country.
    """
    jobs = adapters.parse(json.loads(_read("greenhouse_jobs.json")),
                          _src("greenhouse", "Stripe",
                               "https://boards-api.greenhouse.io/v1/boards/"
                               "stripe/jobs?content=true"))
    london = [j for j in jobs if j.location == "London"]
    assert london, [j.location for j in jobs]


def test_a_greenhouse_hourly_rate_is_not_read_as_a_years_pay():
    """`pay_input_ranges` states no period anywhere, so every figure in it was
    assumed to be annual.

    Databricks publish hourly rates through the same field and say so in the
    title: "SF Bay Area Hourly Rate", `min_cents` 5400. Read as annual that is
    a salary of $54, and `clears_floor` then bins a role paying about $105,000
    a year. Three of Databricks' 825 open roles were priced that way.
    """
    jobs = adapters.parse(json.loads(_read("greenhouse_jobs.json")),
                          _src("greenhouse", "Databricks",
                               "https://boards-api.greenhouse.io/v1/boards/"
                               "databricks/jobs?pay_transparency=true"))
    intern = [j for j in jobs if j.title.startswith("PhD GenAI")][0]
    assert intern.salary.confirmed
    assert intern.salary.period == "hour", intern.salary
    assert intern.salary.max == 60.0, intern.salary
    assert 90_000 < intern.salary.annualised() < 130_000, intern.salary.annualised()


def test_an_unlabelled_greenhouse_figure_too_small_to_be_a_salary_is_not_asserted():
    """Databricks also send 17040 cents under the generic title "Local Pay
    Range". That is $170.40, plainly a rate, and nothing in the payload says
    which kind of rate.

    Only a confirmed figure can disqualify a role, so a figure that cannot be
    read is left unconfirmed rather than asserted -- the same rule the Reed
    adapter already states for an unlabelled figure below 2,000. Asserting it
    as annual disqualified a staff engineering role for paying $170 a year.
    """
    jobs = adapters.parse(json.loads(_read("greenhouse_jobs.json")),
                          _src("greenhouse", "Databricks",
                               "https://boards-api.greenhouse.io/v1/boards/"
                               "databricks/jobs?pay_transparency=true"))
    backline = [j for j in jobs if j.title.startswith("Senior Staff Backline")][0]
    assert not backline.salary.confirmed, backline.salary
    assert backline.salary.label() == "unconfirmed salary"


# ------------------------------------------------------------------- workday
def test_a_workday_posting_gets_a_date_out_of_the_age_workday_states():
    """Workday never states a posting's date, only its age: `postedOn` is
    "Posted 19 Days Ago".

    `_iso` cannot read any of the four shapes it uses, so every posting from
    all 1,489 Workday boards -- Barclays, HSBC, Nvidia, Adobe, Salesforce --
    arrived undated and scored as though it had no recency at all.
    """
    from datetime import date, timedelta

    from jobradar.adapters.platforms import _workday_posted

    today = date(2026, 8, 25)
    assert _workday_posted("Posted Today", today) == "2026-08-25"
    assert _workday_posted("Posted Yesterday", today) == "2026-08-24"
    assert _workday_posted("Posted 19 Days Ago", today) == "2026-08-06"
    # "30+" is read as exactly thirty: the oldest date the phrase permits, so
    # a role open for a year cannot collect recency points by being vague.
    assert _workday_posted("Posted 30+ Days Ago", today) == "2026-07-26"
    assert _workday_posted("") is None
    assert _workday_posted("Posted recently") is None
    # An ISO date, when a tenant does send one, still wins outright.
    assert _workday_posted("2026-01-31", today) == "2026-01-31"

    jobs = adapters.parse(json.loads(_read("workday_jobs.json")),
                          _src("workday", "Nvidia",
                               "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/"
                               "nvidia/NVIDIAExternalCareerSite/jobs"))
    assert len(jobs) == 4, [j.title for j in jobs]
    assert all(j.posted_at for j in jobs), [(j.title, j.posted_at) for j in jobs]
    ages = sorted((date.today() - date.fromisoformat(j.posted_at)).days
                  for j in jobs)
    assert ages[0] == 0 and ages[1] == 1, ages
    assert ages[2] == 19 and ages[3] == 30, ages
    assert timedelta(days=ages[3]).days == 30


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from run_all import main
    raise SystemExit(main())
