"""What we have seen before.

The tool is only useful if it can say what is *new*. That needs state that
survives between runs, and in the GitHub Actions path it has to survive on a
fresh runner, so it is committed back to the repo rather than kept in the
Actions cache. The cache is evicted after 7 days of no use, which would
silently re-alert every role as new.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from .models import Job

DEFAULT_DIR = Path("state")


# ---------------------------------------------------------------- durability
#
# The atomic writers live in this module rather than in a utility one because
# this is the package's dependency-free persistence module: the seen-set, the
# source list, the config writer, the dashboard and the .docx writer can all
# import it without dragging in requests or yaml.
#
# The shape is write-to-temp, flush, fsync, rename. A plain `write_text`
# truncates the target and then streams into it, so a process killed part way
# through leaves a file that exists, is the wrong length, and carries a fresh
# mtime. Nothing downstream can tell that apart from a good file. After a
# rename the target holds either the old bytes or the new bytes and never half
# of either.
#
# `os.replace`, not `os.rename`: on Windows `os.rename` raises FileExistsError
# when the target already exists, and CI runs Windows.


def _tmp_for(path: Path) -> Path:
    """The temp name to write through.

    Same directory, because a rename is only atomic within one filesystem and
    a temp dir is routinely a different one. Dot-prefixed so a half-written
    file is not picked up by a glob or a directory listing, and stamped with
    the pid so two processes writing the same path do not collide on the temp
    name and corrupt each other.
    """
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def _replace(tmp: Path, path: Path) -> Path:
    os.replace(tmp, path)
    # The rename is visible to every other process immediately, but the
    # directory entry itself is only durable once the directory is synced. A
    # killed process does not need this; a power cut or a hard reset does.
    # Windows cannot open a directory as a file, so failing here is normal and
    # is not an error.
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return path
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
    return path


def _cleanup(tmp: Path) -> None:
    try:
        tmp.unlink()
    except OSError:
        pass


def atomic_write_text(path, text: str) -> Path:
    """Replace `path` with `text`, or leave it exactly as it was."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        # No `newline=` argument, so the bytes match what `write_text` used to
        # produce on every platform. This changes when a file becomes visible,
        # not what is in it.
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        return _replace(tmp, path)
    except BaseException:
        # BaseException, so a KeyboardInterrupt lands here too: the whole
        # point is that an interrupted write leaves nothing behind.
        _cleanup(tmp)
        raise


def atomic_write_bytes(path, data: bytes) -> Path:
    """The same, for a file that is not text: a .docx is a zip."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return _replace(tmp, path)
    except BaseException:
        _cleanup(tmp)
        raise


class StateUnreadable(SystemExit):
    """`seen.json` is there but could not be read.

    A SystemExit, so a command stops with the message and no traceback, in the
    same way the config writer refuses to write a file that would not parse.
    It is still a named class, so a caller that wants to handle it can.
    """


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
            if not isinstance(d, dict):
                raise ValueError("the top level is not an object")
        except (ValueError, OSError) as e:
            # Do NOT fall back to an empty state. Falling back is what made
            # the original bug silent in both directions: a truncated file
            # read as "nothing has ever been seen", every role was reported as
            # new, and the next save overwrote the only copy with that empty
            # answer. On the Actions path that empty file is then committed.
            #
            # Refusing is chosen over keeping a `.bak`. A `.bak` would be
            # written by the same process that is about to be killed, so it is
            # one more file to catch half-written, it doubles a file that
            # reaches tens of thousands of entries, and restoring it still
            # needs a person to decide. Refusing touches nothing, which leaves
            # the copy that can be recovered from git exactly where it is, and
            # unlike the old behaviour it says out loud what is wrong.
            #
            # json.JSONDecodeError is a ValueError, and so is the isinstance
            # check above.
            raise StateUnreadable(
                f"{self.path} exists but could not be read ({e}).\n"
                f"Refusing to continue. Treating it as empty would report "
                f"every role as new and then overwrite it with that.\n"
                f"The file has not been touched. Restore the last good copy "
                f"(git checkout -- {self.path}), or delete it to start the "
                f"seen-set again from scratch.")
        self.seen = d.get("seen", {})
        self.source_counts = d.get("source_counts", {})
        self.runs = d.get("runs", 0)

    def save(self) -> None:
        # Atomic: a scan killed at the Actions job timeout used to leave
        # truncated JSON here, which `load` then read as an empty seen-set.
        atomic_write_text(self.path, json.dumps({
            "runs": self.runs,
            "updated": date.today().isoformat(),
            "seen": self.seen,
            "source_counts": self.source_counts,
        }, indent=1, sort_keys=True))

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
