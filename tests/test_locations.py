"""Where a posting is, which decides whether the user can take the job at all.

Kept separate from test_core.py so a country rule can be added without
touching the file every adapter's tests live in.
"""

from jobradar.screen import _countries_in, _country_of


def test_a_state_code_that_is_also_a_country_lets_the_city_decide():
    """Twenty US state codes are also ISO country codes, and the state check
    ran first, so it answered before the city was ever consulted.

    "Berlin, DE" came back as Delaware and "Toronto, CA" as California. Both
    were then filed as US roles: a country the user may need a visa for, and
    one they may have excluded outright."""
    assert _countries_in("Berlin, DE") == {"DE"}
    assert _countries_in("Toronto, CA") == {"CA"}
    assert _countries_in("Munich, DE") == {"DE"}
    assert _countries_in("Vancouver, CA") == {"CA"}


def test_the_same_codes_still_read_as_states_when_the_city_is_american():
    """The fix must not overshoot. San Francisco, CA is not Canada."""
    for loc in ("San Francisco, CA", "Sacramento, CA", "Atlanta, GA",
                "Chicago, IL", "Indianapolis, IN", "Los Angeles, CA"):
        assert _countries_in(loc) == {"US"}, loc


def test_an_ambiguous_code_needs_the_city_to_corroborate_it():
    """Letting the city win outright was the wrong fix, and would have
    reintroduced the bug it replaced.

    "Birmingham, AL" and "Reading, PA" both hit the UK city list, so deferring
    to the city would file two American roles as British, which is exactly
    what once mislabelled 59 of 296 US roles. The code only counts as a
    country when the city names that same country: Berlin corroborates DE,
    Birmingham does not corroborate Albania."""
    for loc in ("Birmingham, AL", "Reading, PA", "Bath, ME", "Manchester, NH"):
        assert _country_of(loc) == "US", loc

    # And with nothing on the city list either way, it stays a state.
    assert _country_of("Wilmington, DE") == "US"
    assert _country_of("Dover, DE") == "US"


def test_unambiguous_state_codes_are_unaffected():
    for loc in ("Austin, TX", "Seattle, WA", "Portland, OR", "Boston, MA"):
        assert _countries_in(loc) == {"US"}, loc


def test_countries_are_recognised_under_the_names_employers_write():
    """A German employer posting in German writes Deutschland, and the
    accented spelling of Sao Paulo is the usual one."""
    assert _countries_in("Deutschland") == {"DE"}
    assert _countries_in("São Paulo, BR") == {"BR"}
    assert _countries_in("Sao Paulo") == {"BR"}
    assert _countries_in("Rio de Janeiro") == {"BR"}


def test_a_location_naming_nothing_stays_unknown():
    """Unknown has to stay distinguishable from a country. "Remote" and
    "EMEA" name no country, and guessing one would put a role in front of
    someone who cannot take it."""
    for loc in ("Remote", "EMEA", "Worldwide", "Anywhere", ""):
        assert _countries_in(loc) == set(), loc


def test_a_platforms_own_remote_flag_beats_prose_in_the_advert():
    """Pinpoint, Breezy and Teamtailor all state the arrangement in a field,
    and the description scan ran first, so it answered first.

    An advert mentioning an on-site gym, on-site parking, or occasional
    on-site visits filed a role the ATS had marked remote as office based.
    Prose still decides when the platform set no flag, and an explicitly
    hybrid posting still wins over both."""
    from jobradar.models import Job
    from jobradar.screen import work_mode

    def job(**kw):
        return Job(company="Acme", title="Engineer", url="https://x/1",
                   platform="pinpoint", **kw)

    flagged = job(location="Remote", remote=True,
                  description="We have an on-site gym at the London office.")
    assert work_mode(flagged) == "remote"

    # No flag set, so the advert is all there is.
    assert work_mode(job(location="London",
                         description="This role is office based.")) == "office"

    # And an explicitly hybrid posting is hybrid whatever the flag says.
    assert work_mode(job(location="London", remote=True,
                         description="Hybrid, 3 days a week in the office.")) == "hybrid"


def test_an_aggregator_never_outranks_the_employers_own_board():
    """Dedupe picks a winner by directness first and description length
    second. Reed returns full advert text, so at the default score a Reed
    repost could take the row from the employer's own posting and hand the
    reader a reed.co.uk redirect instead of the real apply page."""
    from jobradar.screen import directness

    assert directness("reed") < directness("greenhouse")
    assert directness("linkedin") < directness("reed")
    assert directness("greenhouse") == directness("workday") == 2


# ------------------------------------------------- aggregators and dedupe
def _row(company, title, platform, desc="x" * 120, location="London"):
    from jobradar.models import Job
    return Job(company=company, title=title, url=f"https://{platform}/1",
               platform=platform, location=location, description=desc)


def test_the_employers_own_board_wins_over_an_aggregator_reposting_it():
    """Adding aggregators means the same role arrives twice, and the copy the
    reader wants is the one that links to the real apply page rather than a
    redirect."""
    from jobradar.screen import dedupe

    out = dedupe([_row("Monzo", "Engineering Manager", "reed"),
                  _row("Monzo", "Engineering Manager", "greenhouse")])
    assert len(out) == 1
    assert out[0].platform == "greenhouse"


