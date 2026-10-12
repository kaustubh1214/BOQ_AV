# Story-mode testing guide — AV Group BOQ

Open **http://localhost:3100** (start with `docker compose up -d` in `boq-management/` if it isn't running).
Use two browsers (or one normal + one private window) so you can stay signed in as two people at once.
Test the phone layout any time: press **F12 → device toolbar (Ctrl+Shift+M) → iPhone 12**.

> **Before you start:** press **Ctrl+F5** once so the browser loads the latest build (a tab left open from before an
> update may flash a "Something went wrong — Reload" card or reload itself once; that is expected, not a bug).

## The three roles

| Role | Who | In one line |
|---|---|---|
| **Super Admin** | our IT team — exactly **one** account | everything, plus roles, system settings and Store Manager accounts |
| **Store Manager** | AV Group's central-store admin | runs the BOQ, the central ledger, inward, approvals, issue; manages masters, sites, Site Users, reports, audit |
| **Site User** | one or more per site | raises requests, receives material, logs consumption, returns extras — own site only, **no ₹ values** |

## Test logins (local demo data only — change before any real use)

| Who | Email | Password |
|---|---|---|
| Super Admin | `superadmin@avgroup.in` | `Admin@12345` |
| Store Manager | `store.manager@avgroup.in` | `Avgroup@123` |
| Site User – Tower A | `site.towera@avgroup.in` | `Avgroup@123` |
| Site User – Tower B | `site.towerb@avgroup.in` | `Avgroup@123` |
| Site User – Phase 1 (AV Greens) | `site.phase1@avgroup.in` | `Avgroup@123` |

Sign out: top-right circle → **Sign out**. (5 wrong passwords in a minute locks the attempt for a minute — normal.)

Already in the demo data: an approved BOQ (8 materials, ₹1.07 Cr), 4 posted GRNs, one issued+received note with
50 kg cement damaged, 3 days of consumption, and requests waiting: **RFQ-0002** (Tower B, waiting for the Store Manager),
**RFQ-0003** (Phase 1, approved but not yet issued), plus one draft GRN waiting to be posted.

---

## Chapter 1 — Look around as the Store Manager (3 min)
1. Sign in as **store.manager**. The dashboard shows your tasks, three quick buttons (*Site requests to approve*,
   *Post inward*, *Central ledger*), BOQ budget and progress bars.
2. Sidebar → **BOQ (central store)** → open `BOQ/2026-27/0001`. The table is your sketch:
   **Planned → Inward → Pending to procure → Issued → Central stock**.
   Cement: planned **1,00,000**, inward **15,000**, pending **85,000**, issued **3,000**, stock **12,000**, avg ₹6.90.
3. Sidebar → **Central stock & ledger** → click the Cement row: every inward (+) and issue (−) with a running balance.
   This is the central ledger the store maintains.

## Chapter 2 — Add a material and its market rate (Store Manager does it all)
1. **Masters → Materials → New material**: Code `RMC-M25`, Name `Ready Mix Concrete M25`, Category `Aggregates & Sand`,
   Base UOM `CUM`, GST `18` → **Save**. (You can also add **Projects**, **Sites**, **Vendors** from the same page.)
2. **Market research → Record rate**: Material = Ready Mix Concrete M25, Vendor = Gajanan Aggregates, Rate `5200`;
   add a second quote at `5350`. Pick the material in "Compare a material": benchmark, vendor ranking, trend chart.

## Chapter 3 — Revise the BOQ (Store Manager, one person)
1. **BOQ** → open `BOQ/2026-27/0001` → **Revise** → **Add material** → Ready Mix Concrete M25, Planned `500`,
   Estimated rate `5300` → **Save & approve** (or **Save draft** → **Submit & approve**). One step: the Store Manager owns the BOQ, so submitting makes it live.
2. The old BOQ becomes *Superseded*; the new revision is live.

## Chapter 4 — Goods arrive (Store Manager)
1. **Inward (GRN) → New GRN**: Vendor `Gajanan Aggregates`, invoice `GA-200`; item Ready Mix Concrete M25,
   Received `100`, Rejected `5` (add a reason), Rate `5200` → **Save draft** → open it → **Post to stock**.
2. **Central stock & ledger** → Ready Mix: `Inward +95`. The BOQ page shows inward 95, pending 405.
3. Over-receipt guard: draft another GRN for 600 CUM and try to post → blocked ("exceeds BOQ").

