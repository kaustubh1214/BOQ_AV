"""Business rules, integrity and concurrency."""

import asyncio
import uuid

import asyncpg
import pytest

from tests.conftest import TEST_DB_URL
from tests.test_flow import approved_rfq, grn, issue


async def test_store_manager_can_post_their_own_grn(api, refs, plan):
    """No two-person rule: the Store Manager drafts and posts; site users still cannot post."""
    ids = await plan([{"name": "SelfPost", "planned": 100, "rate": 5}])
    sm = await api.as_("store_manager")
    g = await sm.ok(
        "POST",
        "/grns",
        json={"vendor_id": refs["vendors"]["V-SBCT"], "items": [{"material_id": ids["SelfPost"], "received_qty": 10, "rate": 5}]},
    )
    site = await api.as_("site_a")
    assert (await site.post(f"/grns/{g['id']}/post", json={"version": g["version"]})).status_code == 403  # no grn.post
    posted = await sm.ok("POST", f"/grns/{g['id']}/post", json={"version": g["version"]})
    assert posted["status"] == "POSTED"


async def test_grn_tolerance_blocks_over_receipt(api, refs, plan):
    ids = await plan([{"name": "Tolerance", "planned": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["Tolerance"], "received_qty": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["Tolerance"], "received_qty": 50, "rate": 5}])  # exactly +5 %
    sm = await api.as_("store_manager")
    g = await sm.ok(
        "POST",
        "/grns",
        json={"vendor_id": refs["vendors"]["V-SBCT"], "items": [{"material_id": ids["Tolerance"], "received_qty": 1, "rate": 5}]},
    )
    r = await sm.post(f"/grns/{g['id']}/post", json={"version": g["version"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "GRN_EXCEEDS_BOQ"


async def test_material_not_in_boq_rejected(api, refs, code):
    admin = await api.as_("admin")
    m = await admin.ok(
        "POST",
        "/masters/materials",
        json={"code": f"NB-{code}", "name": "Not planned", "category_id": refs["cats"]["CEM"], "base_uom_id": refs["uoms"]["KG"]},
    )
    r = await (await api.as_("store_manager")).post(
        "/grns", json={"vendor_id": refs["vendors"]["V-SBCT"], "items": [{"material_id": m["id"], "received_qty": 1, "rate": 1}]}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "MATERIAL_NOT_IN_BOQ"


async def test_issue_beyond_stock_and_approval(api, refs, plan):
    ids = await plan([{"name": "Scarce", "planned": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["Scarce"], "received_qty": 100, "rate": 5}])
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": ids["Scarce"], "requested_qty": 300}])
    sm = await api.as_("store_manager")
    item = rfq["items"][0]
    r = await sm.post("/issue-notes", json={"request_id": rfq["id"], "items": [{"request_item_id": item["id"], "issued_qty": 301}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXCEEDS_APPROVED"
    n = await sm.ok("POST", "/issue-notes", json={"request_id": rfq["id"], "items": [{"request_item_id": item["id"], "issued_qty": 300}]})
    r = await sm.post(f"/issue-notes/{n['id']}/issue", json={"version": n["version"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert r.json()["error"]["details"]["available"] == 100


async def test_scope_isolation_between_sites_and_projects(api, refs, plan):
    ids = await plan([{"name": "Scope", "planned": 100, "rate": 5}])
    site_a = await api.as_("site_a")
    r = await site_a.ok(
        "POST", "/rfqs", json={"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": ids["Scope"], "requested_qty": 1}]}
    )
    for who in ("site_b", "site_p1"):
        other = await api.as_(who)
        assert (await other.get(f"/rfqs/{r['id']}")).status_code == 404  # 404, not 403: no ID probing
        listed = await other.ok("GET", "/rfqs?limit=200")
        assert r["id"] not in {x["id"] for x in listed}
    assert (await (await api.as_("store_manager")).get(f"/rfqs/{r['id']}")).status_code == 200
    # Site B cannot raise a request for Tower A
    r2 = await (await api.as_("site_b")).post(
        "/rfqs", json={"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": ids["Scope"], "requested_qty": 1}]}
    )
    assert r2.status_code == 404


async def test_site_user_cannot_approve_or_issue(api, refs, plan):
    ids = await plan([{"name": "PMScope", "planned": 100, "rate": 5}])
    site_a = await api.as_("site_a")
    r = await site_a.ok(
        "POST",
        "/rfqs",
        json={"site_id": refs["sites"]["AVH-TA"]["id"], "submit": True, "items": [{"material_id": ids["PMScope"], "requested_qty": 1}]},
    )
    site = await api.as_("site_a")
    for path in ("store-approve", "approve-and-issue", "reject"):
        res = await site.post(f"/rfqs/{r['id']}/{path}", json={"version": r["version"], "comment": "x"})
        assert res.status_code == 403, path
    assert (await site.post(f"/rfqs/{r['id']}/pm-approve", json={"version": r["version"]})).status_code in (404, 405)  # step removed


async def test_finance_fields_hidden_for_site_users(api, refs):
    site_a = await api.as_("site_a")
    notes = await site_a.ok("GET", "/issue-notes?limit=5&status=PARTIALLY_RECEIVED")
    assert notes and all(n["total_value"] is None for n in notes)
    detail = await site_a.ok("GET", f"/issue-notes/{notes[0]['id']}")
    assert all(i["rate"] is None and i["amount"] is None for i in detail["items"])
    reports = {r["key"] for r in await site_a.ok("GET", "/reports")}
    assert "budget-vs-actual" not in reports and "site-stock" in reports
    manager = await api.as_("store_manager")
    d = await manager.ok("GET", f"/issue-notes/{notes[0]['id']}")
    assert all(i["rate"] is not None for i in d["items"])


async def test_allocation_exceeded_needs_override(api, refs, plan):
    ids = await plan(
        [{"name": "Alloc", "planned": 1000, "rate": 5, "allocations": [{"project_id": refs["projects"]["AVH"], "allocated_qty": 100}]}]
    )
    site_a = await api.as_("site_a")
    r = await site_a.ok(
        "POST",
        "/rfqs",
        json={"site_id": refs["sites"]["AVH-TA"]["id"], "submit": True, "items": [{"material_id": ids["Alloc"], "requested_qty": 150}]},
    )
    sm = await api.as_("store_manager")
    res = await sm.post(f"/rfqs/{r['id']}/store-approve", json={"version": r["version"]})
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALLOCATION_EXCEEDED"
    ok = await sm.ok("POST", f"/rfqs/{r['id']}/store-approve", json={"version": r["version"], "override_allocation": True})
    assert ok["status"] == "STORE_APPROVED"
    assert "[Allocation override]" in ok["approvals"][-1]["comment"]


async def test_cancel_posted_grn_reverses_stock(api, refs, plan):
    ids = await plan([{"name": "Reverse", "planned": 1000, "rate": 5}])
    g = await grn(api, refs, [{"material_id": ids["Reverse"], "received_qty": 200, "rate": 5}])
    sm = await api.as_("store_manager")
    central = refs["stores"]["CS-01"]
    c = await sm.ok("POST", f"/grns/{g['id']}/cancel", json={"reason": "Wrong vendor", "version": g["version"]})
    assert c["status"] == "CANCELLED"
    bal = await sm.ok("GET", f"/stock/balances?store_id={central}&material_id={ids['Reverse']}&include_zero=true")
    assert bal[0]["qty"] == 0
    ledger = await sm.ok("GET", f"/stock/ledger?store_id={central}&material_id={ids['Reverse']}")
    assert [r["txn_type"] for r in ledger] == ["REVERSAL", "INWARD"]


async def test_cannot_reverse_grn_after_issue(api, refs, plan):
    ids = await plan([{"name": "NoRev", "planned": 1000, "rate": 5}])
    g = await grn(api, refs, [{"material_id": ids["NoRev"], "received_qty": 100, "rate": 5}])
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": ids["NoRev"], "requested_qty": 60}])
    await issue(api, rfq)
    r = await (await api.as_("store_manager")).post(f"/grns/{g['id']}/cancel", json={"reason": "Too late", "version": g["version"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK"


async def test_cancel_issued_note_restores_central_and_rfq(api, refs, plan):
    ids = await plan([{"name": "IssueRev", "planned": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["IssueRev"], "received_qty": 100, "rate": 5}])
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": ids["IssueRev"], "requested_qty": 40}])
    note = await issue(api, rfq)
    sm = await api.as_("store_manager")
    c = await sm.ok("POST", f"/issue-notes/{note['id']}/cancel", json={"reason": "Vehicle breakdown", "version": note["version"]})
    assert c["status"] == "CANCELLED"
    r = await sm.ok("GET", f"/rfqs/{rfq['id']}")
    assert r["status"] == "STORE_APPROVED" and r["items"][0]["issued_qty"] == 0
    bal = await sm.ok("GET", f"/stock/balances?store_id={refs['stores']['CS-01']}&material_id={ids['IssueRev']}")
    assert bal[0]["qty"] == 100


async def test_optimistic_locking(api, refs, plan):
    ids = await plan([{"name": "Lock", "planned": 100, "rate": 5}])
    site_a = await api.as_("site_a")
    r = await site_a.ok(
        "POST", "/rfqs", json={"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": ids["Lock"], "requested_qty": 1}]}
    )
    await site_a.ok("POST", f"/rfqs/{r['id']}/submit", json={"version": r["version"]})
    stale = await site_a.post(f"/rfqs/{r['id']}/cancel", json={"version": r["version"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_idempotency_key_replays(api, refs, plan):
    ids = await plan([{"name": "Idem", "planned": 100, "rate": 5}])
    site_a = await api.as_("site_a")
    key = uuid.uuid4().hex
    body = {"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": ids["Idem"], "requested_qty": 2}]}
    first = await site_a.ok("POST", "/rfqs", json=body, headers={"Idempotency-Key": key})
    second = await site_a.ok("POST", "/rfqs", json=body, headers={"Idempotency-Key": key})
    assert first["id"] == second["id"] and first["request_no"] == second["request_no"]


async def test_concurrent_consumption_never_goes_negative(api, refs, plan):
    ids = await plan([{"name": "Race", "planned": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["Race"], "received_qty": 500, "rate": 5}])
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": ids["Race"], "requested_qty": 500}])
    note = await issue(api, rfq)
    site_a = await api.as_("site_a")
    await site_a.ok("POST", f"/issue-notes/{note['id']}/receive", json={"version": note["version"]})
    body = {"site_id": refs["sites"]["AVH-TA"]["id"], "items": [{"material_id": ids["Race"], "qty": 100}]}
    results = await asyncio.gather(*[site_a.post("/consumptions", json=body) for _ in range(8)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 5 and codes.count(409) == 3, codes
    stock = {s["material"]["id"]: s for s in await site_a.ok("GET", f"/site/stock?site_id={refs['sites']['AVH-TA']['id']}")}
    assert stock[ids["Race"]]["qty"] == 0


async def test_ledger_is_append_only(app):
    conn = await asyncpg.connect(TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("UPDATE stock_ledger SET qty_in = qty_in + 1 WHERE id = (SELECT min(id) FROM stock_ledger)")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("DELETE FROM stock_ledger")
    finally:
        await conn.close()


async def test_returns_flow(api, refs, plan):
    ids = await plan([{"name": "Ret", "planned": 1000, "rate": 8}])
    await grn(api, refs, [{"material_id": ids["Ret"], "received_qty": 100, "rate": 8}])
    rfq = await approved_rfq(api, refs, "AVH-TA", [{"material_id": ids["Ret"], "requested_qty": 50}])
    note = await issue(api, rfq)
    site_a = await api.as_("site_a")
    await site_a.ok("POST", f"/issue-notes/{note['id']}/receive", json={"version": note["version"]})
    ret = await site_a.ok(
        "POST",
        "/returns",
        json={"site_id": refs["sites"]["AVH-TA"]["id"], "reason": "Excess", "items": [{"material_id": ids["Ret"], "qty": 20}]},
    )
    ret = await site_a.ok("POST", f"/returns/{ret['id']}/dispatch", json={"version": ret["version"]})
    ret = await (await api.as_("store_manager")).ok("POST", f"/returns/{ret['id']}/receive", json={"version": ret["version"]})
    assert ret["status"] == "RECEIVED"
    sm = await api.as_("store_manager")
    bal = await sm.ok("GET", f"/stock/balances?store_id={refs['stores']['CS-01']}&material_id={ids['Ret']}")
    assert bal[0]["qty"] == 70


async def test_physical_count_adjustment(api, refs, plan):
    ids = await plan([{"name": "Count", "planned": 1000, "rate": 10}])
    await grn(api, refs, [{"material_id": ids["Count"], "received_qty": 100, "rate": 10}])
    sm = await api.as_("store_manager")
    a = await sm.ok(
        "POST",
        "/stock-adjustments",
        json={
            "store_id": refs["stores"]["CS-01"],
            "reason": "PHYSICAL_COUNT",
            "items": [{"material_id": ids["Count"], "physical_qty": 97}],
        },
    )
    a = await sm.ok("POST", f"/stock-adjustments/{a['id']}/submit", json={"version": a["version"]})
    a = await sm.ok("POST", f"/stock-adjustments/{a['id']}/approve", json={"version": a["version"]})
    assert a["items"][0]["system_qty"] == 100 and a["items"][0]["qty_delta"] == -3
    bal = await sm.ok("GET", f"/stock/balances?store_id={refs['stores']['CS-01']}&material_id={ids['Count']}")
    assert bal[0]["qty"] == 97


async def test_one_approved_boq_per_fy(api):
    sm = await api.as_("store_manager")
    current = await sm.ok("GET", "/boqs/current")
    b = await sm.ok(
        "POST",
        "/boqs",
        json={
            "title": "Duplicate plan",
            "items": [{"material_id": current["items"][0]["material"]["id"], "planned_qty": 1, "estimated_rate": 1}],
        },
    )
    r = await sm.post(f"/boqs/{b['id']}/submit", json={"version": b["version"]})  # submit tries to approve → blocked
    assert r.status_code == 409 and r.json()["error"]["code"] == "BOQ_EXISTS_FOR_FY"


async def test_exports_and_reports_respect_permissions(api):
    manager = await api.as_("store_manager")
    r = await manager.get("/reports/stock-summary/export")
    assert r.status_code == 200 and r.content[:2] == b"PK"  # xlsx is a zip
    site = await api.as_("site_a")
    assert (await site.get("/reports/stock-summary/export")).status_code == 403
    stock = await site.ok("GET", "/reports/stock-summary")
    assert "value" not in {c["key"] for c in stock["columns"]}


async def test_issue_note_pdf(api):
    sm = await api.as_("store_manager")
    notes = await sm.ok("GET", "/issue-notes?limit=1")
    r = await sm.get(f"/issue-notes/{notes[0]['id']}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


async def test_attachment_upload_validates_content(api, refs):
    sk = await api.as_("store_manager")
    g = (await sk.ok("GET", "/grns?limit=1"))[0]
    files = {"file": ("invoice.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")}
    up = await sk.ok("POST", "/attachments", data={"entity_type": "grn", "entity_id": g["id"]}, files=files)
    assert up["mime"] == "image/png"
    bad = await sk.post(
        "/attachments", data={"entity_type": "grn", "entity_id": g["id"]}, files={"file": ("x.png", b"<script>", "image/png")}
    )
    assert bad.status_code == 415
    site_a = await api.as_("site_a")
    assert (await site_a.get(f"/attachments?entity_type=grn&entity_id={g['id']}")).status_code == 404


async def test_dashboard_kpis_visible_to_site_users(api):
    """Regression: the finance scrub must not blank non-finance KPI values (field is named `value`)."""
    site_a = await api.as_("site_a")
    d = await site_a.ok("GET", "/dashboard")
    kpis = {k["key"]: k for k in d["kpis"]}
    assert kpis["open_rfqs"]["value"] is not None
    assert "boq_value" not in kpis and "stock_value" not in kpis  # finance KPIs omitted entirely
    assert any(t["key"] == "issue_receive" for t in d["tasks"])


async def test_category_defaults_round_trip(api, refs, code):
    sm = await api.as_("store_manager")
    cat = await sm.ok(
        "POST",
        "/masters/categories",
        json={
            "code": f"ST{code}"[:12],
            "name": "Steel test",
            "default_uom_id": refs["uoms"]["KG"],
            "default_hsn_code": "7214",
            "default_gst_rate": 18,
        },
    )
    assert cat["default_uom_id"] == refs["uoms"]["KG"] and cat["default_hsn_code"] == "7214" and cat["default_gst_rate"] == 18
    upd = await sm.ok("PATCH", f"/masters/categories/{cat['id']}", json={"default_gst_rate": 12, "version": cat["version"]})
    assert upd["default_gst_rate"] == 12


async def test_submit_approves_in_one_step_for_the_store_manager(api, plan):
    await plan([{"name": "AutoApprove", "planned": 10, "rate": 1}])  # plan() asserts the revision went APPROVED on submit
    sm = await api.as_("store_manager")
    cur = await sm.ok("GET", "/boqs/current")
    assert cur["status"] == "APPROVED" and cur["approvals"][-1]["action"] == "APPROVED"
