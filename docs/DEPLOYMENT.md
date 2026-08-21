# Lead Outreach OS — Deployment Guide

## 1. Overview

Lead Outreach OS is deployed as a production web application with:

- **Backend:** FastAPI + SQLAlchemy
- **Frontend:** React + Vite + TypeScript
- **Database:** SQLite
- **AI provider:** OmniRoute
- **Authentication:** API token authentication
- **Production API prefix:** `/api`
- **Health endpoint:** `/api/health`
- **Readiness endpoint:** `/api/ready`

The backend can serve the built frontend in production.

---

# 2. Repository Structure

```text
lead-outreach-os/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── integrations/
│   │   └── main.py
│   ├── tests/
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── auth/
│   │   ├── lib/
│   │   └── pages/
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── pnpm-workspace.yaml
│   └── vite.config.*
│
├── data/
├── docs/
└── .env