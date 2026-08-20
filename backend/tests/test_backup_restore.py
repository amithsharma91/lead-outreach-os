"""PR-B: backup validation and restore system tests (isolated temporary databases)."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db.session import ensure_schema
from app.models import Lead
from app.services.backup import (
    BACKUP_DIR,
    BackupRestoreError,
    BackupValidationError,
    create_backup,
    restore_backup,
    validate_backup,
)


def _eng(db: Path):
    return create_engine(f"sqlite:///{db.as_posix()}")


def _seed(db: Path, lead_id="LEAD-B1", name="Beta"):
    eng = _eng(db)
    ensure_schema(eng)
    with Session(eng) as s:
        s.add(Lead(lead_id=lead_id, business_name=name))
        s.commit()
    eng.dispose()
    return db


def _lead_ids(db: Path):
    eng = _eng(db)
    try:
        with Session(eng) as s:
            return [row.lead_id for row in s.scalars(select(Lead)).all()]
    finally:
        eng.dispose()


def _count_backup_files() -> set:
    if not BACKUP_DIR.exists():
        return set()
    return {p.name for p in BACKUP_DIR.iterdir()}


def _cleanup(paths):
    for p in paths:
        if p is None:
            continue
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(p) + suffix)
            if f.exists():
                f.unlink()


# ---------------------------------------------------------------------------
# Backup validation
# ---------------------------------------------------------------------------


class TestBackupValidation:
    def test_backup_is_valid_sqlite_with_tables(self, tmp_path):
        db = _seed(tmp_path / "src.db")
        backup = create_backup(target=db)
        try:
            validate_backup(backup)  # must not raise
            con = sqlite3.connect(backup)
            try:
                tables = {
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                assert {"leads", "outreach_messages", "campaigns"} <= tables
            finally:
                con.close()
        finally:
            _cleanup([backup])

    def test_backup_integrity_check_succeeds(self, tmp_path):
        db = _seed(tmp_path / "src2.db")
        backup = create_backup(target=db)
        try:
            con = sqlite3.connect(backup)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        finally:
            _cleanup([backup])

    def test_backup_missing_source_rejected(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            create_backup(target=tmp_path / "nope.db")

    def test_backup_rejects_non_sqlite_source(self, tmp_path):
        bad = BACKUP_DIR / "notsqlite.db"
        bad.write_bytes(b"this is definitely not a sqlite database file at all")
        try:
            # Record existing backup files before operation
            before = set(BACKUP_DIR.glob("lead_outreach_*.db"))
            with pytest.raises(BackupValidationError):
                create_backup(target=bad)
            # After operation, backup file set should be unchanged (invalid copy cleaned)
            after = set(BACKUP_DIR.glob("lead_outreach_*.db"))
            assert after == before
        finally:
            _cleanup([bad])

    def test_backup_path_absolute_and_cwd_independent(self, tmp_path, monkeypatch):
        db = _seed(tmp_path / "cwdsrc.db")
        monkeypatch.chdir(tmp_path)
        backup = create_backup(target=db)
        try:
            assert backup.is_absolute()
            assert backup.parent == BACKUP_DIR
        finally:
            _cleanup([backup])


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


class TestRestore:
    def test_restore_valid_backup(self, tmp_path):
        db = _seed(tmp_path / "live.db")
        backup = create_backup(target=db)
        eng = _eng(db)
        with eng.begin() as c:
            c.execute(text("DELETE FROM leads"))
        eng.dispose()
        assert _lead_ids(db) == []
        safety = restore_backup(backup, target=db)
        try:
            assert _lead_ids(db) == ["LEAD-B1"]
        finally:
            _cleanup([backup, safety])

    def test_restored_data_matches_backup(self, tmp_path):
        db = _seed(tmp_path / "m1.db")
        backup = create_backup(target=db)
        eng = _eng(db)
        with eng.begin() as c:
            c.execute(text("DELETE FROM leads"))
        eng.dispose()
        safety = restore_backup(backup, target=db)
        try:
            assert _lead_ids(db) == ["LEAD-B1"]
            con = sqlite3.connect(db)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        finally:
            _cleanup([backup, safety])

    def test_restore_corrupt_backup_rejected(self, tmp_path):
        db = _seed(tmp_path / "live2.db")
        corrupt = BACKUP_DIR / "corrupt.db"
        corrupt.write_bytes(b"garbage-not-a-db")
        try:
            with pytest.raises(BackupValidationError):
                restore_backup(corrupt, target=db)
            assert _lead_ids(db) == ["LEAD-B1"]  # original untouched
        finally:
            _cleanup([corrupt])

    def test_restore_non_sqlite_backup_rejected(self, tmp_path):
        db = _seed(tmp_path / "live3.db")
        textfile = BACKUP_DIR / "readme.txt"
        textfile.write_text("hello world")
        try:
            with pytest.raises(BackupValidationError):
                restore_backup(textfile, target=db)
            assert _lead_ids(db) == ["LEAD-B1"]
        finally:
            _cleanup([textfile])

    def test_restore_missing_backup_rejected(self, tmp_path):
        db = _seed(tmp_path / "live4.db")
        with pytest.raises(FileNotFoundError):
            restore_backup(BACKUP_DIR / "missing_backup.db", target=db)

    def test_restore_outside_approved_dir_rejected(self, tmp_path):
        db = _seed(tmp_path / "live5.db")
        outside = tmp_path / "outside.db"
        _seed(outside, lead_id="OUTSIDE")
        with pytest.raises(BackupValidationError, match="approved backup directory"):
            restore_backup(outside, target=db)
        assert _lead_ids(db) == ["LEAD-B1"]

    def test_restore_creates_safety_backup(self, tmp_path):
        db = _seed(tmp_path / "live6.db")
        backup = create_backup(target=db)
        before = _count_backup_files()
        safety = restore_backup(backup, target=db)
        try:
            after = _count_backup_files()
            assert len(after - before) >= 1
            assert safety is not None and Path(safety).exists()
        finally:
            _cleanup([backup, safety])

    def test_restore_repeatable(self, tmp_path):
        db = _seed(tmp_path / "rep.db")
        backup = create_backup(target=db)
        safety1 = restore_backup(backup, target=db)
        safety2 = restore_backup(backup, target=db)
        try:
            assert _lead_ids(db) == ["LEAD-B1"]
        finally:
            _cleanup([backup, safety1, safety2])

    def test_restore_wal_shm_handled(self, tmp_path):
        src = tmp_path / "walsrc.db"
        eng = _eng(src)
        ensure_schema(eng)
        eng.dispose()
        con = sqlite3.connect(src)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA wal_autocheckpoint=0")
            con.execute(
                "INSERT INTO leads (lead_id, business_name, website_status, "
                "website_quality, social_presence, social_quality, lead_priority, "
                "recommended_campaign, recommended_template, intelligence_status, "
                "qualification_status, outreach_status, do_not_contact, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "WAL-LEAD",
                    "Walcorp",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "NOT_ANALYZED",
                    "PENDING",
                    "NOT_CONTACTED",
                    0,
                    "2026-08-20 00:00:00",
                    "2026-08-20 00:00:00",
                ),
            )
            con.commit()
            assert (tmp_path / "walsrc.db-wal").exists()
            backup = create_backup(target=src)  # copies main + wal + shm
        finally:
            con.close()
        try:
            tgt = tmp_path / "waltgt.db"
            safety = restore_backup(backup, target=tgt)
            try:
                assert _lead_ids(tgt) == ["WAL-LEAD"]
            finally:
                _cleanup([tgt, safety])
        finally:
            _cleanup([backup, src])

    def test_restore_does_not_touch_unrelated_files(self, tmp_path):
        db = _seed(tmp_path / "u.db")
        sentinel = tmp_path / "sentinel.txt"
        sentinel.write_text("keep-me")
        backup = create_backup(target=db)
        safety = restore_backup(backup, target=db)
        try:
            assert sentinel.read_text() == "keep-me"
        finally:
            _cleanup([backup, safety])


class TestRestoreErrorType:
    def test_backup_restore_error_type_exists(self):
        # The service exposes a distinct error type for restore failures.
        assert issubclass(BackupRestoreError, Exception)