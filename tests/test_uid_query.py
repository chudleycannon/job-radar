"""Every posting from a query-string board collapsed into one role.

`Job.uid` threw the whole query string away before hashing. An employer
running Greenhouse behind their own careers page puts the posting id there,
so

    https://stripe.com/jobs/search?gh_jid=111
    https://stripe.com/jobs/search?gh_jid=999

were the same id, and only whichever arrived first was ever stored. Measured
on the published UK shard: 2,383 of 41,038 rows disappeared into another
role. Stripe published 89 and one survived. Bayada published 165 and one
survived.

It is invisible from outside, which is what makes it the worst kind. There is
no duplicate row to notice and no error. There is one Stripe job, and one
Stripe job is exactly what a company with one vacancy looks like. `uid` is
also the seen-set key, so "what is new" was broken for those employers on
every ordinary scan too, not only in the seed.

A deny-list, not an allow-list. An unrecognised parameter is KEPT, so the
worst an unknown tracking token can do is re-alert a role once, which is
noisy and visible. An allow-list would drop an unrecognised identifying
parameter and merge two jobs, which is silent, and silent is the bug.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Job  # noqa: E402


def _uid(url, company="Acme", title="Engineer", location="London"):
    return Job(company=company, title=title, url=url, platform="greenhouse",
               location=location).uid


def test_the_identifying_parameter_survives_whatever_it_is_called():
    for key in ("gh_jid", "id", "jobid", "jobId", "posting", "req"):
        assert _uid(f"https://x.test/j?{key}=1") != _uid(f"https://x.test/j?{key}=2"), key


def test_an_unknown_parameter_is_kept_rather_than_guessed_away():
    """The deny-list's whole point. Keeping an unknown one costs at worst a
    re-alert; dropping one merges two jobs and nobody ever finds out."""
    assert _uid("https://x.test/j?wibble=1") != _uid("https://x.test/j?wibble=2")


def test_tracking_parameters_do_not_make_the_same_job_look_new():
    for key in ("utm_source", "utm_campaign", "gh_src", "ref", "fbclid",
                "gclid", "mc_cid", "trk"):
        assert _uid(f"https://x.test/j/1?{key}=a") == \
               _uid(f"https://x.test/j/1?{key}=b"), key


def test_a_tracked_link_and_a_clean_one_are_the_same_job():
    assert _uid("https://x.test/j/1?gh_jid=5&utm_source=linkedin") == \
           _uid("https://x.test/j/1?gh_jid=5")


def test_reordered_parameters_do_not_re_alert_a_whole_board():
    assert _uid("https://x.test/j?a=1&b=2") == _uid("https://x.test/j?b=2&a=1")


def test_a_fragment_is_still_not_part_of_the_identity():
    assert _uid("https://x.test/j/1#apply") == _uid("https://x.test/j/1")


def test_a_posting_with_no_url_still_falls_back_to_company_and_title():
    """Unchanged, and load-bearing: it is what stops a board that rewrites
    its URLs re-alerting everything it publishes."""
    a = Job(company="Acme", title="EM", url="", platform="x", location="London")
    b = Job(company="Acme", title="EM", url="", platform="x", location="London")
    c = Job(company="Acme", title="SRE", url="", platform="x", location="London")
    assert a.uid == b.uid and a.uid != c.uid


def test_the_real_shard_no_longer_merges_distinct_postings():
    """Against the published data rather than an invented URL.

    Skipped when the build is not on this machine, because the shard set is
    242MB and is not in the repository.
    """
    import collections
    from jobradar import seed
    build = Path(__file__).resolve().parent.parent / "seed-build"
    if not (build / "index.json").exists():
        return
    urls = collections.defaultdict(set)
    for j in seed.load(build, ["UK"]):
        urls[j.uid].add(j.url)
    merged = {u: s for u, s in urls.items() if len(s) > 1}
    assert not merged, f"{len(merged)} ids still cover two different postings"
