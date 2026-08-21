"""What we have seen before.

The tool is only useful if it can say what is *new*. That needs state that
survives between runs, and in the GitHub Actions path it has to survive on a
fresh runner, so it is committed back to the repo rather than kept in the
Actions cache. The cache is evicted after 7 days of no use, which would
silently re-alert every role as new.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .models import Job

DEFAULT_DIR = Path("state")


class State:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT_DIR / "seen.json")
        self.seen: dict[str, dict[str, Any]] = {}
        self.source_counts: dict[str, int] = {}
        self.runs: int = 0
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.seen = d.get("seen", {})
        self.source_counts = d.get("source_counts", {})
        self.runs = d.get("runs", 0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "runs": self.runs,
            "updated": date.today().isoformat(),
            "seen": self.seen,
            "source_counts": self.source_counts,
        }, indent=1, sort_keys=True), encoding="utf-8")

    # ---- diffing ----

    def split(self, jobs: list[Job]) -> tuple[list[Job], list[Job]]:
        """(new, already_seen). First ever run reports nothing as new, because
        'here are 900 new roles' on day one is not a useful alert.
        """
        first_run = self.runs == 0
        new, old = [], []
        for j in jobs:
            if j.uid in self.seen:
                old.append(j)
            else:
                (old if first_run else new).append(j)
        return new, old

    def record(self, jobs: list[Job], source_counts: dict[str, int]) -> None:
        today = date.today().isoformat()
        for j in jobs:
            entry = self.seen.get(j.uid)
            if entry:
                entry["last_seen"] = today
            else:
                self.seen[j.uid] = {
                    "first_seen": today,
                    "last_seen": today,
                    "company": j.company,
                    "title": j.title,
                }
        # Only overwrite a source's count when we actually got a response, so
        # a failed fetch does not erase the history throttle detection needs.
        for k, v in source_counts.items():
            self.source_counts[k] = v
        self.runs += 1

    def prune(self, keep_days: int = 180) -> int:
        """Drop entries not seen for a while so the file does not grow forever."""
        cutoff = date.today().toordinal() - keep_days
        drop = []
        for uid, e in self.seen.items():
            try:
                if date.fromisoformat(e.get("last_seen", "")).toordinal() < cutoff:
                    drop.append(uid)
            except ValueError:
                continue
        for uid in drop:
            del self.seen[uid]
        return len(drop)
