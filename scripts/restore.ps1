# Lead Outreach OS — database restore wrapper
#
# Restores the active SQLite database from a validated backup. The restore
# logic lives in the application service (app/services/backup.py:
# restore_backup); this script only passes the backup path through.
#
# The service: validates the backup, creates a safety backup of the current
# database, atomically replaces the database, and verifies the result. The
# backup must reside inside <project>/data/backups/.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/restore.ps1 -BackupPath "<path>"
#
# Exit code 0 on success; non-zero on failure. For consistency, stop the
# application before restoring while the application is running.

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at '$python'. See docs/DEPLOYMENT.md (Python environment)."
}

Push-Location $backend
try {
    $result = & $python -c "import sys; from app.services.backup import restore_backup; s = restore_backup(sys.argv[1]); print(s if s else 'NO_SAFETY_BACKUP')" $BackupPath
    if ($LASTEXITCODE -ne 0) { throw "Restore failed (exit $LASTEXITCODE)." }
    Write-Host "Restore completed successfully."
    if ($result -and $result -ne "NO_SAFETY_BACKUP") {
        Write-Host "Pre-restore safety backup: $result"
    }
}
catch {
    Write-Error "RESTORE FAILED: $($_.Exception.Message) — the original database was left usable where possible."
    exit 1
}
finally {
    Pop-Location
}