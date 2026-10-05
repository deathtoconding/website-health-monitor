from __future__ import annotations

import os
import sqlite3
import stat

import pytest

from app.backup import backup_database, main


def test_backup_is_atomically_created_and_integrity_checked(tmp_path) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('preserved')")
    target = backup_database(source, tmp_path / "backups" / "snapshot.db")
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "preserved"
    assert not (target.parent / f".{target.name}.tmp").exists()
    if os.name == "posix":
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_backup_validates_source_destination_and_existing_target(tmp_path) -> None:
    source = tmp_path / "source.db"
    source.touch()
    target = tmp_path / "backup.db"
    target.touch()
    with pytest.raises(FileExistsError):
        backup_database(source, target)
    with pytest.raises(ValueError):
        backup_database(source, source)
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.db", tmp_path / "new.db")


def test_backup_rejects_corruption_and_cli_reports_result(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "invalid.db"
    source.write_text("not a sqlite database", encoding="utf-8")
    target = tmp_path / "corrupt-copy.db"
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(source, target)
    assert not target.exists()
    assert not (tmp_path / ".corrupt-copy.db.tmp").exists()

    valid_source = tmp_path / "valid.db"
    with sqlite3.connect(valid_source) as connection:
        connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("WHM_DATABASE_PATH", str(valid_source))
    cli_destination = tmp_path / "cli-backup.db"
    assert main([str(cli_destination)]) == 0
    assert "Backup created and verified" in capsys.readouterr().out
    assert main([str(cli_destination)]) == 1
    assert "Backup already exists" in capsys.readouterr().err
