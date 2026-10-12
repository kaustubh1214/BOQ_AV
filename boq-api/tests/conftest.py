"""Test harness: a throw-away `boq_test` database, migrated with Alembic and filled by the demo seed
(which itself drives the API as every role). Tests then talk to the app in-process over httpx."""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
_env = dotenv_values(ROOT.parent / ".env")
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or _env.get("TEST_DATABASE_URL")
assert TEST_DB_URL and TEST_DB_URL.rstrip("/").endswith("_test"), "TEST_DATABASE_URL must point at a *_test database"
os.environ["DATABASE_URL"] = TEST_DB_URL  # must happen before the app is imported
os.environ["REDIS_URL"] = ""
os.environ["UPLOAD_DIR"] = str(ROOT / ".pytest_uploads")

import asyncpg  # noqa: E402
import httpx  # noqa: E402

DEMO_PASSWORD = _env.get("DEMO_PASSWORD") or os.environ.get("DEMO_PASSWORD")
ADMIN_EMAIL = (_env.get("SEED_ADMIN_EMAIL") or "").lower()
ADMIN_PASSWORD = _env.get("SEED_ADMIN_PASSWORD")

USERS = {
    "admin": ADMIN_EMAIL,  # the one Super Admin
    "store_manager": "store.manager@avgroup.in",
    "site_a": "site.towera@avgroup.in",
    "site_b": "site.towerb@avgroup.in",
    "site_p1": "site.phase1@avgroup.in",
}


def pytest_collection_modifyitems(items):
    # One event loop for the whole session (the app's engine/pool is created once).
    for item in items:
        if asyncio.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"))


async def _recreate_db() -> None:
    raw = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    admin_url, dbname = raw.rsplit("/", 1)
    conn = await asyncpg.connect(f"{admin_url}/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def app():
    await _recreate_db()
    env = {**os.environ, "ALEMBIC_DATABASE_URL": TEST_DB_URL}
    await asyncio.to_thread(
        subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True
    )

    from app.core.database import SessionLocal
    from app.main import app as fastapi_app
    from app.seed.base import seed_base
    from app.seed.demo import seed_demo

    async with SessionLocal() as db:
        await seed_base(db)
    await seed_demo(fastapi_app)
    yield fastapi_app
    from app.core.database import engine

    await engine.dispose()


class As:
    """Authenticated client for one user."""

    def __init__(self, client: httpx.AsyncClient, token: str):
        self.c = client
        self.h = {"Authorization": f"Bearer {token}"}

    async def req(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {**self.h, **kw.pop("headers", {})}
        return await self.c.request(method, f"/api/v1{url}", headers=headers, **kw)

    async def get(self, url, **kw):
        return await self.req("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.req("POST", url, **kw)

    async def put(self, url, **kw):
        return await self.req("PUT", url, **kw)

    async def ok(self, method: str, url: str, expect: int | tuple = (200, 201), **kw) -> dict:
        r = await self.req(method, url, **kw)
        exp = (expect,) if isinstance(expect, int) else expect
        assert r.status_code in exp, f"{method} {url} → {r.status_code}: {r.text}"
        return r.json()["data"]


class Api:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._tokens: dict[str, str] = {}

    async def as_(self, who: str) -> As:
        if who not in self._tokens:
            email = USERS[who]
            password = ADMIN_PASSWORD if who == "admin" else DEMO_PASSWORD
            r = await self.client.post("/api/v1/auth/token", json={"email": email, "password": password})
            assert r.status_code == 200, r.text
            self._tokens[who] = r.json()["data"]["access_token"]
        return As(self.client, self._tokens[who])


@pytest.fixture(scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def api(client) -> Api:
    return Api(client)


@pytest.fixture(scope="session")
async def refs(api):
    """IDs of demo masters."""
    admin = await api.as_("admin")
    projects = {p["code"]: p["id"] for p in await admin.ok("GET", "/masters/projects?limit=100")}
    sites = {s["code"]: s for s in await admin.ok("GET", "/masters/sites?limit=100")}
    stores = {s["code"]: s["id"] for s in await admin.ok("GET", "/masters/stores?limit=100")}
    vendors = {v["code"]: v["id"] for v in await admin.ok("GET", "/masters/vendors?limit=100")}
    cats = {c["code"]: c["id"] for c in await admin.ok("GET", "/masters/categories?limit=100")}
    uoms = {u["code"]: u["id"] for u in await admin.ok("GET", "/masters/uoms?limit=100")}
    return {"projects": projects, "sites": sites, "stores": stores, "vendors": vendors, "cats": cats, "uoms": uoms}


@pytest.fixture
async def plan(api, refs):
    """Factory: create fresh materials and put them into the approved BOQ via a revision
    (the Store Manager revises and submits; submitting approves). Returns {name: material_id}."""

    async def _plan(lines: list[dict]) -> dict[str, str]:
        admin = await api.as_("admin")
        sm = await api.as_("store_manager")
        ids = {}
        for ln in lines:
            code = f"T-{ln['name'][:10].upper()}-{uuid.uuid4().hex[:6]}"
            m = await admin.ok(
                "POST",
                "/masters/materials",
                json={
                    "code": code,
                    "name": f"{ln['name']} {code[-6:]}",
                    "category_id": refs["cats"]["CEM"],
                    "base_uom_id": refs["uoms"][ln.get("uom", "KG")],
                    "gst_rate": 18,
                    "reorder_level": ln.get("reorder", 0),
                },
            )
            ids[ln["name"]] = m["id"]
        current = await sm.ok("GET", "/boqs/current")
        rev = await sm.ok("POST", f"/boqs/{current['id']}/revise")
        items = [
            {
                "material_id": i["material"]["id"],
                "planned_qty": i["planned_qty"],
                "estimated_rate": i["estimated_rate"],
                "allocations": [
                    {"project_id": a["project_id"], "site_id": a["site_id"], "allocated_qty": a["allocated_qty"]} for a in i["allocations"]
                ],
            }
            for i in rev["items"]
        ]
        for ln in lines:
            items.append(
                {
                    "material_id": ids[ln["name"]],
                    "planned_qty": ln["planned"],
                    "estimated_rate": ln["rate"],
                    "allocations": ln.get("allocations", []),
                }
            )
        rev = await sm.ok("PUT", f"/boqs/{rev['id']}", json={"title": rev["title"], "items": items, "version": rev["version"]})
        rev = await sm.ok("POST", f"/boqs/{rev['id']}/submit", json={"version": rev["version"]})  # Store Manager: submit = approve
        assert rev["status"] == "APPROVED"
        return ids

    return _plan


@pytest.fixture
def code() -> str:
    return uuid.uuid4().hex[:8]