## Chapter 5 — A site asks, the Store Manager approves and sends (the core of your sketch)
1. Sign in as **site.towera** (phone view recommended). Bottom bar: Home · Request · Receive · Consume · Stock.
   **Request → New request**: Site Tower A, Priority *Urgent*, Purpose `Slab L7`; Cement OPC 53 → `2000` → **Submit request**.
2. Sign in as **store.manager**. Dashboard task *Site requests awaiting your approval* → open the request.
   Two buttons: **Approve & issue** (one step) and **Approve only** (issue later).
3. Click **Approve & issue**. Change cement to `1800`, type vehicle `MH12XY1234` → **Approve & issue**.
   You land on the issue note: stock left the central store at the weighted-average rate. Click **Gate pass PDF** (QR included).
   If the project allocation is exceeded you'll be asked to tick an override.
4. Sign in as **site.towera** → **Receive** → open the note → **Receive at site**: Received `1780`, Damaged `20`
   → **Confirm receipt**. Status becomes "Received (loss)".
5. As **store.manager** → **Reports → Transit loss**: the 20 kg damage is listed.
6. *Approve only path:* open **RFQ-0003** (already approved) → **Create issue note** → **Save draft** → **Issue / dispatch**.

## Chapter 6 — Using it at site (Tower A)
1. **Consume → Log consumption** → today, Activity `Slab L7`, Cement `450` → **Save**. Do it for yesterday too.
2. **Stock**: quantity, **average per day** (7d / 30d) and **days of cover** (red if under 3 days).
3. **Return to store**: send back `100` kg → **Save draft** → **Dispatch**.
   Sign in as **store.manager** → **Returns** → open → **Receive in full**; central stock goes up.

## Chapter 7 — Stock correction (Store Manager)
**Adjustments → New adjustment**: Store = Central, Reason *Physical count*, Cement counted `11800` → **Save draft** → **Submit** → **Approve**.
The difference is posted to the ledger.

## Chapter 8 — Users and sites (Store Manager)
1. **Users → New user**: name, email, temporary password; the role is fixed to **Site User** — pick the site.
   Open the user later to disable them or reset their password.
2. Notice you cannot see the Super Admin, and you cannot create or edit another Store Manager.
3. Sign in as the new user: they must change the temporary password first, then see only their own site.
4. **Masters → Sites / Projects** to add a new site; **Audit trail** shows everything with who/when/IP and before/after.

## Chapter 9 — Reports and export
**Reports**: BOQ vs Actual, Rate variance, Stock summary, Stock ledger (bin card), Pending requests, Non-moving stock.
**Excel** on any report downloads (₹ in Indian format). Sign in as **site.towera**: the same reports show **no ₹ columns**, no Excel button.

## Chapter 10 — Super Admin (our IT team)
1. Sign in as **superadmin**. Extra sidebar items: **Roles & permissions** and **System**.
2. **Users → New user**: you can create a **Store Manager** here (role list includes it). There is no way to create a
   second Super Admin — try: it isn't offered, and the API/database refuse it.
3. **System**: user counts per role and the active settings.
4. **Roles & permissions**: the permission matrix (system roles are read-only; **Custom role** to copy one).
5. If the Super Admin password is ever lost, reset it from the server (see HANDOFF.md → *Recover the Super Admin*).

## Chapter 11 — Try to break it (these should all be refused)
- **site.towerb** opens a Tower A request link → "not found".
- **site.towera** has no ₹ anywhere, no *Users*, *Audit*, *Masters* edit buttons, and cannot approve requests.
- **store.manager** → Users: no Super Admin in the list; cannot edit another Store Manager; cannot open *Roles* or *System*.
- Edit a request in two tabs, save in both → second gets a "changed by someone else" message.
- Approve & issue more cement than the central stock holds → refused, and the request stays *Submitted* (nothing half-done).
- Wrong password 5× → temporary block.

## If something looks wrong
Hard refresh (Ctrl+F5). API health: http://localhost:8100/ready. Logs: `docker logs av-boq-api`.
Reset demo data: stop containers, `docker compose down -v`, `docker compose up -d`, then in `boq-api`:
`.venv/Scripts/python -m app.seed --demo` (migrations run automatically when the API container starts).
