"""Role × endpoint matrix. A role lacking the permission must get 403; a role holding it must not.
Mutating endpoints are probed with an empty body: authorised callers get 422 (validation), others 403."""

import pytest

from app.services.permissions import ROLES

ROLE_USER = {
    "SUPER_ADMIN": "admin",
    "MANAGEMENT": "director",
    "PROCUREMENT": "procurement",
    "STORE_MANAGER": "store_manager",
    "STORE_KEEPER": "store_keeper",
    "PROJECT_MANAGER": "pm_heights",
    "SITE_ENGINEER": "site_a",
    "AUDITOR": "auditor",
}

ENDPOINTS = [
    ("GET", "/admin/users", ("users.manage",)),
    ("GET", "/admin/roles", ("roles.manage", "users.manage")),
    ("GET", "/admin/audit-logs", ("audit.read",)),
    ("GET", "/masters/materials", ("masters.read",)),
    ("POST", "/masters/materials", ("masters.manage",)),
    ("POST", "/masters/vendors", ("vendors.manage",)),
    ("GET", "/market-rates", ("market.read",)),
    ("POST", "/market-rates", ("market.manage",)),
    ("GET", "/boqs", ("boq.read",)),
    ("POST", "/boqs", ("boq.create",)),
    ("GET", "/grns", ("grn.read",)),
    ("POST", "/grns", ("grn.create",)),
    ("GET", "/rfqs", ("rfq.read",)),
    ("POST", "/rfqs", ("rfq.create",)),
    ("GET", "/issue-notes", ("issue.read",)),
    ("POST", "/issue-notes", ("issue.create",)),
    ("POST", "/consumptions", ("site.consume",)),
    ("POST", "/returns", ("site.return",)),
    ("GET", "/stock/balances", ("stock.read",)),
    ("POST", "/stock-adjustments", ("adjustment.create",)),
    ("GET", "/reports", ("reports.read",)),
    ("GET", "/dashboard", ("reports.read",)),
    ("GET", "/reports/stock-summary/export", ("export",)),
]

CASES = [(role, m, url, perms) for role in ROLE_USER for (m, url, perms) in ENDPOINTS]


@pytest.mark.parametrize("role,method,url,perms", CASES, ids=[f"{r}-{m}-{u}" for r, m, u, _ in CASES])
async def test_permission_matrix(api, role, method, url, perms):
    granted = set(ROLES[role][3])
    client = await api.as_(ROLE_USER[role])
    r = await client.req(method, url, json={} if method == "POST" else None)
    allowed = any(p in granted for p in perms)
    if allowed:
        assert r.status_code != 403, f"{role} should reach {method} {url}: {r.text}"
        assert r.status_code in (200, 201, 422), r.text
    else:
        assert r.status_code == 403, f"{role} must be denied {method} {url}, got {r.status_code}"


async def test_unauthenticated_is_rejected(client):
    for _, url, _ in ENDPOINTS:
        r = await client.get(f"/api/v1{url}")
        assert r.status_code in (401, 405)
