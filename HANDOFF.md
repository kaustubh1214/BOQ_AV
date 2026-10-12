# HANDOFF — AV Group BOQ Management

Status date: 2026-10-12 (v2: simplified to three roles) · Branch `claude/boq-management-plan-732692` · Folder `boq-management/`
Read order for a new person/agent: this file → `AGENTS.md` (rules) → `PLAN.md` (design) → `FORMULAS.md` (every number).

## What exists (working)
React web app (mobile-first) + FastAPI + PostgreSQL implementing the full flow from the notebook sketch:
Market research → BOQ (central store master, revisions, per-project allocation) → GRN inward (weighted-average costing,
BOQ tolerance) → Site request (RFQ) → **Store Manager approves & issues in one step** (+ gate-pass PDF with QR) → site
receipt (short/damaged) → site stock, daily consumption, average/day and days-of-cover → returns → stock adjustments /
physical count. Plus: audit trail, 20 reports with Excel export, role dashboard, notifications, attachments, idempotency
keys, optimistic locking, Flutter-ready `/api/v1` (`boq-api/openapi.json`).

**Roles (v2, after AV Group's review said v1 was too complicated):**
- **Super Admin** — our IT team, **exactly one** account (seeded from `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD`). DB trigger
  `trg_single_super_admin` + API refuse a second; cannot be deactivated or re-assigned; hidden from Store Managers.
  Owns roles & permissions and the System page; creates Store Managers.
- **Store Manager** — AV Group's company admin and the keeper of the central ledger: masters, sites/projects, vendors,
  market research, BOQ (create + approve), GRN (draft + post), approve/reject/issue requests, returns, adjustments, all
  reports with ₹ + Excel, audit trail, and creates/edits **Site Users** only.
- **Site User** — one or more per site; own site only; no ₹ values.
Removed: Director, Procurement, Store Keeper, Project Manager, Auditor and the PM approval step. There is **no two-person rule**: the Store Manager can prepare, approve and post everything.

**Recover the Super Admin** (lost password / locked): on the server run
`docker exec -it av-boq-api python -m app.cli reset-superadmin` (or `python -m app.cli reset-superadmin` in `boq-api/`).

## Verification actually done
- Backend: **pytest suite passes** against real Postgres `boq_test`: worked example from the sketch, role × endpoint RBAC
  matrix (3 roles), scope isolation, single-Super-Admin rules (API + DB trigger), Store Manager limits, one-step
  Approve & issue incl. atomic rollback on short stock, tolerance, concurrency, append-only ledger, idempotency, auth
  (rotation/reuse detection/lockout/rate limit), exports, PDF, Super Admin CLI.
  Note: `test_concurrent_consumption_never_goes_negative` opens 8 parallel DB connections and is occasionally flaky on
  Docker Desktop for Windows (`ConnectionResetError` while connecting); it passes on re-run.
- `ruff` clean; `tsc` clean. Upgrade path from the v1 database verified (migration + seed retire old roles, deactivate
  their users, convert Store Managers to global scope).
- UI checked in the in-app browser: Store Manager approve-and-issue, Users page (Super Admin hidden, Site-User-only form),
  Super Admin System page.
- **Not done:** Playwright e2e, Locust load test, gate-pass PDF visual check. Reports query live (no materialized views).

## Run it
Ports: Postgres 5436, Redis 6380, API 8100, UI 3100. Config in `boq-management/.env` (git-ignored; copy `.env.example`).
```bash
docker compose up -d --build        # UI :3100, API :8100/docs (docs off when ENV=production)
# host dev: see AGENTS.md → Commands. Demo data: python -m app.seed --demo (in boq-api)
```
Demo logins (emails; password = `DEMO_PASSWORD` in `.env`, Super Admin = `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD`):
`superadmin@avgroup.in`, `store.manager@`, `site.towera@`, `site.towerb@`, `site.phase1@` `avgroup.in`.
Containers seed only base data (roles, UOMs, central store, the Super Admin); demo data is host-run only
(`python -m app.seed --demo` in `boq-api`).

## Decisions taken for the open questions (change if wrong)
One central BOQ per FY with optional project/site allocations · no Purchase Orders (`grns.po_id` reserved) · UI label
"Requests (RFQ)" · approval chain Site User → Store Manager (one-step Approve & issue) · sites hold stock and log daily consumption · GST captured on
GRN, excluded from valuation · "(AU)" in the sketch treated as audit/approval · single company, UUID keys · in-app
notifications only (email off).

## Deviations from PLAN.md
No materialized views; no TanStack Table/React-Hook-Form/Zod (plain state + custom `DataList`); approval chain is
code, not an `approval_rules` table; `PM_APPROVED` status kept in the enum only for old data; version sent in body (not `If-Match`); attachments on local disk (not S3
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
