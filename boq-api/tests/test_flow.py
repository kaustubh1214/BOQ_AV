"""End-to-end: the worked example from PLAN.md §1 (the notebook sketch), step by step."""

import pytest


def line(progress: dict, material_id: str) -> dict:
    return next(ln for ln in progress["lines"] if ln["material_id"] == material_id)


async def grn(api, refs, items, vendor="V-SBCT") -> dict:
    sm = await api.as_("store_manager")
    g = await sm.ok("POST", "/grns", json={"vendor_id": refs["vendors"][vendor], "invoice_no": "T-1", "items": items})
    return await sm.ok("POST", f"/grns/{g['id']}/post", json={"version": g["version"]})


async def approved_rfq(api, refs, site_code: str, items: list[dict], site_user: str = "site_a") -> dict:
    site = await api.as_(site_user)
    r = await site.ok("POST", "/rfqs", json={"site_id": refs["sites"][site_code]["id"], "submit": True, "items": items})
    return await (await api.as_("store_manager")).ok(
        "POST", f"/rfqs/{r['id']}/store-approve", json={"version": r["version"], "override_allocation": True}
    )


async def issue(api, rfq: dict, qty_by_item: dict | None = None) -> dict:
    sm = await api.as_("store_manager")
    items = [{"request_item_id": i["id"], "issued_qty": (qty_by_item or {}).get(i["id"], i["approved_qty"])} for i in rfq["items"]]
    n = await sm.ok("POST", "/issue-notes", json={"request_id": rfq["id"], "items": items})
    return await sm.ok("POST", f"/issue-notes/{n['id']}/issue", json={"version": n["version"]})


async def test_worked_example(api, refs, plan):
    ids = await plan(
        [
            {
                "name": "Cement",
                "planned": 100000,
                "rate": 7.00,
                "allocations": [{"project_id": refs["projects"]["AVH"], "site_id": refs["sites"]["AVH-TA"]["id"], "allocated_qty": 35000}],
            }
        ]
    )
    cement = ids["Cement"]
    director = await api.as_("store_manager")  # sees ₹ values and the BOQ
    boq = await director.ok("GET", "/boqs/current")

    # Step 1 — planned 1,00,000; nothing received yet
    p = await director.ok("GET", f"/boqs/{boq['id']}/progress")
    assert line(p, cement)["pending_to_procure"] == 100000

    # Step 2 — GRN 10,000 kg @ ₹6.80 → pending 90,000
    await grn(api, refs, [{"material_id": cement, "received_qty": 10000, "rate": 6.80}])
    p = await director.ok("GET", f"/boqs/{boq['id']}/progress")
    ln = line(p, cement)
    assert (ln["accepted_inward"], ln["pending_to_procure"]) == (10000, 90000)

    # Step 3 — GRN 5,000 kg @ ₹7.10 → pending 85,000, weighted average ₹6.90
    await grn(api, refs, [{"material_id": cement, "received_qty": 5000, "rate": 7.10}], vendor="V-SVBM")
    ln = line(await director.ok("GET", f"/boqs/{boq['id']}/progress"), cement)
    assert ln["pending_to_procure"] == 85000
    assert ln["central_stock"] == 15000
    assert ln["actual_avg_rate"] == pytest.approx(6.90)
    assert ln["rate_variance_pct"] == pytest.approx(-1.43)

    # Step 4 — Tower A requests 3,000 → Store Manager approves → issued at ₹6.90
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": cement, "requested_qty": 3000}])
    note = await issue(api, rfq)
    assert note["status"] == "ISSUED"
    assert note["items"][0]["rate"] == pytest.approx(6.90)
    assert note["total_value"] == pytest.approx(20700.00)
    ln = line(await director.ok("GET", f"/boqs/{boq['id']}/progress"), cement)
    assert (ln["central_stock"], ln["issued_qty"], ln["balance_to_issue"]) == (12000, 3000, 97000)
    rfq_after = await director.ok("GET", f"/rfqs/{rfq['id']}")
    assert rfq_after["status"] == "ISSUED"

    # Step 5 — site receives 2,950, 50 damaged
    site_a = await api.as_("site_a")
    received = await site_a.ok(
        "POST",
        f"/issue-notes/{note['id']}/receive",
        json={"items": [{"item_id": note["items"][0]["id"], "received_qty": 2950, "damaged_qty": 50}], "version": note["version"]},
    )
    assert received["status"] == "PARTIALLY_RECEIVED"
    stock = {s["material"]["id"]: s for s in await site_a.ok("GET", f"/site/stock?site_id={refs['sites']['AVH-TA']['id']}")}
    assert stock[cement]["qty"] == 2950
    assert stock[cement]["avg_rate"] is None  # site engineers never see rates

    # Step 6 — consumption 500 kg/day for 2 days → 1,950 left, days of cover from 7-day average
    for _ in range(2):
        await site_a.ok(
            "POST", "/consumptions", json={"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": cement, "qty": 500}]}
        )
    stock = {s["material"]["id"]: s for s in await site_a.ok("GET", f"/site/stock?site_id={refs['sites']['AVH-TA']['id']}")}
    assert stock[cement]["qty"] == 1950
    assert stock[cement]["avg_daily_7d"] == pytest.approx(1000 / 7, abs=0.001)
    assert stock[cement]["days_of_cover"] == pytest.approx(1950 / (1000 / 7), abs=0.02)

    # Reports reflect the same numbers
    rep = await director.ok("GET", "/reports/boq-vs-actual")
    row = next(
        r for r in rep["rows"] if r["material_name"].startswith("Cement") and r["planned_qty"] == 100000 and r["accepted_inward"] == 15000
    )
    assert row["pending_to_procure"] == 85000
    loss = await director.ok("GET", "/reports/transit-loss")
    assert any(r["issue_no"] == note["issue_no"] and r["damaged_qty"] == 50 for r in loss["rows"])


