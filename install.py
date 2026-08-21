"""One command from a fresh clone to a working install and a first scan.

    git clone https://github.com/maccydee/job-radar
    cd job-radar && python3 install.py

The three-step version asked people to create a virtual environment, know that
`pip install -e .` means "install this directory", and only then discover
whether their Python was new enough. Each step is obvious to someone who has
done it before and a place to stop for everyone else.

This file imports nothing outside the standard library, on purpose: it runs
before anything is installed, so it cannot depend on the thing it installs. It
also has to work on a Python it has not checked yet, which is why the version
check is the first thing it does and why the syntax here stays plain.

It is not clever. It creates a virtual environment beside the checkout,
installs the package into it, and hands over to `job-radar setup`. Everything
it does can be done by hand, and it prints each step so you can see which one
you would repeat.
"""

import os
import subprocess
import sys
from pathlib import Path

MIN = (3, 10)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def say(msg=""):
    print(msg, flush=True)


def venv_python(venv: Path) -> Path:
    # Windows puts executables in Scripts, everyone else in bin.
    return venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python3")


def venv_bin(venv: Path, name: str) -> Path:
    d = venv / ("Scripts" if os.name == "nt" else "bin")
    return d / (f"{name}.exe" if os.name == "nt" else name)


def run(cmd, **kw):
    """Run a step, and on failure say what to try rather than dumping a stack."""
    try:
        return subprocess.run(cmd, check=True, **kw)
    except FileNotFoundError:
        say(f"\n{cmd[0]} is not available on this machine.")
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        say(f"\nThat step failed (exit {e.returncode}):")
        say("  " + " ".join(str(c) for c in cmd))
        raise SystemExit(e.returncode)


def main() -> int:
    if sys.version_info < MIN:
        say(f"job-radar needs Python {MIN[0]}.{MIN[1]} or newer. "
            f"This is {sys.version.split()[0]} at {sys.executable}.")
        say("Install a newer Python and run this again with it.")
        return 1

    say(f"job-radar install")
    say(f"  python {sys.version.split()[0]}")
    say(f"  into   {VENV}")
    say()

    if not venv_python(VENV).exists():
        say("Creating the virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV)])
    else:
        say("Virtual environment already there, reusing it.")

    py = venv_python(VENV)
    say("Installing job-radar and its two dependencies...")
    # -q keeps the output to what went wrong. --upgrade so re-running this
    # after a `git pull` actually picks the new version up, which is the whole
    # point of running it again.
    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "-q", "-e", str(ROOT)])

    exe = venv_bin(VENV, "job-radar")
    say()
    say("Installed. From here on:")
    say(f"  {exe} <command>")
    say(f"  or activate the environment first: "
        f"{'.venv\\\\Scripts\\\\activate' if os.name == 'nt' else 'source .venv/bin/activate'}")
    say()

    # Hand straight over. Setup asks for a CV, asks what you are looking for,
    # and runs the first scan itself, so this is the last instruction anyone
    # needs to read.
    say("Starting setup. It asks for your CV first and will not finish "
        "without one.")
    say("-" * 60)
    try:
        return subprocess.run([str(exe), "setup"]).returncode
    except KeyboardInterrupt:
        say("\nStopped. The install is done; run this when you are ready:")
        say(f"  {exe} setup")
        return 0
    except FileNotFoundError:
        # Installed but the entry point is missing, which means the install
        # did not really succeed. Say so rather than reporting success.
        say(f"\nInstalled, but {exe} is not there. Try:")
        say(f"  {py} -m jobradar.cli setup")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
