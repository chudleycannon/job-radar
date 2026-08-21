"""Fetch the full posting for roles whose source only returned a headline.

LinkedIn's search endpoint returns a title, a company and a location. Nothing
else. That is a quarter to a half of a typical board, and for every one of
those roles the tool was silently doing nothing: dealbreakers had no text to
run against, the salary floor had no figure to compare, `rank` skipped them
because there was nothing to judge fit on, and `generate` refused outright.
They were leads pretending to be matches.

LinkedIn publishes each posting separately, one job id at a time, and that
response carries the whole description. So the missing text is one request per
role rather than something the design has to live without.

This is a read. It spends no tokens. It is also 125 requests to somebody
else's servers on a normal run, so it goes one at a time with a pause, skips
anything it already has, and gives up on a role quietly rather than retrying
it into the ground.

The robots.txt position is the same one the README already discloses for the
search endpoint: LinkedIn disallows it, this reads it anyway, and that is a
deliberate choice a user should know they are making.
"""

from __future__ import annotations

import html as _h
import re
import time

import requests

from . import salary as sal_mod, store

# One posting, by id. The same guest surface the search endpoint lives on.
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# The description sits in one block. Everything else on the page is chrome.
# Capture what is INSIDE the block. Matching from the class name onwards left
# `description__text description__text--rich">` sitting at the front of every
# description, which is noise in the token budget and in anything reading it.
_BLOCK = re.compile(r'description__text[^>]*>(.*?)</section>', re.S)
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>|</p>|</li>", re.I)

# Trailing digits of a LinkedIn job URL are its id.
_JOB_ID = re.compile(r"(\d{6,})(?:[/?#]|$)")


def job_id(url: str) -> str:
    m = _JOB_ID.search((url or "").split("?")[0])
    return m.group(1) if m else ""


def _text(page: str) -> str:
    m = _BLOCK.search(page)
    if not m:
        return ""
    # Keep the line breaks: a description that arrives as one wall of text
    # loses the bullet structure the dealbreaker patterns read best against.
    body = _BR.sub("\n", m.group(1))
    body = _h.unescape(_TAG.sub(" ", body))
    lines = [" ".join(x.split()) for x in body.split("\n")]
    return "\n".join(x for x in lines if x).strip()


def fetch(url: str, session=None, timeout: int = 20) -> str:
    jid = job_id(url)
    if not jid:
        return ""
    get = (session or requests).get
    try:
        r = get(JOB_URL.format(job_id=jid), headers={"User-Agent": UA},
                timeout=timeout)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    return _text(r.text)


def candidates(con, limit: int = 0) -> list:
    """Roles on the board that have a URL we can expand and no description."""
    store._ensure_columns(con)
    q = ("SELECT r.uid, r.url, r.platform, r.salary_confirmed FROM roles r "
         "LEFT JOIN role_state s ON s.uid = r.uid "
         "WHERE COALESCE(s.status,'new') NOT IN "
         "('rejected','withdrawn','skipped','closed') "
         f"AND {store.LIVE_SQL} "
         "AND r.platform = 'linkedin' "
         "AND LENGTH(TRIM(COALESCE(r.description,''))) < 200")
    rows = con.execute(q).fetchall()
    return rows[:limit] if limit else rows


def run(con, cfg=None, rows=None, pause: float = 1.0, on_each=None) -> tuple[int, int]:
    """Fill in descriptions. Returns (fetched, attempted).

    Re-parses pay while it is there: a posting that states a salary in its body
    was being carried as "unconfirmed" purely because the body had never been
    read, which meant the floor could not act on it either.
    """
    rows = candidates(con) if rows is None else rows
    session = requests.Session()
    got = 0
    for i, r in enumerate(rows, 1):
        text = fetch(r["url"], session=session)
        if text and len(text) >= 200:
            got += 1
            fields = {"description": text[:20000]}
            s = sal_mod.parse_text(text, cfg.salary_currency if cfg else None)
            if s.confirmed and not r["salary_confirmed"]:
                fields.update({
                    "salary_min": s.min, "salary_max": s.max,
                    "salary_currency": s.currency, "salary_period": s.period,
                    "salary_confirmed": 1, "salary_label": s.label(),
                })
            con.execute(
                "UPDATE roles SET " + ",".join(f"{k}=?" for k in fields)
                + " WHERE uid=?", (*fields.values(), r["uid"]))
        if on_each:
            on_each(i, len(rows), got)
        # Other people's servers, and a lot of requests in a row.
        if i < len(rows):
            time.sleep(pause)
    return got, len(rows)
