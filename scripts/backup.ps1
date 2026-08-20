# Lead Outreach OS — database backup wrapper
#
# Invokes the existing, verified backup service (app/services/backup.py),
# which copies the SQLite database (and WAL/SHM) to <project>/data/backups/
# with a timestamped name and VALIDATES the copy (SQLite header, openable,
# integrity_check, required tables) before reporting success.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/backup.ps1
#
# Exit code 0 on success; non-zero on failure.

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at '$python'. See docs/DEPLOYMENT.md (Python environment)."
}

Push-Location $backend
try {
    $backup = & $python -c "from app.services.backup import create_backup; print(create_backup())"
    if ($LASTEXITCODE -ne 0) { throw "Backup failed (exit $LASTEXITCODE)." }
    Write-Host "Backup created and validated: $backup"
}
finally {
    Pop-Location
}