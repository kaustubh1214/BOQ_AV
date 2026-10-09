# HANDOFF — AV Group BOQ Management

Status date: 2026-10-09 · Branch `claude/boq-management-plan-732692` · Folder `boq-management/`
Read order for a new person/agent: this file → `AGENTS.md` (rules) → `PLAN.md` (design) → `FORMULAS.md` (every number).

## What exists (working)
React web app (mobile-first) + FastAPI + PostgreSQL implementing the full flow from the notebook sketch:
Market research → BOQ (central store master, approval, revisions, per-project allocation) → GRN inward
(weighted-average costing, BOQ tolerance) → Site RFQ (PM → Store approval, allocation check/override) →
Issue note (+ gate-pass PDF with QR) → site receipt (short/damaged) → site stock, daily consumption,
average/day and days-of-cover → returns → stock adjustments / physical count. Plus: 8-role scoped RBAC,
audit trail, 20 reports with Excel export, role dashboard, notifications, attachments, idempotency keys,
optimistic locking, Flutter-ready `/api/v1` (`boq-api/openapi.json`, 100 paths).

## Verification actually done
- Backend: **218 pytest tests pass** (real Postgres `boq_test`): worked example from the sketch, full role×endpoint
  RBAC matrix, scope isolation, maker-checker, tolerance, concurrency (8 parallel consumptions → no negative
  stock), append-only ledger trigger, idempotency, auth (rotation/reuse detection/lockout/rate limit), exports, PDF.
- `ruff` clean; `pip-audit` and `npm audit` clean after upgrading FastAPI 0.143 / Starlette 1.7 / PyJWT 2.15.1.
- UI: `tsc` + `vite build` clean. Checked in the in-app browser at 375px and 1280px: no sideways scroll on ~20
  routes; login, raise RFQ, PM approval (reduced qty), dialogs verified; strict CSP → zero violations.
- **Not done:** Playwright e2e, Locust load test, screenshots (pane was not visible), gate-pass PDF rendered
  visually in-browser (bytes verified only). Reports query live (no materialized views yet).

## Run it
Ports: Postgres 5436, Redis 6380, API 8100, UI 3100. Config in `boq-management/.env` (git-ignored; copy `.env.example`).
```bash
docker compose up -d --build        # UI :3100, API :8100/docs (docs off when ENV=production)
# host dev: see AGENTS.md → Commands. Demo data: python -m app.seed --demo (in boq-api)
```
Demo logins (emails; password = `DEMO_PASSWORD` in `.env`, admin = `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD`):
director@, procurement@, store.manager@, store.keeper@, pm.heights@, pm.greens@, site.towera@, site.towerb@,
site.phase1@, auditor@ `avgroup.in`. The dev DB also holds RFQ/2026-27/0004 (PM-approved) created during UI testing.
Containers seed only base data (roles, UOMs, central store, admin); demo data is host-run only.

## Decisions taken for the open questions (change if wrong)
One central BOQ per FY with optional project/site allocations · no Purchase Orders (`grns.po_id` reserved) · UI label
"Requests (RFQ)" · approval chain Site → PM → Store · sites hold stock and log daily consumption · GST captured on
GRN, excluded from valuation · "(AU)" in the sketch treated as audit/approval · single company, UUID keys · in-app
notifications only (email off).

## Deviations from PLAN.md
No materialized views; no TanStack Table/React-Hook-Form/Zod (plain state + custom `DataList`); approval chain is
code, not an `approval_rules` table; version sent in body (not `If-Match`); attachments on local disk (not S3
presigned); uvicorn workers (no gunicorn); delta sync (`updated_since`) on masters only.

## Gotchas already solved
Async SQLAlchemy MissingGreenlet (→ `eager_defaults`, `finish()`); nginx `add_header` in a location drops server-level
headers (→ `security-headers.conf` include); finance scrub blanked a field named `value` (dashboard bypasses scrub);
`npm` via tools breaks on the space in `C:\Program Files` (use `node .../vite.js`); Docker Desktop occasionally
fails a build with a snapshotter error — just retry.

## Before production
Replace every secret in `.env`; `COOKIE_SECURE=true`; enable HSTS line in `boq-ui/security-headers.conf`; set
`CORS_ORIGINS`; `ENV=production`; TLS proxy; Postgres backups (pg_dump + WAL); PgBouncer; pin image digests.

## Next steps (suggested order)
1. Flutter app from `openapi.json` (`dart-dio`), device tokens endpoint already exists. 2. Playwright e2e at 375/1280.
3. Locust on GRN/issue/consumption. 4. Materialized views for heavy reports. 5. S3/MinIO attachments. 6. SMTP
notifications via arq worker. 7. Purchase Orders. 8. Partition `stock_ledger`/`audit_logs` by year when large.

## Unrelated finding
Repo-root `docker-compose.yml` (analytics app) has real DB/Meta/Gemini credentials committed; a follow-up task chip
was raised. Those keys should be rotated. Not touched here.
