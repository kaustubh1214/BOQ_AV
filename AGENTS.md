# AV Group BOQ Management — guide for AI agents & developers

Self-contained sub-project of this repository (unrelated to the analytics dashboard at the repo root).
Read **HANDOFF.md** first for status, then **PLAN.md** (design) and **FORMULAS.md** (every number).

## Layout

```
boq-management/
├── boq-api/            FastAPI + SQLAlchemy 2 (async) + PostgreSQL 17 + Redis (optional)
│   ├── app/core/       config, database, security (argon2, JWT), errors, middleware, redis, responses
│   ├── app/models/     ORM models: auth.py, masters.py, documents.py, stock.py, enums.py
│   ├── app/services/   business logic: stock_engine, boq, reports, exports, pdf, notify, context (RBAC scopes)
│   ├── app/api/v1/     routers (one per module) + deps.py (auth, Perm, finish, Idempotency, respond)
│   ├── app/seed/       base.py (roles, permissions, UOMs, central store, the one Super Admin) · demo.py (AV Group demo via the API)
│   ├── app/cli.py      server-side recovery: `python -m app.cli reset-superadmin`
│   ├── alembic/        migrations (includes the stock-ledger append-only trigger)
│   ├── tests/          pytest suite (real Postgres `boq_test`, ~120 tests incl. RBAC matrix + role rules)
│   └── openapi.json    exported API contract (regenerate: python scripts/export_openapi.py)
└── boq-ui/             React 19 + Vite + TypeScript + Tailwind v4 + TanStack Query + Recharts
    └── src/{lib,components,features}
```

## Commands

```bash
# infra (Postgres :5436, Redis :6380)
docker compose up -d db redis
# API (from boq-api/)
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed --demo        # idempotent; demo part skipped if a BOQ exists
.venv/Scripts/python -m uvicorn app.main:app --port 8100 --reload
.venv/Scripts/python -m pytest -q              # recreates boq_test each run
.venv/Scripts/ruff check app tests && .venv/Scripts/ruff format app tests
# UI (from boq-ui/)
npm run dev        # http://localhost:3100, proxies /api → :8100
npm run typecheck && npm run build
# full stack in containers
docker compose up -d --build                   # UI http://localhost:3100, API http://localhost:8100/docs
```

On Windows, `npm` started from tools may break on the space in `C:\Program Files`; `node node_modules/vite/bin/vite.js` works.
Rebuild only the container you changed (`docker compose up -d --build api` or `ui`).

## Hard rules (do not break)

1. **Stock changes only through `services/stock_engine.post_movements`.** Never write `stock_balances` or
   `stock_ledger` directly. The ledger is append-only (DB trigger); corrections are `REVERSAL` movements.
2. **No hard deletes of documents.** Cancel → reversal. Masters are deactivated (`is_active=false`).
3. **Every mutating endpoint**: check permission (`Perm(...)`) **and** scope (`ensure_covers` / `scope_filter`),
   check `version` (`check_version`) on updates, lock the row (`get_or_404(..., lock=True)`), write `audit(...)`,
   and return via `finish(db, ctx, loader, idem)` (builds the response inside the transaction, then commits).
4. **Out-of-scope records return 404**, not 403 (use `ensure_covers`).
5. **No maker-checker.** The Store Manager prepares, approves and posts everything; there is no env switch for a two-person rule.
5a. **Three roles only** — `SUPER_ADMIN` (exactly one, seeded from `.env`; DB trigger + API refuse a second; cannot be
   deactivated/re-assigned; invisible to non-super users), `STORE_MANAGER` (global, company admin, manages Site Users only),
   `SITE_USER` (site scope, no ₹). Don't reintroduce PM/Keeper/Director roles without being asked. Request flow is
   Site User → Store Manager; `POST /rfqs/{id}/approve-and-issue` does approve + create note + dispatch atomically.
6. **Finance fields** are nulled for users without `finance.view` by `scrub()` using the names in
   `services/permissions.FINANCE_FIELDS`. Name money/rate fields accordingly — and never name a non-finance
   field `value`, `rate`, `amount`… (the dashboard returns without scrub for exactly that reason).
   Finance-typed schema fields must be `OptNum` (nullable).
7. **New permission** → add to `PERMISSIONS` and the role matrix in `services/permissions.py`, re-run the seed,
   extend `tests/test_rbac.py`. System roles are re-synced from code on every seed run.
8. **Schema changes** → Alembic migration (`alembic revision --autogenerate -m ...`), review it, run up/down.
9. **Async SQLAlchemy**: never touch an expired/unloaded attribute (MissingGreenlet). `Base` uses
   `eager_defaults`; reload with `get_or_404` (populate_existing) after mutations; `Boq.items` is
   `raise_on_sql` — load with `BOQ_FULL` options.
10. **Money/qty are Decimal** server-side; JSON carries numbers (`Num`/`OptNum` serializers). Indian formatting
    (1,00,000) is a UI concern (`lib/format.ts`).
11. Create endpoints accept an `Idempotency-Key` header (mobile retries) — keep that for new creates.
12. Single company (AV Group). Don't add multi-tenancy without being asked; UUID keys keep it possible later.
13. Secrets live in `.env` (git-ignored). Never commit credentials; `.env.example` holds placeholders only.

## Frontend conventions

- Server state via TanStack Query (`useList`, `useOne`, `useAction` in `lib/hooks.ts`); list filters live in
  the URL (`useUrlFilters`) so links like `/rfqs?queue=store` work.
- Lists use `DataList` (table ≥ md, cards on phones). Forms end with `ActionBar` (sticky on phones).
  Dialogs are bottom sheets on phones. Inputs ≥16px on mobile (no iOS zoom). Check 375px width: no sideways scroll.
- `useAuth().can(...)` only hides UI; the API is the authority.
- Access token is memory-only; refresh token is an httpOnly cookie scoped to `/api/v1/auth`.
- Charts: one validated series colour (`--series-1`), text in text tokens, tooltips on hover (see dataviz rules).

## API conventions (also what the Flutter app relies on)

- Base `/api/v1`; envelope `{success, data, meta}`; errors `{success:false, error:{code, message, details}}` —
  clients switch on `error.code`.
- Mobile auth: `POST /auth/token` → access + refresh in body; `POST /auth/refresh` with `{refresh_token}`.
- Optimistic locking: send the document's `version` back on every update/transition → `409 VERSION_CONFLICT`.
- Delta sync for master caches: `GET /masters/<x>?updated_since=<iso>`.
- Enums with labels: `GET /meta/enums`. Push tokens: `POST /me/device-tokens`.
