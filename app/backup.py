"""Create an online, integrity-checked SQLite backup."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from app.config import Settings
from app.filesystem import ensure_private_file, ensure_private_parent


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Use SQLite's backup API so a live WAL-mode database is copied consistently."""
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if source_path == destination_path:
        raise ValueError("Backup destination must differ from the source database")
    ensure_private_parent(destination_path)
    if destination_path.exists():
        raise FileExistsError(f"Backup already exists: {destination_path}")

    temporary_path = destination_path.with_name(f".{destination_path.name}.tmp").resolve()
    if source_path == temporary_path:
        raise ValueError("Backup temporary path must differ from the source database")
    if temporary_path.exists():
        temporary_path.unlink()
    ensure_private_file(temporary_path)
    source_connection = sqlite3.connect(source_path, timeout=10.0)
    destination_connection: sqlite3.Connection | None = None
    try:
        destination_connection = sqlite3.connect(temporary_path, timeout=10.0)
        source_connection.backup(destination_connection)
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity}")
        destination_connection.commit()
        if os.name == "posix":
            temporary_path.chmod(0o600)
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
        source_connection.close()
        temporary_path.unlink(missing_ok=True)
        raise
    else:
        destination_connection.close()
        source_connection.close()
    temporary_path.replace(destination_path)
    return destination_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup for WHM.")
    parser.add_argument("destination", help="New backup database path (must not already exist)")
    arguments = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        backup_path = backup_database(settings.database_path, arguments.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup created and verified: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
