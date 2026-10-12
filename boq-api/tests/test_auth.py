import uuid

from app.core.config import settings
from tests.conftest import DEMO_PASSWORD

STRONG = "Str0ng!Passw0rd"


async def _new_user(api, refs, *, role="SITE_USER", scope_type="SITE", scope_id=None) -> tuple[str, str]:
    admin = await api.as_("admin")
    roles = {r["code"]: r["id"] for r in await admin.ok("GET", "/admin/roles")}
    email = f"user-{uuid.uuid4().hex[:8]}@avgroup.in"
    await admin.ok(
        "POST",
        "/admin/users",
        json={
            "name": "Test User",
            "email": email,
            "password": STRONG,
            "assignments": [{"role_id": roles[role], "scope_type": scope_type, "scope_id": scope_id or refs["sites"]["AVH-TA"]["id"]}],
        },
    )
    return email, STRONG


async def test_web_login_refresh_rotation_and_reuse_detection(client):
    r = await client.post("/api/v1/auth/login", json={"email": "store.manager@avgroup.in", "password": DEMO_PASSWORD})
    assert r.status_code == 200
    first_cookie = r.cookies.get(settings.REFRESH_COOKIE_NAME)
    assert first_cookie and r.json()["data"]["refresh_token"] is None  # never exposed to JS on web
    client.cookies.clear()

    r2 = await client.post("/api/v1/auth/refresh", cookies={settings.REFRESH_COOKIE_NAME: first_cookie})
    assert r2.status_code == 200
    second_cookie = r2.cookies.get(settings.REFRESH_COOKIE_NAME)
    assert second_cookie and second_cookie != first_cookie
    client.cookies.clear()

    # Replaying the rotated token revokes the whole family, including the newest token.
    reuse = await client.post("/api/v1/auth/refresh", cookies={settings.REFRESH_COOKIE_NAME: first_cookie})
    assert reuse.status_code == 401
    client.cookies.clear()
    after = await client.post("/api/v1/auth/refresh", cookies={settings.REFRESH_COOKIE_NAME: second_cookie})
    assert after.status_code == 401
    client.cookies.clear()


async def test_mobile_token_flow(client):
    r = await client.post(
        "/api/v1/auth/token",
        json={"email": "store.manager@avgroup.in", "password": DEMO_PASSWORD, "platform": "android", "device_name": "Pixel"},
    )
    data = r.json()["data"]
    assert data["refresh_token"]
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r2.status_code == 200 and r2.json()["data"]["refresh_token"] != data["refresh_token"]
    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {r2.json()['data']['access_token']}"})
    assert me.json()["data"]["company_name"] == "AV Group"


async def test_invalid_credentials_and_lockout(client, api, refs, monkeypatch):
    email, password = await _new_user(api, refs)
    monkeypatch.setattr(settings, "LOCKOUT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_MIN", 100)
    for _ in range(3):
        r = await client.post("/api/v1/auth/token", json={"email": email, "password": "wrong-password"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    locked = await client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert locked.status_code == 423
    unknown = await client.post("/api/v1/auth/token", json={"email": "nobody@avgroup.in", "password": "x"})
    assert unknown.status_code == 401 and unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"  # no user enumeration


async def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_MIN", 2)
    email = f"ratelimit-{uuid.uuid4().hex[:6]}@avgroup.in"
    codes = [(await client.post("/api/v1/auth/token", json={"email": email, "password": "x"})).status_code for _ in range(4)]
    assert codes[:2] == [401, 401] and codes[2:] == [429, 429]


async def test_must_change_password_gate(client, api, refs):
    email, password = await _new_user(api, refs)
    tok = (await client.post("/api/v1/auth/token", json={"email": email, "password": password})).json()["data"]
    assert tok["must_change_password"] is True
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    blocked = await client.get("/api/v1/rfqs", headers=h)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert (await client.get("/api/v1/me", headers=h)).status_code == 200
    weak = await client.post("/api/v1/auth/change-password", headers=h, json={"current_password": password, "new_password": "short"})
    assert weak.status_code == 422
    changed = await client.post(
        "/api/v1/auth/change-password", headers=h, json={"current_password": password, "new_password": "N3w!Passw0rd#"}
    )
    assert changed.status_code == 200
    assert (await client.get("/api/v1/rfqs", headers=h)).status_code == 401  # old token revoked
    h2 = {"Authorization": f"Bearer {changed.json()['data']['access_token']}"}
    assert (await client.get("/api/v1/rfqs", headers=h2)).status_code == 200


async def test_logout_all_invalidates_access_tokens(client, api, refs):
    email, password = await _new_user(api, refs)
    admin = await api.as_("admin")
    users = await admin.ok("GET", f"/admin/users?q={email}")
    await admin.ok("POST", f"/admin/users/{users[0]['id']}/reset-password", json={"password": STRONG})
    tok = (await client.post("/api/v1/auth/token", json={"email": email, "password": STRONG})).json()["data"]
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    changed = await client.post(
        "/api/v1/auth/change-password", headers=h, json={"current_password": STRONG, "new_password": "An0ther!Pass"}
    )
    h = {"Authorization": f"Bearer {changed.json()['data']['access_token']}"}
    assert (await client.post("/api/v1/auth/logout-all", headers=h)).status_code == 200
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401


async def test_no_privilege_escalation(api, refs):
    admin = await api.as_("admin")
    roles = {r["code"]: r["id"] for r in await admin.ok("GET", "/admin/roles")}
    # A custom role may not include permissions the creator lacks; system roles are immutable.
    r = await admin.put(f"/admin/roles/{roles['SITE_USER']}/permissions", json={"permissions": ["users.manage"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SYSTEM_ROLE"
    # Admin cannot change their own assignments.
    me = await admin.ok("GET", "/me")
    r = await admin.put(f"/admin/users/{me['id']}/assignments", json=[])
    assert r.status_code == 403
    # Site Users can't reach user management at all.
    assert (await (await api.as_("site_a")).get("/admin/users")).status_code == 403


async def test_audit_trail_records_changes(api):
    manager = await api.as_("store_manager")
    logs = await manager.ok("GET", "/admin/audit-logs?entity_type=grn&limit=20")
    assert {"CREATE", "POST"} <= {x["action"] for x in logs}
    assert all(x["user_name"] for x in logs if x["action"] == "POST")
