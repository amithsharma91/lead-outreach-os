# Lead Outreach OS — backend startup wrapper (deterministic, documented path)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/start.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -AppEnv production
#
# The application is started with the project's own virtual environment and
# the existing FastAPI/uvicorn architecture. No new process manager, Docker,
# or scheduler is introduced.

param(
    [ValidateSet("development", "test", "production")]
    [string]$AppEnv = ""
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at '$python'. See docs/DEPLOYMENT.md (Python environment)."
}

if (-not $env:APP_ENV) {
    $env:APP_ENV = if ($AppEnv) { $AppEnv } else { "development" }
}

Write-Host "Starting Lead Outreach OS backend (APP_ENV=$env:APP_ENV) at http://127.0.0.1:8000"
Write-Host "Safety state: messaging_provider=$env:MESSAGING_PROVIDER daily_send_limit=$env:DAILY_SEND_LIMIT (set by config; defaults: none / 0)"
# PR-C: production refuses to boot without an explicit CORS_ORIGINS and
# refuses auth-without-token. Set $env:CORS_ORIGINS / $env:API_AUTH_TOKEN
# before starting in production (see backend/.env.example).

Push-Location $backend
try {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw "uvicorn exited with code $LASTEXITCODE" }
}
finally {
    Pop-Location
}