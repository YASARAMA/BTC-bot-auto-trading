"""Which code produced a result. Saved with every experiment so it can be reproduced."""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from app import __version__

_REPO = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=_REPO, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    if out.returncode != 0 or not commit:
        return None
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=_REPO, capture_output=True,
            text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        dirty = ""
    return commit + ("-dirty" if dirty else "")


def software_version() -> str:
    commit = git_commit()
    return f"{__version__}+{commit}" if commit else __version__
