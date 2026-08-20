"""PR-A focused tests: backup destination is absolute and CWD-independent."""

from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.services.backup import create_backup

BACKUP_DIR = PROJECT_ROOT / "data" / "backups"


def _cleanup(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()


def test_backup_path_is_absolute():
    path = create_backup()
    try:
        assert path.is_absolute()
        assert path.parent == BACKUP_DIR
        assert path.name.startswith("lead_outreach_")
        assert path.name.endswith(".db")
        assert path.exists()
    finally:
        _cleanup(path)


def test_backup_path_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = create_backup()
    try:
        assert path.is_absolute()
        assert path.parent == BACKUP_DIR
        # No stray backup directory may be created under the CWD.
        assert not (tmp_path / "data" / "backups").exists()
    finally:
        _cleanup(path)


def test_backup_dir_created_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = create_backup()
    try:
        assert BACKUP_DIR.is_dir()
    finally:
        _cleanup(path)