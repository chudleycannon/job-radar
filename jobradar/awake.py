"""Keep the machine awake for as long as something is running.

A full scan takes the better part of an hour, and the way people actually use
this is to set it up, watch the dashboard fill, apply to something and shut
the laptop. The scan then dies at whatever percent it had reached, and the
next run starts from nothing. Asking somebody to babysit a progress bar for
fifty minutes is not a plan.

So the scan holds a power assertion while it runs and drops it the moment it
finishes. Nothing is configured and nothing is left behind: on every platform
here the assertion lives and dies with a process.

**What this cannot do, said plainly because the difference matters.** It stops
the machine falling asleep on its own while nobody touches it. It does not
stop a laptop sleeping when the lid is closed. On macOS that needs
`pmset disablesleep`, which is undocumented, system wide and needs a password,
and this tool is not going to ask for one. Closing the lid will still end the
scan, and the message the user sees says so rather than implying otherwise.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys


def _macos(pid: int):
    """`caffeinate -i -w PID`, which exits by itself when PID does.

    Tied to the process rather than given a duration, so a scan that finishes
    early releases immediately and a scan that overruns is still covered. If
    this process is killed with SIGKILL the child notices its target has gone
    and exits on its own, which is why `-w` is worth the extra argument over
    `-t`.
    """
    exe = shutil.which("caffeinate") or "/usr/bin/caffeinate"
    if not os.path.exists(exe):
        return None
    try:
        return subprocess.Popen([exe, "-i", "-w", str(pid)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


# Windows: ES_CONTINUOUS keeps the state until it is cleared, ES_SYSTEM_REQUIRED
# is the "do not idle to sleep" bit. Deliberately no ES_DISPLAY_REQUIRED: the
# screen may sleep, the machine may not. Nobody wants a monitor burning for an
# hour because a job scan is running.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _windows() -> bool:
    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
    except Exception:
        return False


def _windows_release() -> None:
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)  # type: ignore[attr-defined]
    except Exception:
        pass


def _linux(reason: str):
    """systemd-inhibit, which every current desktop distribution has.

    `--what=idle` only: a scan is not a reason to block the user shutting the
    machine down or closing the lid, and asking for those would be rude in a
    way `idle` is not.
    """
    exe = shutil.which("systemd-inhibit")
    if not exe:
        return None
    try:
        return subprocess.Popen(
            [exe, "--what=idle", "--who=job-radar", f"--why={reason}",
             "--mode=block", "sleep", "86400"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


class keep_awake:
    """Hold a power assertion for the life of the block.

    Never raises and never blocks the work: a machine with no way to hold an
    assertion runs the scan anyway and says so once. `held` reports whether it
    actually got one, so the caller can tell the user the truth rather than a
    reassuring guess.
    """

    def __init__(self, reason: str = "job-radar is scanning", enabled: bool = True):
        self.reason = reason
        self.enabled = enabled
        self.held = False
        self._proc = None
        self._windows = False

    def __enter__(self) -> "keep_awake":
        if not self.enabled:
            return self
        if sys.platform == "darwin":
            self._proc = _macos(os.getpid())
            self.held = self._proc is not None
        elif sys.platform.startswith("win"):
            self._windows = _windows()
            self.held = self._windows
        else:
            self._proc = _linux(self.reason)
            self.held = self._proc is not None
        return self

    def __exit__(self, *exc) -> bool:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None
        if self._windows:
            _windows_release()
            self._windows = False
        self.held = False
        return False


def describe(held: bool) -> str:
    """One line for the user, and an honest one.

    A message saying "your machine will stay awake" would be a lie the first
    time somebody shuts the lid, so this says what is actually true.
    """
    if held:
        return ("Your machine will not fall asleep on its own while this runs. "
                "Closing the lid will still stop it.")
    return ("This machine has no way to stay awake on request, so a scan will "
            "stop if it sleeps.")
