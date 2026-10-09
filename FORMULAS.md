# BOQ Management — Business Formulas (single source of truth)

Every figure shown in the app, the reports and the Excel exports is computed exactly as below.
Code references point at the implementation; change both together.

All quantities are in the material's **base UOM** (entries in another unit — e.g. BAG — are converted
on input via `uom_conversions`, material-specific first, then global). Quantities are `NUMERIC(18,3)`,
rates `NUMERIC(18,4)`, values `NUMERIC(20,4)`, money `NUMERIC(18,2)`. Never float.

## 1. BOQ (central store master) — `app/services/boq.py`

| Metric | Formula |
|---|---|
| Estimated amount (line) | `planned_qty × estimated_rate` |
| BOQ total estimated value | Σ line estimated amounts |
| Benchmark rate (snapshot on save) | average market rate of the material over the trailing 180 days |
| Accepted inward | Σ `grn_items.accepted_qty` of **POSTED** GRNs whose BOQ is in the same revision chain (`root_boq_id`) |
| **Pending to procure** | `max(planned − accepted_inward, 0)` → *1,00,000 − 10,000 = 90,000* |
| Excess inward | `max(accepted_inward − planned, 0)` |
| Issued | Σ `issue_note_items.issued_qty` of ISSUED / RECEIVED / PARTIALLY_RECEIVED notes dated inside the BOQ's financial year |
| Balance to issue | `max(planned − issued, 0)` |
| Central stock | Σ `stock_balances.qty` over CENTRAL stores |
| Procurement % | `accepted_inward / planned × 100` |
| Utilisation % | `issued / planned × 100` |
| Actual avg purchase rate | `Σ grn amount / Σ accepted qty` (posted GRNs in the chain) |
| Rate variance % | `(actual_avg_rate − estimated_rate) / estimated_rate × 100` |
| Benchmark gap % | `(actual_avg_rate − benchmark_rate) / benchmark_rate × 100` |
| Budget used % | `procured_value / estimated_value × 100` |

**Tolerance rule (GRN post):** cumulative accepted qty for a material may not exceed
`planned × (1 + GRN_TOLERANCE_PCT/100)` (default 5 %) → error `GRN_EXCEEDS_BOQ`. Revise the BOQ instead.

**One live BOQ per FY:** a partial unique index allows only one `APPROVED` BOQ per financial year.
Approving a revision marks its parent `SUPERSEDED` in the same transaction.

## 2. Stock valuation — `app/services/stock_engine.py`

Weighted average, per (store, material):

```
inward:   new_qty   = qty + in_qty
          new_value = value + in_qty × in_rate
          avg       = new_value / new_qty
outward:  out_rate  = current avg   (or the original rate for reversals)
          new_qty   = qty − out_qty               (must stay ≥ 0 → INSUFFICIENT_STOCK)
          new_value = value − out_qty × out_rate  (0 when qty reaches 0)
```

Worked example (cement): 10,000 @ ₹6.80 + 5,000 @ ₹7.10 → 15,000 @ **₹6.90**; issue 3,000 → out at ₹6.90, 12,000 left.

| Movement | Store | Direction | Rate |
|---|---|---|---|
| GRN post (`INWARD`) | central | + accepted qty | GRN rate (excl. GST) |
| Issue (`ISSUE`) | central | − issued qty | current avg |
| Receipt (`RECEIPT`) | site | + received qty | issue rate |
| Consumption (`CONSUMPTION`) | site | − qty | site avg |
| Return dispatch (`RETURN_OUT`) | site | − qty | site avg |
| Return receipt (`RETURN_IN`) | central | + received qty | dispatch rate |
| Adjustment (`ADJUSTMENT`) | any | ± delta | opening: given rate · surplus: current avg · loss: current avg |
| Cancellation (`REVERSAL`) | original store | opposite of original | original rate |

GST is captured on the GRN (`gst_amount = amount × gst_rate / 100`) but **not** included in stock value
(input-credit assumption).

## 3. Requests, issues, transit

| Metric | Formula |
|---|---|
| Pending to issue (request line) | `approved_qty − issued_qty` |
| Committed on drafts | Σ issued qty of non-cancelled issue notes for the line (drafts included) — a new note may not exceed `approved − committed` |
| Request status after an issue | `ISSUED` if every line `issued ≥ approved`, else `PARTIALLY_ISSUED` |
| Receipt rule | `received + shortage + damaged = issued` for every line |
| Transit loss % | `(shortage + damaged) / issued × 100` |
| Transit loss value | `(shortage + damaged) × issue rate` |
| RFQ turnaround (hours) | `first PM approval − submitted`, `first store approval − submitted`, `first issue − submitted` |

**Project allocation check (store approval):**
`remaining = allocated(project, material) − approved in other live requests of the FY − this request's approved qty`.
If `remaining < 0` the store must pass `override_allocation=true` (recorded as `[Allocation override]`).

## 4. Site

| Metric | Formula |
|---|---|
| Consumed 7d / 30d | Σ POSTED consumption qty with `entry_date` in the last 7 / 30 days (today inclusive) |
| Avg daily consumption | `consumed_7d / 7` and `consumed_30d / 30` |
| **Days of cover** | `site_qty / avg_daily_7d` (falls back to the 30-day average when the 7-day one is 0; blank when no consumption) |
| In transit to site | Σ issued qty of notes in status `ISSUED` for the site |

## 5. Physical count

`delta = physical_qty − system_qty`, where `system_qty` is read **under row lock at approval time**.

## 6. Dates

Financial year runs April–March (`2026-10-09 → "2026-27"`). Document numbers: `<TYPE>/<FY>/<0001>` per type
per FY (`BOQ`, `GRN`, `RFQ`, `ISN`, `RTN`, `ADJ`); BOQ revisions are `<root no>-R<n>`. Business dates use
`Asia/Kolkata`; timestamps are stored in UTC.
