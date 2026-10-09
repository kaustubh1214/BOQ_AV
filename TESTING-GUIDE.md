# Story-mode testing guide — AV Group BOQ

Open **http://localhost:3100** (start with `docker compose up -d` in `boq-management/` if it isn't running).
Use two browsers (or one normal + one private window) so you can stay signed in as two people at once.
Test the phone layout any time: press **F12 → device toolbar (Ctrl+Shift+M) → iPhone 12**.

## Test logins (local demo data only — change before any real use)

| Who | Email | Password | What they do |
|---|---|---|---|
| Admin | `admin@avgroup.in` | `Admin@12345` | masters, users, roles |
| Director | `director@avgroup.in` | `Avgroup@123` | approves BOQ, sees everything with ₹ values |
| Procurement | `procurement@avgroup.in` | `Avgroup@123` | market research, vendors |
| Store Manager | `store.manager@avgroup.in` | `Avgroup@123` | BOQ, posts GRN, approves requests, dispatches |
| Store Keeper | `store.keeper@avgroup.in` | `Avgroup@123` | drafts GRN / issue notes / counts |
| PM – AV Heights | `pm.heights@avgroup.in` | `Avgroup@123` | approves requests of Tower A & B |
| PM – AV Greens | `pm.greens@avgroup.in` | `Avgroup@123` | approves requests of Phase 1 |
| Site Eng. – Tower A | `site.towera@avgroup.in` | `Avgroup@123` | raises requests, receives, consumes (no ₹) |
| Site Eng. – Tower B | `site.towerb@avgroup.in` | `Avgroup@123` | same, Tower B only |
| Site Eng. – Phase 1 | `site.phase1@avgroup.in` | `Avgroup@123` | same, AV Greens only |
| Auditor | `auditor@avgroup.in` | `Avgroup@123` | read-only + audit trail |

Sign out: top-right circle → **Sign out**. (5 wrong passwords in a minute locks the attempt for a minute — normal.)

Already in the demo data: an approved BOQ (8 materials, ₹1.07 Cr), 4 posted GRNs, one issued+received note with
50 kg cement damaged, 3 days of consumption, and requests waiting in the queues (RFQ-0002 for the PM, RFQ-0003 and
RFQ-0004 for the store), plus one draft GRN waiting to be posted.

---

## Chapter 1 — Look around as the Director (2 min)
1. Sign in as **director**. Dashboard: BOQ budget, procured so far, bars for "BOQ progress", project chart.
2. Sidebar → **BOQ (central store)** → click `BOQ/2026-27/0001`.
3. The table is your sketch: **Planned → Inward → Pending to procure → Issued → Central stock**.
   Cement: planned **1,00,000**, inward **15,000**, pending **85,000**, issued **3,000**, stock **12,000**, avg ₹6.90.
4. Scroll: *Project allocations*, *Approval history*.

## Chapter 2 — Add a new material and its market rate (Admin → Procurement)
1. Sign in as **admin** → **Masters** → tab **Materials** → **New material**.
   Code `RMC-M25`, Name `Ready Mix Concrete M25`, Category `Aggregates & Sand`, Base UOM `CUM`, GST `18` → **Save**.
2. Sign out. Sign in as **procurement** → **Market research** → **Record rate**.
   Material = Ready Mix Concrete M25, Vendor = Gajanan Aggregates, Rate `5200` → **Save rate**. Add a second quote
   from another vendor at `5350`.
3. Pick the material in "Compare a material": you see **benchmark**, vendor ranking (LOWEST tag) and a trend chart.

## Chapter 3 — Revise the BOQ (Store Manager → Director)
(One approved BOQ is allowed per year, so you *revise* it.)
1. Sign in as **store.manager** → **BOQ** → open `BOQ/2026-27/0001` → **Revise**. You land on an editable copy.
2. **Add material** → pick Ready Mix Concrete M25 → Planned `500`, Estimated rate `5300` → **Save draft** → **Submit**.
3. Sign in as **director** → open the new revision (`…-R1`) → **Approve**. The old one becomes *Superseded*.
4. Try the maker-checker: as store.manager open a submitted BOQ — there is no Approve button for you.

