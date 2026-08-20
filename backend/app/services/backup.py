"""Database backup and restore service.

Backup (creating a recovery artifact):
    create_backup() copies the SQLite database (and WAL/SHM when present) to
    <project>/data/backups/ with a timestamped name and VALIDATES the copy
    before reporting success.

Restore (replacing the active database after validation):
    restore_backup() validates the requested backup, creates a safety backup
    of the current database, then atomically replaces the active database.
    Restore is SQLite-only and path-policy restricted to BACKUP_DIR.

The backup destination is resolved against PROJECT_ROOT so that it is
independent of the process working directory (PR-A hardening). Validation,
safety backup, atomic replacement, and post-restore verification are PR-B.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings
from app.core.logging import get_logger

logger = get_logger("backup")

# Backup destination and database file path are resolved against the project
# root so backups work regardless of the process working directory.
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

# Construct the actual database file path from the SQLAlchemy URL
_DATABASE_URL = settings.database_url
if _DATABASE_URL.startswith("sqlite:///"):
    _DB_PATH = Path(_DATABASE_URL[len("sqlite:///"):])
    if not _DB_PATH.is_absolute():
        _DB_PATH = (PROJECT_ROOT / _DB_PATH).resolve()
    else:
        _DB_PATH = _DB_PATH.resolve()
else:
    _DB_PATH = Path.cwd() / "database.db"

_SQLITE_MAGIC = b"SQLite format 3\x00"


class BackupValidationError(RuntimeError):
    """A backup file is not a usable SQLite database."""


class BackupRestoreError(RuntimeError):
    """A restore operation failed; see the message for recovery details."""


def validate_backup(path: Path) -> None:
    """Raise BackupValidationError if ``path`` is not a usable backup.

    Verifies: file exists, non-empty, carries the SQLite file header, can be
    opened read-only, passes PRAGMA integrity_check, and contains every table
    the application schema defines.
    """
    path = Path(path)
    if not path.exists():
        raise BackupValidationError(f"backup not found: {path}")
    if not path.is_file():
        raise BackupValidationError(f"backup is not a file: {path}")
    if path.stat().st_size == 0:
        raise BackupValidationError(f"backup is empty: {path}")
    with path.open("rb") as fh:
        if fh.read(16) != _SQLITE_MAGIC:
            raise BackupValidationError(f"not a SQLite database: {path}")

    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupValidationError(f"cannot open backup: {exc}") from exc
    try:
        with con:
            row = con.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            detail = row[0] if row else "no result"
            raise BackupValidationError(f"integrity_check failed: {detail}")
    except sqlite3.Error as exc:
        raise BackupValidationError(f"integrity check error: {exc}") from exc
    finally:
        con.close()

    from app import models  # noqa: F401  (register models)
    from app.db.base import Base

    expected = set(Base.metadata.tables)
    try:
        con2 = sqlite3.connect(uri, uri=True)
        try:
            tables = {
                r[0]
                for r in con2.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            con2.close()
    except sqlite3.Error as exc:
        raise BackupValidationError(f"cannot read backup schema: {exc}") from exc
    missing = sorted(expected - tables)
    if missing:
        raise BackupValidationError(
            f"backup missing required tables: {', '.join(missing)}"
        )


def create_backup(target: Path | None = None) -> Path:
    """Copy the database (and WAL/SHM if present) to BACKUP_DIR.

    ``target`` defaults to the configured live database. The created backup
    is validated before it is reported; an invalid copy is deleted and an
    error is raised. Returns the path of the main backup file.
    """
    db_path = target if target is not None else _DB_PATH
    db_path = Path(db_path)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()

    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = BACKUP_DIR / f"lead_outreach_{stamp}.db"

    copied: list[Path] = []
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db_path) + suffix)
            if not src.exists():
                continue
            dst = Path(str(backup_path) + suffix)
            shutil.copy2(src, dst)
            copied.append(dst)
        validate_backup(backup_path)
    except Exception:
        for dst in copied:
            dst.unlink(missing_ok=True)
        raise

    logger.info("backup created path=%s", backup_path)
    return backup_path


def _rollback_to_safety(safety_path: Path, db_path: Path) -> str:
    """Best-effort rollback of the live database from the safety backup."""
    try:
        os.replace(safety_path, db_path)
        for suffix in ("-wal", "-shm"):
            src = Path(str(safety_path) + suffix)
            dst = Path(str(db_path) + suffix)
            if src.exists():
                os.replace(src, dst)
            else:
                dst.unlink(missing_ok=True)
        return "original database restored from the pre-restore safety backup"
    except Exception as rollback_exc:  # pragma: no cover - hard failure path
        return (
            f"ROLLBACK FAILED: {rollback_exc}; manual recovery file: {safety_path}"
        )


def restore_backup(backup_path: Path, target: Path | None = None) -> Path:
    """Validate and restore a backup over the active database.

    Flow: validate request -> resolve backup -> verify integrity -> safety
    backup of the current database -> stage + re-validate -> atomic replace
    -> reopen + verify -> report. On failure the original database is left
    usable (rollback to the safety backup when replacement already happened).

    Returns the path of the pre-restore safety backup.
    """
    from app.db.session import engine as app_engine

    source = Path(backup_path)
    if not source.is_absolute():
        source = (PROJECT_ROOT / source).resolve()
    source = source.resolve()

    # Path policy: backups must live inside the approved backup directory.
    if not source.is_relative_to(BACKUP_DIR):
        raise BackupValidationError(
            f"backup must reside inside the approved backup directory "
            f"{BACKUP_DIR}: {source}"
        )
    if not source.exists():
        raise FileNotFoundError(f"backup not found: {source}")
    if not source.is_file():
        raise BackupValidationError(f"backup is not a file: {source}")
    validate_backup(source)

    db_path = Path(target) if target is not None else _DB_PATH
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()

    # Safety backup of the current database before any destructive step.
    safety_path: Path | None = None
    if db_path.exists():
        safety_path = create_backup(target=db_path)

    # Stage the restore inside the target directory (same volume -> atomic).
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".restore_stage_", dir=str(db_path.parent))
    )
    replaced = False
    try:
        staged_main = stage_dir / db_path.name
        shutil.copy2(source, staged_main)
        for suffix in ("-wal", "-shm"):
            src = Path(str(source) + suffix)
            if src.exists():
                shutil.copy2(src, Path(str(staged_main) + suffix))
        validate_backup(staged_main)

        app_engine.dispose()  # release pooled connections to the live DB

        os.replace(staged_main, db_path)
        replaced = True
        for suffix in ("-wal", "-shm"):
            staged_suffix = Path(str(staged_main) + suffix)
            live_suffix = Path(str(db_path) + suffix)
            if staged_suffix.exists():
                os.replace(staged_suffix, live_suffix)
            else:
                live_suffix.unlink(missing_ok=True)
    except Exception as exc:
        note = "no replacement occurred; original database untouched"
        if replaced and safety_path is not None:
            note = _rollback_to_safety(safety_path, db_path)
        raise BackupRestoreError(f"restore failed: {exc}; {note}") from exc
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    # Reopen + verify the restored database.
    try:
        validate_backup(db_path)
    except BackupValidationError as exc:
        note = "no recovery action taken"
        if safety_path is not None:
            note = _rollback_to_safety(safety_path, db_path)
        raise BackupRestoreError(
            f"restore verification failed after replacement; {note}: {exc}"
        ) from exc

    logger.info("restore complete path=%s safety_backup=%s", db_path, safety_path)
    return safety_path