async def test_bag_conversion_on_grn(api, refs, plan):
    """Entering 20 BAG @ ₹350/bag stores 1,000 KG @ ₹7/kg (material-specific conversion)."""
    ids = await plan([{"name": "CementBag", "planned": 5000, "rate": 7}])
    admin = await api.as_("admin")
    await admin.ok(
        "POST",
        "/masters/uom-conversions",
        json={"material_id": ids["CementBag"], "from_uom_id": refs["uoms"]["BAG"], "to_uom_id": refs["uoms"]["KG"], "factor": 50},
    )
    g = await grn(api, refs, [{"material_id": ids["CementBag"], "received_qty": 20, "rate": 350, "uom_id": refs["uoms"]["BAG"]}])
    assert g["items"][0]["accepted_qty"] == 1000
    assert g["items"][0]["rate"] == 7


async def test_ledger_reconciles_with_balance(api, refs, plan):
    ids = await plan([{"name": "Recon", "planned": 1000, "rate": 10}])
    await grn(api, refs, [{"material_id": ids["Recon"], "received_qty": 300, "rate": 10}])
    await grn(api, refs, [{"material_id": ids["Recon"], "received_qty": 200, "rate": 13}])
    sm = await api.as_("store_manager")
    central = refs["stores"]["CS-01"]
    ledger = await sm.ok("GET", f"/stock/ledger?store_id={central}&material_id={ids['Recon']}")
    assert sum(r["qty_in"] - r["qty_out"] for r in ledger) == 500
    bal = await sm.ok("GET", f"/stock/balances?store_id={central}&material_id={ids['Recon']}")
    assert bal[0]["qty"] == 500
    assert bal[0]["avg_rate"] == pytest.approx(11.2)  # (300×10 + 200×13) / 500
    assert bal[0]["value"] == pytest.approx(5600)
