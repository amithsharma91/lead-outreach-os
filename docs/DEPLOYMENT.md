# Lead Outreach OS — Deployment Guide

## 1. Overview

Lead Outreach OS is a cloud-hosted lead management system:

- **Backend:** FastAPI + SQLAlchemy (Python 3.14)
- **Frontend:** React + Vite + TypeScript
- **Database:** SQLite (dev) / PostgreSQL (production)
- **Containerization:** Docker (multi-stage build)
- **Scheduler:** Internal loop or external cron via `POST /api/queue/tick`
- **Authentication:** API token (env-controlled)
- **Health:** `GET /api/health` + `GET /api/ready`

---

## 2. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | No | `development` | `development` / `production` / `test` |
| `DATABASE_URL` | Production | `sqlite:///./data/lead_outreach.db` | PostgreSQL URL for production |
| `CORS_ORIGINS` | Yes (prod) | — | Comma-separated allowed origins |
| `API_AUTH_ENABLED` | No | `false` | Enable token auth |
| `API_AUTH_TOKEN` | If auth enabled | — | Bearer token for API auth |
| `SCHEDULER_ENABLED` | No | `true` | Enable internal scheduler loop |
| `SCHEDULER_INTERVAL_SECONDS` | No | `60` | Seconds between scheduler ticks |
| `OUTREACH_TIMEZONE` | No | `Asia/Kolkata` | Timezone for outreach windows |
| `OUTREACH_START_TIME` | No | `21:00` | Start of outreach window |
| `OUTREACH_END_TIME` | No | `23:00` | End of outreach window |
| `DAILY_SEND_LIMIT` | No | `0` | Max messages per day (0 = no sends) |
| `REQUIRE_HUMAN_APPROVAL` | No | `true` | Require approval before sending |
| `AI_PROVIDER` | No | `omniroute` | AI provider name |
| `MESSAGING_PROVIDER` | No | `none` | Messaging provider name |
| `OMNIROUTE_API_KEY` | No | — | OmniRoute API key |
| `OMNIROUTE_BASE_URL` | No | — | OmniRoute base URL |
| `OMNIROUTE_MODEL` | No | — | OmniRoute model name |
| `VITE_API_BASE_URL` | No | `/api` | Frontend API base URL override |

### Production Requirements

When `APP_ENV=production`:
- `CORS_ORIGINS` must be set explicitly (no localhost defaults)
- `DATABASE_URL` should point to PostgreSQL
- `API_AUTH_ENABLED=true` requires `API_AUTH_TOKEN` in the process environment

---

## 3. Docker

### Build

```bash
docker build -t lead-outreach-os .
```

Multi-stage build:
1. **Stage 1 (Node 20 Alpine):** Builds frontend static assets
2. **Stage 2 (Python 3.14 Slim):** Installs backend deps, copies frontend dist, runs uvicorn

### Run

```bash
docker run -p 8000:8000 \
  -e APP_ENV=development \
  -e CORS_ORIGINS=http://localhost:5173 \
  lead-outreach-os
```

### Production with PostgreSQL

```bash
docker run -p 8000:8000 \
  -e APP_ENV=production \
  -e DATABASE_URL=postgresql://user:pass@host:5432/dbname \
  -e CORS_ORIGINS=https://your-app.up.railway.app \
  -e API_AUTH_ENABLED=true \
  -e API_AUTH_TOKEN=your-secret-token \
  -e SCHEDULER_ENABLED=false \
  lead-outreach-os
```

### .dockerignore

Excludes `.env`, `__pycache__/`, `node_modules/`, `*.db`, `docs/`, `tests/`, `scripts/`, and IDE files from the build context.

---

## 4. Scheduler

Two modes:

### Internal Scheduler (default)

`SCHEDULER_ENABLED=true` starts an APScheduler loop inside the FastAPI process. Good for simple deployments.

### External Cron (recommended for cloud)

Set `SCHEDULER_ENABLED=false` and call the tick endpoint from an external scheduler:

```
POST https://your-app.up.railway.app/api/queue/tick
```

This is safe — the tick never sends messages when `MESSAGING_PROVIDER=none` or `DAILY_SEND_LIMIT=0`.

Example Railway cron (every 5 minutes):
```bash
curl -X POST https://your-app.up.railway.app/api/queue/tick
```

---

## 5. Database

### SQLite (development)

Default. No setup required. Creates `data/lead_outreach.db` automatically.

### PostgreSQL (production)

Set `DATABASE_URL` to a PostgreSQL connection string:
```
postgresql://user:password@host:5432/database_name
```

The schema is created automatically on startup via `ensure_schema()` — additive, idempotent, never destructive in production.

---

## 6. Health & Readiness

```bash
# Health check (always returns 200 if app is running)
curl https://your-app.up.railway.app/api/health

# Readiness check (returns 503 if DB is unreachable)
curl https://your-app.up.railway.app/api/ready
```

Use `GET /api/ready` for platform health checks (Railway, Docker healthcheck).

---

## 7. Deployment Platforms

### Railway

1. Connect your GitHub repo
2. Set environment variables in Railway dashboard
3. Railway auto-detects the `Dockerfile`
4. Set health check path to `/api/ready`
5. Add a cron job or use internal scheduler

### Docker (any provider)

1. Build and push the image
2. Set env vars at runtime
3. Expose port 8000
4. Mount a volume for `data/` if using SQLite (not recommended for production)

---

## 8. Security Notes

- `.env` is never committed (`.gitignore` protects it)
- `API_AUTH_TOKEN` must come from the process environment in production
- CORS origins must be explicit in production (no wildcard localhost)
- Database credentials should come from environment variables, not config files
- The frontend build is served by the backend but the frontend has no secrets
