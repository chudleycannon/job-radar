"""Core data types. Every adapter normalises into `Job`."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Salary:
    """A pay figure attached to a posting.

    `confirmed` is the important field. Most postings state no salary at all,
    so `confirmed=False` means "the employer did not publish one", not
    "we failed to parse it". Those roles are shown to the user, labelled.
    Only a confirmed figure can disqualify a role.
    """

    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: str = "year"  # year | day | hour
    raw: str | None = None
    confirmed: bool = False

    @property
    def top(self) -> float | None:
        """The figure to compare against a floor.

        Uses the top of the band: a posting advertised at 100k-150k clears a
        120k floor, because that is a role you would still talk to them about.
        """
        return self.max if self.max is not None else self.min

    def annualised(self, working_days: int = 220, hours_per_day: int = 8) -> float | None:
        """Day and hour rates converted to an annual figure so a single floor works.

        Without this a 600/day contract reads as 600 and gets binned by any
        sane annual floor.
        """
        t = self.top
        if t is None:
            return None
        if self.period == "day":
            return t * working_days
        if self.period == "hour":
            return t * working_days * hours_per_day
        return t

    SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}

    def label(self) -> str:
        """What to show the reader.

        Built from the numbers rather than from whatever string the platform
        supplied, because those strings are often just a heading: Greenhouse
        returns things like "Annual Salary:" and "Local Pay Range" in the same
        field as the figures, and showing that tells the reader nothing.
        """
        if not self.confirmed:
            return "unconfirmed salary"

        sym = self.SYMBOLS.get((self.currency or "").upper(), "")
        cur = "" if sym else (self.currency + " " if self.currency else "")
        per = {"day": "/day", "hour": "/hr"}.get(self.period, "")

        def fmt(v: float) -> str:
            if self.period == "year" and v >= 10_000:
                return f"{sym}{cur}{v / 1000:,.0f}k"
            return f"{sym}{cur}{v:,.0f}"

        if self.min is not None and self.max is not None and self.max != self.min:
            return f"{fmt(self.min)} - {fmt(self.max)}{per}"
        v = self.top
        if v is not None:
            return f"{fmt(v)}{per}"
        return self.raw or "salary stated"


@dataclass
class Job:
    company: str
    title: str
    url: str
    platform: str
    location: str = ""
    remote: bool | None = None
    department: str | None = None
    posted_at: str | None = None
    description: str = ""
    salary: Salary = field(default_factory=Salary)
    sector: str | None = None
    country: str | None = None
    city: str = ""
    work_mode: str = "unstated"   # remote | hybrid | office | unstated
    source_id: str = ""

    # populated downstream
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Stable id for the seen-set.

        Keyed on the apply URL where possible; ATS URLs carry a stable posting
        id. Falls back to company+title+location so a board that rewrites its
        URLs does not re-alert everything.
        """
        basis = self.url or f"{self.company}|{self.title}|{self.location}"
        basis = re.sub(r"[?#].*$", "", basis.strip().lower())
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        d["salary_label"] = self.salary.label()
        return d


@dataclass
class Source:
    """One employer job board."""

    company: str
    url: str
    platform: str
    sector: str | None = None
    country: str | None = None
    domain: str | None = None  # used to verify the board is really this company
    method: str = "GET"
    body: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        # Only the fragment is dropped. The query string is load-bearing on
        # some platforms: the LinkedIn guest endpoint distinguishes searches
        # purely by its parameters, so stripping them collapses six distinct
        # sources into one.
        return re.sub(r"#.*$", "", self.url)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "company": self.company,
            "url": self.url,
            "platform": self.platform,
        }
        for k in ("sector", "country", "domain"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.method != "GET":
            d["method"] = self.method
        if self.body:
            d["body"] = self.body
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Source":
        return cls(
            company=d["company"],
            url=d["url"],
            platform=d.get("platform") or "",
            sector=d.get("sector"),
            country=d.get("country"),
            domain=d.get("domain"),
            method=d.get("method", "GET"),
            body=d.get("body"),
        )