def test_a_legal_form_or_descriptor_does_not_hide_the_duplicate():
    """An aggregator prints whatever the employer registered as. Grouping on
    the raw name left "Monzo Bank Ltd" and "Monzo" in separate groups, so both
    rows showed and directness never got to decide."""
    from jobradar.screen import dedupe

    for agg_name, direct_name, plat in (
            ("Monzo Bank Ltd", "Monzo", "greenhouse"),
            ("BT Group plc", "BT", "workday"),
            ("Wise Payments Limited", "Wise", "smartrecruiters")):
        out = dedupe([_row(agg_name, "Risk Manager", "reed"),
                      _row(direct_name, "Risk Manager", plat)])
        assert len(out) == 1, agg_name
        assert out[0].platform == plat
        assert any("also listed on" in f for f in out[0].flags)


def test_a_loose_name_match_never_collapses_two_real_employers():
    """The fuzzy half only ever folds an aggregator into a direct board. Sky
    and Skyscanner both run their own boards and both roles are real, so a
    prefix match must not be allowed to delete one of them."""
    from jobradar.screen import dedupe

    out = dedupe([_row("Sky", "Data Engineer", "greenhouse"),
                  _row("Skyscanner", "Data Engineer", "workday")])
    assert len(out) == 2


def test_two_different_roles_at_one_employer_stay_two_roles():
    """Titles still have to match exactly. Platform and Payments are two
    vacancies, and merging them would lose one."""
    from jobradar.screen import dedupe

    out = dedupe([_row("Monzo", "Engineering Manager, Platform", "greenhouse"),
                  _row("Monzo Bank Ltd", "Engineering Manager, Payments", "reed")])
    assert len(out) == 2


def test_an_agency_repost_is_left_alone_because_it_names_the_agency():
    """A role posted by Robert Walters carries Robert Walters as the employer,
    so no name rule can tie it to Monzo. Reed's postedByDirectEmployer filter
    is what handles that, at fetch time, not dedupe."""
    from jobradar.screen import dedupe

    out = dedupe([_row("Monzo", "Engineering Manager", "greenhouse"),
                  _row("Robert Walters", "Engineering Manager", "reed")])
    assert len(out) == 2


def test_a_postings_own_location_beats_the_boards_country_tag():
    """A board is tagged with where its vacancies usually are. That is a fair
    default and a bad override.

    Homebase's board is tagged UK because Homebase is a UK retailer, and the
    Ashby adapter sets no country, so `j.country or source.country` filed a
    genuine Toronto vacancy as UK. 23 roles were stored UK while their own
    location said US."""
    from jobradar.screen import _countries_in

    def resolve(location, tag):
        here = _countries_in(location or "")
        if tag in ("multiple", "multi", "unknown"):
            tag = ""
        if len(here) == 1:
            return here.pop()
        if here:
            return tag if tag in here else ""
        return tag

    assert resolve("Toronto", "UK") == "CA"
    assert resolve("Berlin", "UK") == "DE"
    # Only when the posting names nowhere does the board's tag get used.
    assert resolve("", "UK") == "UK"
    assert resolve("Remote", "UK") == "UK"
    # "multiple" is not a country and must never be stored as one.
    assert resolve("", "multiple") == ""
    # Several countries named: the tag is usable only if it is one of them.
    assert resolve("London / New York", "UK") == "UK"
    assert resolve("Berlin / Paris", "UK") == ""


# ------------------------------------------------------------ avature links
def test_avature_reads_pipelines_and_query_string_ids_not_just_slugs():
    """Reading only `/JobDetail/<slug>` reported whole boards as empty.

    A pipeline is Avature's evergreen requisition and it is a real vacancy:
    HSBC's board carries 96 `/PipelineDetail/` links and zero `/JobDetail/`
    ones. Separately, Macquarie and Ross Stores put the id in the query
    (`/JobDetail?jobId=23921`) with no slug at all."""
    from jobradar.adapters.platforms import _AV_LINK

    for html, want in (
            ('<a href="https://x/careers/JobDetail?jobId=23921">Automation Engineer</a>',
             "https://x/careers/JobDetail?jobId=23921"),
            ('<a href="https://x/external/PipelineDetail/GSC-Manager">GSC: Manager</a>',
             "https://x/external/PipelineDetail/GSC-Manager"),
            ('<a href="https://x/careers/JobDetail/Some-Role">Some Role</a>',
             "https://x/careers/JobDetail/Some-Role")):
        got = _AV_LINK.findall(html)
        assert got and got[0][0] == want, html


def test_an_avature_share_link_is_still_not_a_job():
    """Every card carries share links whose QUERY STRING holds the job's own
    URL, so admitting a question mark anywhere before the match reports three
    rows per job. The query form is allowed only as `?jobId=<digits>`."""
    from jobradar.adapters.platforms import _AV_LINK

    tweet = ('<a href="https://twitter.com/share?text=Some Role '
             'https://x/careers/JobDetail/Some-Role">Share</a>')
    assert _AV_LINK.findall(tweet) == []


def test_the_same_avature_job_linked_twice_is_one_row():
    """A card links the record on its title and again on a View Job button.
    Keeping whichever came first would be luck; the labelled one is real."""
    from jobradar.adapters.platforms import parse_avature
    from jobradar.models import Source

    page = ('<a href="https://x/external/PipelineDetail/GSC-Manager">'
            'GSC: Senior Control Manager (Cyber)</a>'
            '<a href="https://x/external/PipelineDetail/GSC-Manager">View Job</a>')
    jobs = list(parse_avature(page, Source(company="HSBC", platform="avature",
                                           url="https://x/external/SearchJobs/")))
    assert len(jobs) == 1
    assert jobs[0].title == "GSC: Senior Control Manager (Cyber)"
