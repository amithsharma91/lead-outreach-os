# PHASE 3 PREPARATION

Updated: 2026-08-19 — preparation only. NOTHING in this document has been implemented.

## 1. Canonical roadmap determination

- The repository contains **no roadmap document** (verified by search: only `docs/FINAL_PHASE_2_AUDIT.md` exists).
- The canonical roadmap is the **master prompt (project execution rules)** from the operator: Phase 0 (baseline) → Phase 1 (AI intelligence) → Phase 2 (outreach automation 2A–2K, COMPLETE) → Phase 3 (next) → further phases.
- **Conflict analysis**: no conflicting roadmap documents exist in the repository. The only ambiguity is that the exact Phase 3 definition from the master prompt is not present verbatim in the repository or in this session's accessible context.
- **Resolution**: Phase 3 scope below is **PROPOSED** and labeled as such. It MUST be confirmed or replaced by the operator from the canonical roadmap before any implementation. Nothing here may be implemented without explicit per-milestone authorization.

## 2. Current state (verified 2026-08-19)

- 262/262 backend tests passing (independent re-run this session).
- `messaging_provider="none"`, `daily_send_limit=0`, `require_human_approval=true` — real sends impossible.
- Full Phase 2 stack live: generation → approval → queue → scheduler → send (gated) → reply ingestion → follow-ups → analytics → frontend.
- Provider registry: `none` + dormant `whatsapp_openwa` stub; zero network imports in app code.
- `init_db()` refuses outside `APP_ENV=test`; state machine enforced on all status writes; legacy `record_reply` hardened.
- Frontend builds clean (`tsc --noEmit`, `vite build`).

## 3. Next phase (PROPOSED — pending operator confirmation of the canonical definition)

- **Phase number**: 3
- **Phase name (proposed)**: Campaign Operations & Production Readiness
- **Objective (proposed)**: Operate outreach at scale safely: full campaign lifecycle management, real-provider activation path (explicitly authorized only), monitoring/observability, and production deployment hardening.
- **Milestones (proposed, each independently authorized)**:
  - **3A** — Campaign lifecycle: CRUD/pause/resume, per-campaign windows and daily limits, campaign list in API + frontend (model fields already exist).
  - **3B** — Monitoring & observability: delivery receipts, message audit trail queries, activity stream search, alerting on failures/DNC violations.
  - **3C** — Real provider activation path: complete the WhatsApp OpenWA connector behind explicit activation gates (credential validation, dry-run mode, manual activation flag, per-provider limits). Activation itself is a SEPARATE operator authorization, never implied by milestone authorization.
  - **3D** — Production deployment: backend static serving of the frontend, backup/restore automation, health/readiness endpoints, ops runbook.
  - **3E** — Security & compliance pass: API authentication decision, DNC compliance checks, rate-limit verification, FINAL PHASE 3 AUDIT.

## 4. Prerequisites (currently missing — DOCUMENTED, not implemented)

1. **Canonical Phase 3 definition from the master prompt** — the operator must provide or confirm it (see section 1). BLOCKING.
2. **Official messaging credentials / provider endpoint config** — required only for 3C; must be supplied by the operator, never invented.
3. **Production `config/settings.json` or env configuration** — deployment-time decision (currently only the example file exists).
4. **API authentication decision** — operator choice for 3E (local-first vs. token/basic auth) since the dashboard becomes operational.
5. **Phase 0/1 audit documents** — optional; were delivered in chat only.

## 5. Existing components to reuse (MUST NOT be rewritten)

- Everything: queue, scheduler, approval service + endpoints, state machine, provider registry/abstraction, reply ingestion, follow-ups, analytics, message generator, frontend pages/routing, safety gates, all 262 tests.

## 6. Components that may need modification (justified only)

- `Campaign` CRUD: no campaign write API exists (only listing) — needed for 3A.
- `providers/whatsapp_openwa.py`: dormant stub → activation path (3C only).
- `app/main.py`: optional static frontend serving (3D).
- `app/core/config.py`: any new settings fields required by the confirmed Phase 3 scope.
- Frontend: campaign management + monitoring pages (3A/3B).

## 7. Dependencies

- No new Python libraries foreseen (dependency-free scheduler already exists; SQLAlchemy/FastAPI suffice).
- WhatsApp OpenWA: operator-provided endpoint URL/credentials (3C, only on explicit authorization).
- No infrastructure changes required for 3A/3B.

## 8. Safety requirements (binding for every Phase 3 milestone)

- No uncontrolled sends; no bulk-send commands; every send path passes `assert_message_sendable` (provider + `approved_at` + non-terminal).
- Human approval mandatory before any enqueue (`require_human_approval` stays true).
- `daily_send_limit` unchanged at 0 unless a milestone is EXPLICITLY authorized to raise it, with a documented provider-approved number.
- Outreach window (21:00–23:00 IST) enforced by the queue; per-campaign windows (3A) must never widen the global window.
- STOP enforcement: any STOP reply → `stop_lead` (DNC + STOPPED + active messages → STOPPED) — follow-ups and queue both honor it.
- Idempotency: enqueue dedup key + unique index; reply dedup keys — preserved in all new paths.
- Retry limits: `max_attempts=3`, backoff capped at 48h — unchanged.
- Provider isolation: dormant stub stays dormant until 3C activation gates pass; registry fallback to `none` on unknown names.
- Secrets: env-only, log redaction; credentials never written to code, config files, or logs.
- Rollback capability: additive migrations only (`ensure_schema`); backups exist (`data/backups`); no destructive DB ops outside APP_ENV=test.

## 9. Risks

- **Activation risk (highest)**: real provider activation can send real messages — mitigated by per-milestone authorization, dry-run gate, small authorized limit, and full suite regression.
- **Compliance risk**: DNC/STOP mis-handling could cause unwanted contact — mitigated by unified `stop_lead` and queue/follow-up checks.
- **Credential leakage**: provider credentials must remain env-only; never committed or logged.
- **Data loss**: only additive schema changes; backups required before any 3A campaign mutations.
- **Scope creep**: proposed milestones may diverge from the canonical Phase 3 definition — must be confirmed by the operator.
- **Operational**: frontend/API deployment split (dev proxy) — addressed in 3D.

## 10. Tests required before Phase 3 can be declared complete

- Full regression: 262/262 at every milestone boundary (re-run twice).
- Per-milestone tests: 3A campaign CRUD/pause/window/limit enforcement; 3B delivery receipts + audit queries; 3C dry-run (zero sends proven by socket-monkeypatch), activation gate rejection without credentials, per-provider limit enforcement; 3D static serving + backup/restore round-trip; 3E auth (if adopted) + DNC compliance sweep + rate-limit audit.
- Negative tests: every new endpoint must prove it cannot trigger a send or bypass approval.

## 11. Definition of Done

1. Each milestone: implementation + tests + full suite green twice + operator authorization recorded.
2. FINAL PHASE 3 AUDIT delivered in the mandated format with evidence.
3. No milestone claimed complete without objective evidence.
4. Safety invariants from section 8 hold at every commit point.

## 12. What MUST NOT happen

- NO provider activation, NO change of `messaging_provider` away from `none`, NO increase of `daily_send_limit` — unless an explicit standalone authorization says so.
- NO bulk sends, NO send command that bypasses approval/queue/window/limits.
- NO disabling or weakening of `assert_send_allowed`/`assert_message_sendable`, `require_human_approval`, the state machine, or the STOP/DNC path.
- NO `drop_all`/`create_all`/destructive DB operations in production code (init_db guard remains).
- NO rewriting of working Phase 2 components.
- NO starting of a later milestone before the previous one is authorized and green.