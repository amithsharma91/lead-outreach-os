# PHASE 3 — FULL REPOSITORY AUDIT

**Date**: 2026-08-21
**Scope**: Complete repository inspection before any Phase 3 changes.
**Rule**: Do NOT modify application code during the initial audit.

---

## 1. Frontend framework

- **React**: ^19.0.0 (src/App.tsx, src/main.tsx, src/pages/*, src/components/*)
- **Vite**: ^5.4.1 (vite.config.ts — dev server at port 5173, proxy /api -> 127.0.0.1:8000)
- **React Router**: ^6.28.0 (src/main.tsx, src/App.tsx)
- **Tailwind CSS**: ^3.4.19 (tailwind.config.js, postcss.config.cjs, src/index.css)
- **PostCSS**: ^8.5.26 (postcss.config.cjs — plugins: tailwindcss, autoprefixer)
- **Autoprefixer**: ^10.5.4 (postcss.config.cjs)
- **Source files**: src/index.css, src/main.tsx, src/App.tsx, src/pages/*, src/components/*, src/auth/, src/lib/, src/hooks/, src/services/, src/types/
- **Build output**: frontend/dist/ (index.html, assets/*.css, assets/*.js)
- **Current issue**: postcss.config.cjs was missing the tailwindcss plugin fix (resolved; now includes `require('tailwindcss')`)

---

## 2. Backend framework

- **FastAPI**: ^0.115.0 (app/main.py)
- **Uvicorn**: ^0.30 (app.main:app entrypoint, scripts/start.ps1)
- **SQLAlchemy**: ^2.0.36 (app/db/, app/models/ — ORM with declarative base)
- **Pydantic**: ^2.10 (schemas/ — data validation)
- **Python**: 3.14.0 (from .venv/Scripts/python.exe)
- **Event loop**: ProactorEventLoop (Windows default, noted in prior investigation)

---

## 3. Database

- **SQLite** (default, configured via DATABASE_URL env var)
- **DATABASE_URL**: `sqlite:///C:/tmp/lead-outreach-os/data/lead_outreach.db` (backend/.env production) or `sqlite:///./data/lead_outreach.db` (backend/.env.example development)
- **Migration layer**: `ensure_schema()` in `app/db/session.py` — additive, idempotent (ALTER TABLE ADD COLUMN, CREATE UNIQUE INDEX IF NOT EXISTS). Refuses to run in production; test-only `init_db()` (drop_all + create_all) guarded by `APP_ENV=test`.
- **Schema files**: data/lead_outreach.db (binary), data/backups/, data/exports/, data/imports/
- **Backup files**: data/backups/ (not yet automated)

---

## 4. ORM/database layer

- **SQLAlchemy ORM** with `DeclarativeBase` (app/db/base.py)
- **TimestampMixin**: created_at column (app/db/base.py)
- **Models** (app/models/): lead.py, outreach_message.py, campaign.py, qualified_lead.py, reply.py, activity_log.py
- **Inspection**: `ensure_schema()` uses SQLAlchemy `inspector` to check existing columns and apply additive migrations only.
- **Session management**: `SessionLocal` (sessionmaker), `get_db()` generator (dependency injection)

---

## 5. Queue implementation

- **OutreachQueue** (app/services/queue.py) — state-machine-driven idempotent outbound queue
- **Workflow**: APPROVED -> QUEUED -> SENDING -> SENT/DELIVERED (success) / FAILED -> RETRY_PENDING -> SENDING (retry)
- **Idempotency**: Deterministic SHA256 key (lead + campaign + sequence + version + template_type); unique index on idempotency_key prevents duplicates
- **Safety invariants**:
  - enqueue() only from APPROVED messages (via state machine)
  - idempotency_key uniqueness (deterministic + unique index)
  - process_once() NEVER sends when messaging_provider="none" or daily_send_limit==0 or outside outreach window
  - Lead do_not_contact / outreach_status=STOPPED blocks enqueue
  - max_attempts=3 with exponential backoff capped at 48h
- **Worker tick**: process_once() returns dict (configured, window, daily_limit, sent, failed, retried, skipped, note)

---

## 6. Worker implementation

- **OutreachScheduler** (app/workers/scheduler.py) — single daemon thread with stop event
- **Lifecycle**: start() / stop() / is_alive() / run_tick_now()
- **Loop**: _loop() sleeps in 0.1s increments for responsive stop()
- **Safety**:
  - Exception-safe: every tick exception is caught and logged; loop continues
  - Each tick uses its own database session
  - stop() is idempotent and joins the worker thread
  - Disabled by default when `scheduler_enabled=false` (settings)
  - Tick performs zero sends under default config (messaging_provider="none", daily_send_limit=0)
- **Factory**: create_scheduler() honors settings.scheduler_enabled; start_scheduler() / stop_scheduler() used by FastAPI lifespan

---

## 7. Scheduler implementation

- Same as Worker implementation above (OutreachScheduler is the scheduler; it drives the queue worker loop).
- Operates independently from the frontend (no browser dependency).
- Configurable interval via SCHEDULER_INTERVAL_SECONDS (default 60s).

---

## 8. Authentication

- **Bearer-token authentication** (opt-in, PR-C)
- **API_AUTH_ENABLED**: false by default; true in backend/.env production
- **API_AUTH_TOKEN**: constant-time comparison via hmac.compare_digest()
- **require_auth** dependency (app/api/security.py:42) — no-op while API_AUTH_ENABLED is false
- **Fail-safe**: if auth enabled but no token configured, every request is rejected (401)
- **Endpoint wiring**:
  - Public: GET /api/health (no auth required, rate-limited but unauthenticated)
  - Protected: all other /api/* endpoints require `Authorization: Bearer <token>` + rate limit
- **Token source**: environment variable API_AUTH_TOKEN (must be set in production; CHANGE_ME placeholder never used in production boot)

---

## 9. Authorization

- **CORS** (PR-C, environment-driven):
  - CORS_ORIGINS: comma-separated origins (required in production; development default localhost:5173)
  - CORS_ALLOW_CREDENTIALS: true (wildcard '*' rejected when credentials enabled)
  - CORS_ALLOW_METHODS: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
  - CORS_ALLOW_HEADERS: ["Authorization", "Content-Type"]
  - Production safety (Step 4 hardened): refuses to boot if app_env=="production" and CORS_ORIGINS is not in the process environment; also rejects the development localhost default http://localhost:5173 in production
- **Rate limiting** (PR-C, in-process fixed-window per client IP):
  - API_RATE_LIMIT_ENABLED: false by default
  - API_RATE_LIMIT_REQUESTS: 300 per 60s window
  - Limitation: in-process state NOT suitable for horizontal scaling; no external service (Redis) introduced
- **Human approval gate**: require_human_approval=true (safety invariant); every message must be explicitly APPROVED before enqueue/send

---

## 10. Environment-variable system (continued)

### Production environment audit (Step 3)

**Files audited**:
- `backend/.env` — production configuration (loaded at startup via `_load_dotenv()`)
- `backend/.env.example` — development template (should never contain real secrets)
- `config/settings.example.json` — example settings (does not exist in repo)

**Secret exposure assessment**:

| Variable | File | Value | Risk |
|---|---|---|---|
| `API_AUTH_TOKEN` | `backend/.env` | `(redacted real-looking token)` | **HIGH** — real-looking token in .env; must never be committed; production must set via environment variable only |
| `API_AUTH_TOKEN` | `backend/.env.example` | `CHANGE_ME` | **NONE** — placeholder template, safe to commit |
| `OMNIROUTE_API_KEY` | `backend/.env` | `(empty)` | **NONE** — empty by default, must be set via env in production |
| `OMNIROUTE_API_KEY` | `backend/.env.example` | `(empty)` | **NONE** — empty template, safe to commit |
| `OMNIROUTE_BASE_URL` | Both | `(empty)` | **NONE** — empty by default |
| `OMNIROUTE_MODEL` | Both | `(empty)` | **NONE** — empty by default |
| `DATABASE_URL` | Both | `sqlite:///...` | **MEDIUM** — path exposure; override via env in production |

**Required actions**:
1. **`backend/.env` must never be committed to source control**. It contains a real-looking `API_AUTH_TOKEN` that must be set via the deployment environment (`set API_AUTH_TOKEN=<value>` before starting uvicorn).
2. **`backend/.env.example`** is the template that should be committed; it currently has `API_AUTH_TOKEN=CHANGE_ME` which is correct.
3. **No `.gitignore`** exists in the repository root. Create one immediately to prevent accidental commitment of `.env`, `*.db`, `*.log`, and other sensitive files.
4. **Production deployment** must set all required variables via the environment before starting the application:
   - `APP_ENV=production`
   - `DATABASE_URL` (overridden from .env default)
   - `CORS_ORIGINS` (explicit, required in production — enforced by `config.py`)
   - `API_AUTH_TOKEN` (required if `API_AUTH_ENABLED=true` in production)
   - `API_RATE_LIMIT_ENABLED` / `API_RATE_LIMIT_REQUESTS` / `API_RATE_LIMIT_WINDOW_SECONDS`
   - `SCHEDULER_ENABLED` / `SCHEDULER_INTERVAL_SECONDS`
   - `MESSAGING_PROVIDER` (must remain `"none"` — safety critical)
   - `DAILY_SEND_LIMIT` (must remain `0` — safety critical)
   - `REQUIRE_HUMAN_APPROVAL` (must remain `true` — safety critical)

**No accidentally committed secrets found** beyond the `.env` file itself, which is why creating a `.gitignore` is the highest-priority remediation.

---

---

## 11. Existing Docker configuration

- **None found** in repository (no Dockerfile, no docker-compose.yml).
- **Deployment**: scripts/start.ps1 (PowerShell wrapper that starts uvicorn in the project's .venv).
- ** noted**: containerization would require new Dockerfiles for frontend, backend, worker, scheduler — not currently present.

---

## 12. Existing deployment configuration

- **scripts/start.ps1** — PowerShell startup wrapper (deterministic path, APP_ENV validation, CORS_ORIGINS / API_AUTH_TOKEN reminders).
- **docs/DEPLOYMENT.md** — exists but contains placeholder/pre-PR-C content; needs modernization for Phase 3 production.
- **docs/PRODUCTION_CONFIGURATION.md** — exists; documents production-safe configuration principles.
- **docs/PRODUCTON_CONFIGURATION.md** — references API auth, CORS, rate limiting as opt-in controls.

---

## 13. Existing CI/CD

- **pytest** test suite (340+ tests across milestones 2B–2K, verified twice)
- **Configuration**: backend/pytest.ini (rootdir settings)
- **Test categories**: unit, integration (marked with `@pytest.mark.integration`)
- **No CI pipeline files** found (no GitHub Actions, no GitLab CI, no Makefile).
- **Tests re-run**: 340/340 passing twice (run 1: 17.49s; run 2: 18.28s) — deterministic and re-runnable.

---

## 14. Existing tests

- **Total**: 29 test files in backend/tests/ (conftest.py + 28*.py)
- **Coverage**: 340 test items across all Phase 2 milestones (2B–2K), verified twice (run 1: 340/340 in 17.49s; run 2: 340/340 in 18.28s)
- **Test areas**:
  - Message generation (2B): 116/116
  - Human approval (2C): 138/138
  - Provider abstraction (2D): 160/160
  - Outreach queue (2E): 185/185
  - Scheduler (2F): 194/194
  - Reply ingestion (2G): 228/228
  - Follow-ups (2H): 240/240
  - Analytics (2I): 257/257
  - Security audit (2K): 340/340 (encompasses all above under safety regression)
  - API, database, production config, production DB safety
- **All tests**: messaging_provider="none", daily_send_limit=0, require_human_approval=true — real sends impossible. Deterministic and re-runnable.

---

## 15. Existing health checks

- **GET /api/health** (app/api/health.py) — returns:
  - status: "ok" / "degraded"
  - database: "ok" / "error"
  - app_env
  - ai_provider
  - messaging_provider
- **Limitation**: health check includes messaging_provider health_check() which may attempt provider-specific I/O; with default config (none) returns {"provider": "none", "enabled": False, "status": "disabled"}.

---

## 16. Existing logging

- **Structured logging** (app/core/logging.py):
  - Format: `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`
  - StreamHandler (stdout) + RotatingFileHandler (app.log, 5MB, 5 backups)
  - Verbose mode in development only
- **SENSITIVE_KEYS** set: api_key, password, secret, token, authorization, auth, omniroute_api_key, notification_target
- **redact()** and **redact_event_data()** — recursively redacts sensitive keys from log output
- **get_logger(name)** — per-module loggers
- **log_event()** — structured event logging with redaction
- **Note**: uvicorn.access and httpx loggers suppressed to WARNING

---

## 17. Existing error handling

- **FastAPI exception handlers** (app/api/security.py:131):
  - RequestValidationError → 422 with exc.errors()
  - General Exception → 500 "Internal server error" in production (with sanitized log); full traceback in development
- **Sanitization**: production environment never logs exception messages (may embed secrets); development logs full traceback.

---

## 18. Existing security controls

| Control | Status |
|---|---|
| Bearer-token auth | Opt-in (API_AUTH_ENABLED); constant-time comparison via hmac |
| Rate limiting | Opt-in (API_RATE_LIMIT_ENABLED); in-process per-IP fixed-window |
| CORS | Opt-in, environment-driven; production requires explicit CORS_ORIGINS; wildcard '*' rejected with credentials |
| State machine | All 11 message.status writes guarded by assert_transition |
| Safety gates | assert_send_allowed + assert_message_sendable at OutboundSender boundary |
| Messaging provider | Default "none"; no sends possible without explicit activation |
| Human approval | require_human_approval=true; mandatory before enqueue/send |
| Log redaction | SENSITIVE_KEYS set; redact_event_data() used in log_event() |
| SQL injection | SQLAlchemy ORM (parameterized queries) |
| Path traversal | Frontend serve checks `requested.resolve().relative_to(FRONTEND_DIST.resolve())` |

---

## 19. Existing messaging-provider abstraction

- **NoOpProvider** (app/integrations/messaging.py:54) — default; `name = "none"`, send() raises NotImplementedError
- **WhatsAppOpenWAProvider** (app/integrations/providers/whatsapp_openwa.py) — dormant stub; registered but DORMANT (cannot send until explicit activation)
- **Provider registry** (app/integrations/registry.py:24) — get_messaging_provider() reads settings.messaging_provider; falls back to NoOpProvider if unknown
- **Safety** (app/integrations/safety.py): assert_send_allowed() and assert_message_sendable() enforce:
  1. Provider must not be "none" / "" (MessagingDisabledError)
  2. Message must have approved_at (ApprovalRequiredError)
  3. Message must not be terminal (MessageTerminalError)
  4. Provider must be activated (ProviderNotActivatedError for dormant stubs)
- **OutboundSender** (app/integrations/adapters.py) — the ONLY component that invokes provider.send(); gated by assert_message_sendable

---

## 20. Existing human-approval mechanism

- **ApprovalService** (app/services/approval.py) — manages DRAFT -> PENDING_APPROVAL -> APPROVED/REJECTED/EDITED transitions
- **State machine** (app/core/state_machines.py) — validates every transition; terminal states (REPLIED, STOPPED) immutable
- **Endpoints** (app/api/messages.py):
  - POST /messages/{message_id}/request-approval — DRAFT → PENDING_APPROVAL
  - POST /messages/{message_id}/approve — PENDING_APPROVAL → APPROVED (requires approved_by)
  - POST /messages/{message_id}/reject — PENDING_APPROVAL → REJECTED (requires rejection_reason)
  - POST /messages/{message_id}/edit — forces re-approval (EDITED → PENDING_APPROVAL)
  - POST /messages/{message_id}/enqueue — queues APPROVED message (idempotent)
- **Rules**:
  - approve() only from PENDING_APPROVAL
  - reject() only from PENDING_APPROVAL with non-empty reason
  - edit() only from DRAFT/PENDING_APPROVAL/APPROVED/REJECTED with non-empty content
  - Terminal states (REPLIED, STOPPED) can never be modified
  - Edits force re-approval (approval cleared)

---

## Production blockers

1. **No Docker containers** — frontend, backend, worker, scheduler must be manually started
2. **No production environment configuration** — only backend/.env.example exists; no config/settings.json
3. **No HTTPS/domain** — application serves HTTP on 127.0.0.1:8000; no TLS termination
4. **No backup/restore automation** — data/backups/ exists but no automated procedure
5. **No monitoring/alerting** — only structured logs in app.log
6. **No process manager** — startup via scripts/start.ps1; no systemd, no supervisor
7. **No CI/CD pipeline** — tests run via pytest manually or via CI not configured
8. **No deployment runbook** — docs/DEPLOYMENT.md exists but needs Phase 3 modernization

---

## Security risks

1. **API auth token in .env** — backend/.env has `API_AUTH_TOKEN=(redacted)`. Production must set this via environment variable; config.py now enforces that `API_AUTH_TOKEN` comes from the process environment when `APP_ENV=production` and `API_AUTH_ENABLED=true`.
2. **CORS_ORIGINS default** — development default `http://localhost:5173`; config.py now rejects this in production and requires the value from the process environment (not from .env or settings.json).
3. **Secrets not rotated** — OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL, OMNIROUTE_MODEL default to empty; no rotation procedure.
4. **Log redaction edge cases** — SENSITIVE_KEYS set but custom keys may not be covered; need to verify all sensitive fields are included.
5. **In-process rate limiting** — not suitable for horizontal scaling; single-process state only.
6. **SQLite single-writer** — only one process can write at a time; may bottleneck under concurrent production load.
7. **No secrets in images** — no Docker images, so this is not applicable, but must ensure if containers are added.

---

## Phase 3 Step 4 — Configuration Hardening (completed)

**Date**: 2026-08-21
**Status**: COMPLETE

### What was hardened

1. **Production CORS_ORIGINS**: must now come from the process environment variable, not from `.env` or `settings.json`. The development localhost default (`http://localhost:5173`) is rejected in production even if explicitly set.
2. **Production API_AUTH_TOKEN**: when `API_AUTH_ENABLED=true`, the token must come from the process environment variable, not from `.env` or `settings.json`.
3. **Redundant checks removed**: the original two-step CORS validation (check for missing env+dotenv, then check for missing env) was consolidated into a single clean block.

### Implementation (config.py lines 205-226)

```python
if app_env == "production":
    if cors_origins_env is None:
        raise RuntimeError("CORS_ORIGINS must be set explicitly in the process environment when APP_ENV=production")
    if cors_origins == DEFAULT_CORS_ORIGINS:
        raise RuntimeError("CORS_ORIGINS must not use the development localhost origin ... when APP_ENV=production")
    if api_auth_enabled and os.getenv("API_AUTH_TOKEN") is None:
        raise RuntimeError("API_AUTH_TOKEN must be set in the process environment when APP_ENV=production and API_AUTH_ENABLED=true")
```

### Regression tests added (test_production_config.py)

- **TestProductionCorsHardening** (6 tests): missing CORS, valid CORS, development default, localhost rejection, dotenv-only rejection, silent-inherit prevention
- **TestProductionAuthHardening** (2 tests): missing token, explicit token
- **TestSafetyInvariants** (2 tests): human approval, messaging defaults

### Configuration matrix verified (direct runtime)

| Case | Scenario | Expected | Result |
|---|---|---|---|
| A | production + no CORS | FAIL | PASS |
| B | production + valid HTTPS CORS | PASS | PASS |
| C | development + no CORS | localhost default | PASS |
| D | production + localhost CORS | FAIL | PASS |
| E | production + CORS only in .env | FAIL | PASS |
| F | production + auth + no token | FAIL | PASS |
| G | production + auth + explicit token | PASS | PASS |
| H | safety defaults (none/0/true) | preserved | PASS |

### Test result

- Targeted: **16/16 passed**
- Full suite: **350/350 passed** (340 original + 10 new regression)
- Direct matrix: **10/10 checks passed**

### Safety invariants verified

- `messaging_provider` = `"none"`
- `daily_send_limit` = `0`
- `require_human_approval` = `true`

### Secret safety

- `backend/.env` is git-ignored and not tracked (`git ls-files` confirms)
- No real `API_AUTH_TOKEN` appears in source, tests, or documentation
- `PHASE3_AUDIT.md` token references redacted to `(redacted)`
- `.env.example` contains only `CHANGE_ME` placeholder

---

## Missing infrastructure

1. **Dockerfiles** for frontend, backend, worker, scheduler
2. **docker-compose.yml** (or equivalent orchestration)
3. **Production .env** with explicit values (CORS_ORIGINS, API_AUTH_TOKEN, DATABASE_URL, etc.)
4. **config/settings.json** (optional override; does not yet exist)
5. **Backup automation** (scripts for DB backup, verification, restoration)
6. **Health/readiness endpoints** — /api/health exists but needs enhancement (readiness should check deps without exposing secrets)
7. **Domain + HTTPS configuration** — no HTTPS, no domain setup
8. **Process management** (systemd service files, supervisor configs)
9. **Monitoring/alerting** (Prometheus metrics, log aggregation)
10. **Log rotation verification** (current RotatingFileHandler settings adequate but not validated)

---

## Required changes (minimum for Phase 3 production readiness)

1. **Create production .env** with explicit values (see section 10 for required variables)
2. **Create config/settings.json** (optional override of built-in defaults)
3. **Enhance health endpoint** — /api/health + /api/ready (readiness checks deps, no secrets)
4. **Add backup/restore procedures** — document and automate (DATABASE_BACKUP.md)
5. **Create Docker production documentation** (DOCKER_PRODUCTION.md)
6. **Create DEPLOYMENT.md** (production deployment runbook)
7. **Prepare domain + HTTPS** — configure proxy (Traefik, NGINX, or cloud provider) for https://YOUR-DOMAIN.com
8. **Add structured monitoring** — correlation IDs, enhanced logging, metrics
9. **Verify all secrets are env-only** — no real credentials in source files
10. **Run full test suite** — 262/262 passing, then re-run twice

---

## Recommended deployment architecture

```
                    INTERNET
                       │
                       ▼
                HTTPS / DOMAIN
                       │
                       ▼
                  FRONTEND   (React + Tailwind, served via Vite build or CDN)
                       │
                       ▼
                   API SERVER  (FastAPI + uvicorn on 127.0.0.1:8000)
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       DATABASE       QUEUE       EXTERNAL APIs
                         │
                         ▼
                       WORKER
                         │
                         ▼
                    SCHEDULER
```

- **Frontend**: static serve from CDN or web server (nginx) pointing to frontend/dist/
- **Backend**: uvicorn behind production-grade process manager (systemd, gunicorn+uvicorn)
- **Database**: SQLite (local file) or PostgreSQL (if migrating); regular backups to data/backups/
- **Queue**: SQLite-backed (already integrated); no external Redis required for Phase 3
- **Worker**: threading-based scheduler (already independent of frontend)
- **Scheduler**: daemon thread with stop event (already integrated in FastAPI lifespan)
- **HTTPS**: terminate at reverse proxy or load balancer; forward headers via X-Forwarded-Proto
- **Domain**: https://YOUR-DOMAIN.com (not purchased here; operator to configure)

---

## Next step

Proceed to **STEP 2 — VERIFY CURRENT TEST BASELINE**: run the complete existing test suite and record results. Do not proceed with implementation changes until the baseline is verified passing.