## Chapter 4 — Goods arrive (Store Keeper → Store Manager)
1. Sign in as **store.keeper** → **Inward (GRN)** → **New GRN**. Vendor `Gajanan Aggregates`, invoice `GA-200`.
   Item: Ready Mix Concrete M25, Received `100`, Rejected `5` (add reason), Rate `5200` → **Save draft**.
2. As keeper note: there is no *Post* button (keeper can't post). Use **Attach photo / PDF** to attach a picture.
3. Sign in as **store.manager** → **Inward (GRN)** → open the GRN → **Post to stock**.
   (If you open your own draft you'll be told a different person must post.)
4. **Stock & ledger** → click the Ready Mix row: ledger shows `Inward +95`. Back in the BOQ page the progress shows
   inward 95, pending 405.
5. Over-receipt guard: draft another GRN for 600 CUM and try to post → blocked ("exceeds BOQ").

## Chapter 5 — A site asks, the store sends (the core of your sketch)
1. Sign in as **site.towera** (phone view recommended). Bottom bar: Home · Request · Receive · Consume · Stock.
   **Request** → **New request**: Site Tower A, Priority *Urgent*, Purpose `Slab L7`; material Cement OPC 53 → `2000`;
   **Submit request**.
2. Sign in as **pm.heights** → dashboard shows *Requests awaiting your approval* → open it → **Approve**,
   change cement to `1800` → **Approve**. (pm.greens can't even see this request — try it.)
3. Sign in as **store.manager** → **Requests (RFQ)** → tab *Store approval* → open → **Approve**.
   If the project allocation is exceeded you'll be asked to tick an override.
4. Sign in as **store.keeper** → **Requests** → tab *Ready to issue* → open → **Create issue note**
   (vehicle `MH12XY1234`, driver name) → **Save draft**.
5. Sign in as **store.manager** → **Issue notes** → open the draft → **Issue / dispatch**.
   Central stock drops at the weighted-average rate. Click **Gate pass PDF** (QR code included).
6. Sign in as **site.towera** → **Receive** → open the note → **Receive at site**: Received `1780`, Damaged `20`
   → **Confirm receipt**. Status becomes "Received (loss)".
7. Check **Reports → Transit loss**: the 20 kg damage is listed.

## Chapter 6 — Using it at site (Tower A)
1. **Consume** → **Log consumption** → today, Activity `Slab L7`, Cement `450` → **Save**. Do it for yesterday too (change date).
2. **Stock**: cement quantity, **average per day** (7d / 30d) and **days of cover** (red if under 3 days).
3. **Return to store**: send back `100` kg → **Save draft** → **Dispatch**.
   Sign in as **store.manager** → **Returns** → open → **Receive in full**; central stock goes up.

## Chapter 7 — Stock correction (Keeper → Manager)
1. As **store.keeper** → **Adjustments** → **New adjustment**: Store = Central, Reason *Physical count*,
   Cement counted `11800` → **Save draft** → open → **Submit**.
2. As **store.manager** → open it → **Approve**. System vs counted difference is posted; see it in the ledger.

## Chapter 8 — Reports and export
1. As **director** → **Reports**. Open **BOQ vs Actual**, **Rate variance**, **Stock summary**,
   **Stock ledger (bin card)** (choose Central store + a material), **Pending requests**, **Non-moving stock**.
2. Click **Excel** on any report → file downloads (₹ in Indian format).
3. Sign in as **store.keeper** → the same reports show **no ₹ columns** and no Excel button.

## Chapter 9 — Try to break it (these should all be refused)
- **site.towerb** opens a Tower A request link → "not found".
- **site.towera** has no ₹ rates/values anywhere and no *Users*/*Audit* menu.
- **store.manager** tries to approve a request they raised, or post a GRN they drafted → refused.
- Edit a request in two tabs, save in both → second gets a "changed by someone else" message.
- Wrong password 5× → temporary block; a new user (Admin → Users → **New user**) must change the temp password on first login.
- **auditor** → **Audit trail**: every step above with who/when/IP; click a row to see before/after.
- **admin** → **Roles & permissions**: the permission matrix (system roles are read-only; **Custom role** to copy one).

## If something looks wrong
Hard refresh (Ctrl+F5). API health: http://localhost:8100/ready. Logs: `docker logs av-boq-api`.
Reset demo data: stop containers, `docker compose down -v`, `docker compose up -d`, then in `boq-api`:
`.venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m app.seed --demo`.
