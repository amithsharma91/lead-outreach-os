# Production Configuration — Lead Outreach OS

Date: 2026-08-20 (PR-C completion: Configuration & API Security)

## 1. Supported environments

| APP_ENV | Purpose | Log level |
|---|---|---|
| `development` | Local development (default when APP_ENV unset) | DEBUG |
| `test` | Automated test suite (`tests/conftest.py` sets it) | INFO |
| `production` | Operational deployment | INFO |

Any other `APP_ENV` value is **rejected** at startup with a clear error
(`ValueError` listing the supported values). This fail-fast guard prevents
silent misconfiguration.

## 2. Configuration precedence (highest wins)

1. Environment variables (including `backend/.env` via the built-in loader)
2. `config/settings.json` (optional user override of the example file)
3. Built-in defaults matching `config/settings.example.json`

Secrets (OmniRoute API key, notification target, etc.) are **environment-only**
and are never read from settings files. Logging redacts sensitive keys.

## 3. Safe production defaults

These are the defaults and MUST remain in force for the current system:

- `messaging_provider = "none"` — real outbound messaging is disabled.
- `daily_send_limit = 0` — zero sends.
- `require_human_approval = true` — every message requires human approval.
- Outreach window `21:00–23:00` in `Asia/Kolkata` (timezone `Asia/Kolkata`).
- `scheduler_enabled = true` — the tick runs but performs zero sends under
  the above defaults.
- CORS defaults to `http://localhost:5173` for development; production must
  set `CORS_ORIGINS` explicitly.

The system is currently **incapable of real outbound messaging**.

## 4. Required environment variables

See `backend/.env.example` (source of truth: `backend/app/core/config.py`).

- **Safe defaults (must not be weakened without authorization):**
  `APP_ENV`, `OUTREACH_TIMEZONE`, `OUTREACH_START_TIME`, `OUTREACH_END_TIME`,
  `DAILY_SEND_LIMIT`, `REQUIRE_HUMAN_APPROVAL`, `SCHEDULER_ENABLED`,
  `SCHEDULER_INTERVAL_SECONDS`, `AI_PROVIDER`, `MESSAGING_PROVIDER`,
  `LOG_MESSAGE_CONTENT`.
- **Required in production when the feature is used (placeholders only):**
  `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL`,
  `NOTIFICATION_PROVIDER`, `NOTIFICATION_TARGET`.
- `DATABASE_URL` — prefer an **absolute** path in production; relative sqlite
  paths resolve against the process working directory.

Do not set variables that are not listed — the loader only reads known keys.

## 5. Starting the application in production

```
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -AppEnv production
```

The wrapper starts uvicorn (`app.main:app`) from the project virtual
environment; startup uses the additive, idempotent `ensure_schema()` and
never destroys data.

## 6. Settings that MUST remain disabled

- `MESSAGING_PROVIDER` must remain `none` (the `whatsapp_openwa` entry is a
  dormant stub that cannot send).
- `DAILY_SEND_LIMIT` must remain `0` unless a separately authorized milestone
  defines and approves a provider-specific number.
- `REQUIRE_HUMAN_APPROVAL` must remain `true`.
- No provider activation, credential population, or bulk-send functionality
  is part of this phase.

## 7. Backup location behavior

Backups are written to `<project>/data/backups/` with timestamped names
(`lead_outreach_<stamp>.db`, plus `-wal`/`-shm` when present). The directory
is resolved against the project root and is **independent of the process
working directory**.

**BACKUP** creates and validates a recovery artifact:
`scripts/backup.ps1` (or `create_backup()`) copies the database and verifies
the copy is a usable SQLite database (header, openable, `integrity_check` ok,
all required tables present) before reporting success.

**RESTORE** replaces the active database after validation:
`scripts/restore.ps1 -BackupPath <path>` (or `restore_backup()`) validates the
backup, creates a **safety backup** of the current database, atomically
replaces the database (and WAL/SHM), and verifies the result. The backup must
reside inside `data/backups/`; the application must be stopped while
restoring. A failed restore leaves the original database usable.

Fresh/empty/missing SQLite databases are bootstrapped automatically at startup
(`ensure_schema()`); non-SQLite backends are rejected with a clear error.

## 8. Intentionally NOT included

- Authentication is disabled by default; bearer authentication is available
  as an opt-in production control and requires `API_AUTH_TOKEN` when enabled.
- Rate limiting is disabled by default; the available limiter is per-process
  and per-client-IP.
- Deployment process managers / Docker / system services
- Provider activation
- Observability/monitoring beyond logs + `/api/health`
- Frontend serving from the backend

These remain explicit production-hosting considerations; see
`docs/DEPLOYMENT.md` and `docs/PR_C_COMPLETION_REPORT.md`.
