"""Polite HTTP with throttle detection.

The reason this file exists rather than a bare `requests.get` loop: several of
these APIs fail in ways that look exactly like success.

  * Ashby and SmartRecruiters return HTTP 200 with an empty array for a board
    token that does not exist, and for one that is being rate-limited. Status
    code tells you nothing; job count does.
  * Greenhouse returns 403 if you attach a body to a GET, which is easy to do
    accidentally when one code path handles both GET and POST platforms.
  * Workday returns 406 rather than 404 for a tenant that does not exist,
    because of wildcard DNS. A non-404 is not evidence a tenant is real.

So a source that used to return jobs and now returns none is reported as a
suspected throttle rather than "no jobs", because the difference matters and
the API will not tell you.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import requests

from .models import Source


@dataclass
class Result:
    source: Source
    payload: Any = None
    error: str | None = None
    status: int | None = None
    elapsed: float = 0.0
    throttled: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 30.0))
            return
        except ValueError:
            pass
    time.sleep(min(2 ** attempt, 20) + random.uniform(0, 0.75))


def fetch_one(
    src: Source,
    *,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    session: requests.Session | None = None,
) -> Result:
    s = session or requests.Session()
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    t0 = time.time()
    last = "unknown error"
    status = None

    for attempt in range(retries + 1):
        try:
            if src.method.upper() == "POST":
                r = s.post(src.url, json=src.body or {}, headers=headers, timeout=timeout)
            else:
                # Never attach a body to a GET. Greenhouse 403s on it.
                r = s.get(src.url, headers=headers, timeout=timeout)
            status = r.status_code

            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = f"HTTP {r.status_code}"
                if attempt < retries:
                    _sleep_backoff(attempt, r.headers.get("Retry-After"))
                    continue
                return Result(src, error=last, status=status,
                              elapsed=time.time() - t0, throttled=r.status_code == 429)

            if r.status_code >= 400:
                return Result(src, error=f"HTTP {r.status_code}", status=status,
                              elapsed=time.time() - t0)

            ctype = (r.headers.get("Content-Type") or "").lower()
            if "json" in ctype or r.text.lstrip()[:1] in "[{":
                return Result(src, payload=r.json(), status=status, elapsed=time.time() - t0)
            return Result(src, payload=r.text, status=status, elapsed=time.time() - t0)

        except requests.RequestException as e:
            last = type(e).__name__
            if attempt < retries:
                _sleep_backoff(attempt, None)
                continue

    return Result(src, error=last, status=status, elapsed=time.time() - t0)


def fetch_all(
    sources: Iterable[Source],
    *,
    concurrency: int = 4,
    timeout: int = 20,
    retries: int = 2,
    user_agent: str = "job-radar/0.1",
    on_result: Callable[[Result], None] | None = None,
) -> list[Result]:
    sources = list(sources)
    out: list[Result] = []
    # One session per worker; requests.Session is not thread-safe to share.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {}
        for i, src in enumerate(sources):
            # Stagger the opening burst so we do not hit 200 hosts in the
            # same 50ms and look like something worth blocking.
            delay = (i % max(1, concurrency)) * 0.05
            futs[ex.submit(_delayed_fetch, src, delay, timeout, retries, user_agent)] = src
        for f in as_completed(futs):
            res = f.result()
            out.append(res)
            if on_result:
                on_result(res)
    return out


def _delayed_fetch(src, delay, timeout, retries, ua) -> Result:
    if delay:
        time.sleep(delay)
    return fetch_one(src, timeout=timeout, retries=retries, user_agent=ua,
                     session=requests.Session())


def detect_throttling(
    results: list[Result],
    counts: dict[str, int],
    history: dict[str, int],
) -> list[str]:
    """Sources that previously returned jobs and now return none.

    Silent throttling is the failure mode that makes this whole tool lie: an
    empty array reads as "nothing matched" when it actually means "you were
    blocked". Anything here should be treated as unknown, not as zero.
    """
    suspects = []
    for res in results:
        key = res.source.key
        was = history.get(key, 0)
        now = counts.get(key, 0)
        if was >= 3 and now == 0:
            suspects.append(res.source.company)
        elif res.throttled:
            suspects.append(res.source.company)
    return sorted(set(suspects))
