"""The three-role model: one Super Admin, Store Managers (company admin) and Site Users."""

import uuid

import asyncpg
import pytest

from tests.conftest import TEST_DB_URL
from tests.test_flow import grn

STRONG = "Str0ng!Passw0rd"


async def _roles(api) -> dict[str, str]:
    admin = await api.as_("admin")
    return {r["code"]: r["id"] for r in await admin.ok("GET", "/admin/roles")}


def _email() -> str:
    return f"u-{uuid.uuid4().hex[:8]}@avgroup.in"


def _new_user_body(role_id: str, scope_type: str, scope_id: str | None = None, **extra) -> dict:
    a = {"role_id": role_id, "scope_type": scope_type}
    if scope_id:
        a["scope_id"] = scope_id
    return {"name": "Test User", "email": _email(), "password": STRONG, "assignments": [a], **extra}


async def test_only_three_system_roles(api):
    roles = await _roles(api)
    assert {"SUPER_ADMIN", "STORE_MANAGER", "SITE_USER"} <= set(roles)
    assert not {"MANAGEMENT", "PROCUREMENT", "STORE_KEEPER", "PROJECT_MANAGER", "AUDITOR", "SITE_ENGINEER"} & set(roles)


async def test_super_admin_cannot_be_assigned_through_the_api(api):
    admin, sm = await api.as_("admin"), await api.as_("store_manager")
    roles = await _roles(api)
    r = await admin.post("/admin/users", json=_new_user_body(roles["SUPER_ADMIN"], "GLOBAL"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SINGLE_SUPER_ADMIN"
    r = await sm.post("/admin/users", json=_new_user_body(roles["SUPER_ADMIN"], "GLOBAL"))
    assert r.status_code in (403, 409)


async def test_database_refuses_a_second_super_admin_and_its_removal(app):
    conn = await asyncpg.connect(TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        uid = await conn.fetchval("SELECT id FROM users WHERE email = $1", "store.manager@avgroup.in")
        with pytest.raises(asyncpg.UniqueViolationError, match="only one Super Admin"):
            await conn.execute(
                "INSERT INTO user_role_assignments (id, user_id, role_id, scope_type) "
                "SELECT gen_random_uuid(), $1, id, 'GLOBAL' FROM roles WHERE code = 'SUPER_ADMIN'",
                uid,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError, match="cannot be removed"):
            await conn.execute("DELETE FROM user_role_assignments WHERE role_id = (SELECT id FROM roles WHERE code = 'SUPER_ADMIN')")
    finally:
        await conn.close()


async def test_super_admin_is_protected_and_hidden(api):
    admin, sm = await api.as_("admin"), await api.as_("store_manager")
    me = await admin.ok("GET", "/me")
    r = await admin.req("PATCH", f"/admin/users/{me['id']}", json={"is_active": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROTECTED_ACCOUNT"
    listed = await sm.ok("GET", "/admin/users?limit=200")
    assert me["id"] not in {u["id"] for u in listed}
    assert (await sm.get(f"/admin/users/{me['id']}")).status_code == 404
    assert (await sm.post(f"/admin/users/{me['id']}/reset-password", json={"password": STRONG})).status_code == 404
    assert me["id"] in {u["id"] for u in await admin.ok("GET", "/admin/users?limit=200")}


async def test_store_manager_manages_site_users_only(api, refs):
    sm, admin = await api.as_("store_manager"), await api.as_("admin")
    roles = await _roles(api)
    assert {r["code"] for r in await sm.ok("GET", "/admin/roles")} == {"SITE_USER"}  # can only hand out the Site User role
    u = await sm.ok("POST", "/admin/users", json=_new_user_body(roles["SITE_USER"], "SITE", refs["sites"]["AVH-TB"]["id"]))
    assert u["assignments"][0]["role_code"] == "SITE_USER"
    # ...but not another Store Manager
    assert (await sm.post("/admin/users", json=_new_user_body(roles["STORE_MANAGER"], "GLOBAL"))).status_code == 403
    # a Site User must be tied to a site
    assert (await sm.post("/admin/users", json=_new_user_body(roles["SITE_USER"], "GLOBAL"))).status_code == 403
    # The Super Admin creates a Store Manager; peers cannot edit it
    peer = await admin.ok("POST", "/admin/users", json=_new_user_body(roles["STORE_MANAGER"], "GLOBAL"))
    assert (await sm.post(f"/admin/users/{peer['id']}/reset-password", json={"password": STRONG})).status_code == 403
    assert (await sm.req("PATCH", f"/admin/users/{peer['id']}", json={"is_active": False})).status_code == 403
    # Site users are fully manageable by the Store Manager
    assert (await sm.post(f"/admin/users/{u['id']}/reset-password", json={"password": "An0ther!Passw0rd"})).status_code == 200
    r = await sm.req("PATCH", f"/admin/users/{u['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["data"]["is_active"] is False


async def test_store_manager_holds_the_admin_toolbox(api, refs, code):
    sm = await api.as_("store_manager")
    m = await sm.ok(
        "POST",
        "/masters/materials",
        json={
            "code": f"SM-{code}",
            "name": "Made by the Store Manager",
            "category_id": refs["cats"]["CEM"],
            "base_uom_id": refs["uoms"]["KG"],
        },
    )
    assert m["id"]
    p = await sm.ok("POST", "/masters/projects", json={"code": f"P{code}"[:10], "name": "New Project", "city": "Pune"})
    s = await sm.ok("POST", "/masters/sites", json={"code": f"S{code}"[:10], "name": "New Site", "project_id": p["id"]})
    assert s["id"]
    assert (await sm.get("/admin/audit-logs?limit=5")).status_code == 200
    me = await sm.ok("GET", "/me")
    assert "finance.view" in me["permissions"]
    # the system-level controls stay with the Super Admin
    assert (await sm.get("/admin/system")).status_code == 403
    assert (await sm.post("/admin/roles", json={"code": "HACK", "name": "Hack", "permissions": []})).status_code == 403


async def test_super_admin_system_overview(api):
    admin = await api.as_("admin")
    d = await admin.ok("GET", "/admin/system")
    assert d["users_by_role"]["SUPER_ADMIN"] == 1
    assert d["users_by_role"]["STORE_MANAGER"] >= 1


async def test_approve_and_issue_in_one_step(api, refs, plan):
    ids = await plan([{"name": "OneClick", "planned": 1000, "rate": 5}])
    await grn(api, refs, [{"material_id": ids["OneClick"], "received_qty": 500, "rate": 5}])
    site, sm = await api.as_("site_a"), await api.as_("store_manager")
    r = await site.ok(
        "POST",
        "/rfqs",
        json={"site_id": refs["sites"]["AVH-TA"]["id"], "submit": True, "items": [{"material_id": ids["OneClick"], "requested_qty": 200}]},
    )
    assert r["status"] == "SUBMITTED"
    queue = await sm.ok("GET", "/rfqs?queue=store&limit=200")
    assert r["id"] in {x["id"] for x in queue}
    item = r["items"][0]
    note = await sm.ok(
        "POST",
        f"/rfqs/{r['id']}/approve-and-issue",
        json={
            "version": r["version"],
            "override_allocation": True,
            "vehicle_no": "MH12XY1234",
            "driver_name": "Ganesh",
            "items": [{"item_id": item["id"], "approved_qty": 150}],  # reduced from 200
        },
    )
    assert note["status"] == "ISSUED" and note["items"][0]["issued_qty"] == 150 and note["vehicle_no"] == "MH12XY1234"
    after = await sm.ok("GET", f"/rfqs/{r['id']}")
    assert after["status"] == "ISSUED"
    bal = await sm.ok("GET", f"/stock/balances?store_id={refs['stores']['CS-01']}&material_id={ids['OneClick']}")
    assert bal[0]["qty"] == 350
    got = await site.ok("POST", f"/issue-notes/{note['id']}/receive", json={"version": note["version"]})
    assert got["status"] == "RECEIVED"


async def test_approve_and_issue_is_atomic_when_stock_is_short(api, refs, plan):
    ids = await plan([{"name": "NoStock", "planned": 1000, "rate": 5}])
    site, sm = await api.as_("site_a"), await api.as_("store_manager")
    r = await site.ok(
        "POST",
        "/rfqs",
        json={"site_id": refs["sites"]["AVH-TA"]["id"], "submit": True, "items": [{"material_id": ids["NoStock"], "requested_qty": 10}]},
    )
    res = await sm.post(f"/rfqs/{r['id']}/approve-and-issue", json={"version": r["version"], "override_allocation": True})
    assert res.status_code == 409 and res.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    again = await sm.ok("GET", f"/rfqs/{r['id']}")
    assert again["status"] == "SUBMITTED"  # nothing half-done: the approval rolled back with the failed issue


async def test_cli_resets_and_unlocks_the_super_admin(api, client):
    from app.cli import reset_superadmin
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    assert await reset_superadmin(ADMIN_PASSWORD) == ADMIN_EMAIL  # same password: keeps the other tests logged in
    r = await client.post("/api/v1/auth/token", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    api._tokens.pop("admin", None)  # the reset signed the old token out
    with pytest.raises(ValueError):
        await reset_superadmin("weak")
