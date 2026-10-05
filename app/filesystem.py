"""Restrictive local filesystem defaults for sensitive SQLite state."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_private_parent(path: Path) -> None:
    """Create each missing parent directory as owner-only on POSIX."""
    parent = path.parent
    if os.name != "posix":
        parent.mkdir(parents=True, exist_ok=True)
        return

    missing: list[Path] = []
    current = parent
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise


def ensure_private_file(path: Path) -> None:
    """Create a missing POSIX file as owner-only before another library opens it."""
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        return
    else:
        os.close(descriptor)
