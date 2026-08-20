# Deployment Notes — Lead Outreach OS

Date: 2026-08-20 (PR-C completion). This document does **not** claim the system is fully
production-ready; remaining gaps are listed at the end.

## Prerequisites

- Windows with PowerShell (pwsh) or PowerShell 5.1+ (`-ExecutionPolicy Bypass`).
- Python 3.14 (the project venv was created with 3.14.7).
- Node.js / npm for the frontend build.

## Python environment

```
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt   # if requirements file present
```

Verified packages in the current venv: FastAPI, SQLAlchemy, uvicorn 0.52.3,
pytest 9.1.1. The backend has zero outbound-network dependencies in app code.

## Frontend build

```
cd frontend
npm install
npx tsc --noEmit        # type check
npm run build           # outputs frontend/dist/
```

The SPA is currently served separately (Vite dev proxy forwards `/api` to
`http://127.0.0.1:8000`). Backend serving of the built frontend is **not yet
implemented** (a known gap — PR-E).

## Backend startup

```
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -AppEnv production
```

- Serves the FastAPI app at `http://127.0.0.1:8000` via uvicorn.
- Startup runs `ensure_schema()` (PR-B): on a fresh/empty/missing database it
  creates the full schema from the model definitions; on an existing database
  it runs additive, idempotent migrations. It **never** drops data.

## Database location

Default: `<project>/data/lead_outreach.db` (SQLite, WAL journal mode,
foreign keys enabled). Configurable via `DATABASE_URL` (prefer an absolute
path). Backends other than SQLite are **rejected** at startup.

## Environment configuration

Copy `backend/.env.example` to `backend/.env` and set only the values you
intend to override. See `docs/PRODUCTION_CONFIGURATION.md` for precedence,
supported variables, and safe defaults.

## Fresh database initialization

There is nothing to do — the application bootstraps automatically on first
start. A missing database file, an empty file, or a file without tables is
initialized with the complete schema. Initialization is idempotent: starting
the application repeatedly never duplicates schema objects or destroys data.

## Backup and restore

**BACKUP** = creating a recovery artifact (copy + validation).
**RESTORE** = replacing the active database after validation (a distinct,
safe operation).

### Backup

```
powershell -ExecutionPolicy Bypass -File scripts/backup.ps1
```

Copies the database (and WAL/SHM when present) to
`<project>/data/backups/` with a timestamped name and **validates** the copy
(SQLite header, openable, `PRAGMA integrity_check` = ok, all required tables
present) before reporting success. Exit code 0 on success, non-zero on
failure. Invalid copies are deleted.

### Restore

```
powershell -ExecutionPolicy Bypass -File scripts/restore.ps1 -BackupPath "<path to backup inside data\backups>"
```

The backup must reside inside `<project>/data/backups/`. The application's
restore service:

1. Resolves and validates the requested backup.
2. Creates a **safety backup** of the current database (also in `data/backups/`).
3. Stages and re-validates the backup in a temporary directory.
4. Atomically replaces the active database (and WAL/SHM) using the OS rename.
5. Reopens the database and verifies it (`integrity_check` + schema).

Exit code 0 on success (the pre-restore safety backup path is printed), non-zero
on failure. A failed restore leaves the original database usable (rolled back
to the safety backup if replacement already occurred).

**Operational requirement:** stop the application before restoring. SQLite
files are replaced via an OS rename, and an actively running process holds the
database file open; on Windows this raises an access-denied error. Run the
restore while the backend is stopped.

## Current safety configuration

- `messaging_provider = none`
- `daily_send_limit = 0`
- `require_human_approval = true`
- Real outbound messaging is **disabled**. The configured `none` provider and
  zero daily limit prevent outbound sends; provider activation is outside PR-C.

## Health endpoint

`GET http://127.0.0.1:8000/api/health` returns:

```json
{ "status": "ok", "database": "ok", "app_env": "...", "ai_provider": "...", "messaging_provider": "..." }
```

Scheduler/queue/approval/reply observability is not yet covered by health
checks (PR-D).

## SQLite limitations

- SQLite supports a single writer; concurrent writers can cause `SQLITE_BUSY`.
- WAL/SHM files are part of the database; backups copy them and restores
  restore them. The application manages this automatically.
- Do not copy or delete database files manually while the application runs.
- Restoring over a live database is not supported (see above).
- Non-SQLite backends (e.g. PostgreSQL) are **not supported** and are rejected
  at startup with a clear error. No schema is ever created for them.

## Known limitations / remaining gaps

1. **Authentication and rate limiting are opt-in** — enable and configure
   them explicitly for a non-local deployment. Rate limiting is in-process and
   not a multi-instance/shared-limit solution.
2. **CORS requires explicit production configuration** — production refuses
   to inherit the development default; configure `CORS_ORIGINS` deliberately.
3. **Backend does not serve the frontend** — split deployment (PR-E).
4. **Observability is logs + `/api/health` only** (PR-D).
5. **No process manager / Docker / system service** — uvicorn runs in the
   foreground via the start script.
6. **`data/lead_outreach.db` is reset by any pytest run** (test-only, by design).

## PR-C and later milestones

- **PR-C (API/Security):** complete — opt-in bearer authentication, in-process
  rate limiting, environment-driven CORS, and sanitized unexpected-error
  responses. See `docs/PR_C_COMPLETION_REPORT.md`.
- **PR-D (Health/Observability):** scheduler/queue/approval/reply health and
  summary observability.
- **PR-E (Frontend/Deployment):** backend static serving of the frontend,
  environment-based API base URL, UX-state verification.
- **PR-F:** final production-readiness audit.
