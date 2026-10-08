"""Demo data for AV Group, created by driving the real API as each role (so every rule — maker-checker,
scopes, stock engine — is exercised exactly as in production). Reproduces the notebook sketch:
Cement 1,00,000 kg planned → 15,000 kg inward → issue to Tower A → receipt with damage → daily consumption.

Users get DEMO_PASSWORD from .env. Safe to re-run: skipped if a BOQ already exists.
"""

import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Boq, Role, User, UserRoleAssignment
from app.models.enums import ScopeType
from app.services.common import today

log = logging.getLogger("boq.seed.demo")

DEMO_USERS = [
    # email, name, role, scope kind, scope key
    ("director@avgroup.in", "Anil Verma (Director)", "MANAGEMENT", ScopeType.GLOBAL, None),
    ("procurement@avgroup.in", "Priya Kulkarni (Procurement)", "PROCUREMENT", ScopeType.GLOBAL, None),
    ("store.manager@avgroup.in", "Suresh Patil (Store Manager)", "STORE_MANAGER", ScopeType.STORE, "CS-01"),
    ("store.keeper@avgroup.in", "Ramesh Jadhav (Store Keeper)", "STORE_KEEPER", ScopeType.STORE, "CS-01"),
    ("pm.heights@avgroup.in", "Meera Joshi (PM, AV Heights)", "PROJECT_MANAGER", ScopeType.PROJECT, "AVH"),
    ("pm.greens@avgroup.in", "Rahul Deshmukh (PM, AV Greens)", "PROJECT_MANAGER", ScopeType.PROJECT, "AVG"),
    ("site.towera@avgroup.in", "Vikas Shinde (Site Eng., Tower A)", "SITE_ENGINEER", ScopeType.SITE, "AVH-TA"),
    ("site.towerb@avgroup.in", "Neha Pawar (Site Eng., Tower B)", "SITE_ENGINEER", ScopeType.SITE, "AVH-TB"),
    ("site.phase1@avgroup.in", "Sanjay More (Site Eng., Phase 1)", "SITE_ENGINEER", ScopeType.SITE, "AVG-P1"),
    ("auditor@avgroup.in", "Kavita Rao (Auditor)", "AUDITOR", ScopeType.GLOBAL, None),
]


class Api:
    def __init__(self, client: httpx.AsyncClient):
        self.c = client
        self.tokens: dict[str, str] = {}

    async def login(self, email: str, password: str) -> None:
        r = await self.c.post("/api/v1/auth/token", json={"email": email, "password": password, "device_name": "seed"})
        r.raise_for_status()
        self.tokens[email] = r.json()["data"]["access_token"]

    async def call(self, who: str, method: str, url: str, **kw) -> dict:
        r = await self.c.request(method, f"/api/v1{url}", headers={"Authorization": f"Bearer {self.tokens[who]}"}, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{who} {method} {url} → {r.status_code}: {r.text}")
        return r.json()["data"]


async def _ensure_users(scope_ids: dict[str, uuid.UUID]) -> None:
    async with SessionLocal() as db:
        roles = {r.code: r.id for r in (await db.execute(select(Role))).scalars()}
        for email, name, role, scope_type, key in DEMO_USERS:
            user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
            if user is None:
                user = User(id=uuid.uuid4(), name=name, email=email, password_hash=hash_password(settings.DEMO_PASSWORD))
                db.add(user)
                await db.flush()
                db.add(
                    UserRoleAssignment(
                        user_id=user.id, role_id=roles[role], scope_type=scope_type, scope_id=scope_ids.get(key) if key else None
                    )
                )
        await db.commit()


async def seed_demo(app) -> None:
    if not settings.DEMO_PASSWORD or not settings.SEED_ADMIN_EMAIL or not settings.SEED_ADMIN_PASSWORD:
        raise SystemExit("Set DEMO_PASSWORD, SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD in .env first")
    async with SessionLocal() as db:
        if (await db.execute(select(func.count()).select_from(Boq))).scalar_one():
            log.info("demo data already present — skipping")
            return

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://seed") as client:
        api = Api(client)
        admin = settings.SEED_ADMIN_EMAIL.lower()
        await api.login(admin, settings.SEED_ADMIN_PASSWORD)

        # ---------------- masters (as admin)
        uoms = {
            u["code"]: u["id"]
            for u in (await client.get("/api/v1/masters/uoms?limit=100", headers={"Authorization": f"Bearer {api.tokens[admin]}"})).json()[
                "data"
            ]
        }
        cats = {}
        for code, name in [
            ("CEM", "Cement"),
            ("STL", "Steel"),
            ("AGG", "Aggregates & Sand"),
            ("BLK", "Blocks & Bricks"),
            ("ELE", "Electrical"),
            ("PLB", "Plumbing"),
        ]:
            cats[code] = (await api.call(admin, "POST", "/masters/categories", json={"code": code, "name": name}))["id"]
        mats = {}
        for code, name, cat, uom, hsn, gst, reorder in [
            ("CEM-OPC53", "Cement OPC 53 Grade", "CEM", "KG", "2523", 28, 5000),
            ("STL-TMT12", "TMT Bar Fe500D 12mm", "STL", "KG", "7214", 18, 2000),
            ("STL-TMT8", "TMT Bar Fe500D 8mm", "STL", "KG", "7214", 18, 1000),
            ("AGG-20", "Aggregate 20mm", "AGG", "CUM", "2517", 5, 40),
            ("SND-RIVER", "River Sand", "AGG", "CUM", "2505", 5, 30),
            ("BLK-AAC", "AAC Block 600x200x150", "BLK", "NOS", "6810", 12, 2000),
            ("ELE-WIRE25", "FR Copper Wire 2.5 sqmm", "ELE", "RMT", "8544", 18, 0),
            ("PLB-CPVC1", "CPVC Pipe 1 inch", "PLB", "RMT", "3917", 18, 0),
        ]:
            mats[code] = (
                await api.call(
                    admin,
                    "POST",
                    "/masters/materials",
                    json={
                        "code": code,
                        "name": name,
                        "category_id": cats[cat],
                        "base_uom_id": uoms[uom],
                        "hsn_code": hsn,
                        "gst_rate": gst,
                        "reorder_level": reorder,
                    },
                )
            )["id"]
        await api.call(
            admin,
            "POST",
            "/masters/uom-conversions",
            json={"material_id": mats["CEM-OPC53"], "from_uom_id": uoms["BAG"], "to_uom_id": uoms["KG"], "factor": 50},
        )

        vendors = {}
        for code, name, gstin in [
            ("V-SBCT", "Shree Balaji Cement Traders", "27AABCS1234F1Z5"),
            ("V-OSSS", "Om Sai Steel Suppliers", "27AACCO5678G1Z2"),
            ("V-GAGG", "Gajanan Aggregates", None),
            ("V-SVBM", "Siddhivinayak Building Materials", "27AADFS9012H1Z9"),
        ]:
            vendors[code] = (
                await api.call(
                    admin, "POST", "/masters/vendors", json={"code": code, "name": name, "gstin": gstin, "phone": "+91 98220 00000"}
                )
            )["id"]

        projects, sites = {}, {}
        for code, name, city in [("AVH", "AV Heights", "Pune"), ("AVG", "AV Greens", "Nashik")]:
            projects[code] = (
                await api.call(
                    admin, "POST", "/masters/projects", json={"code": code, "name": name, "city": city, "start_date": "2026-04-01"}
                )
            )["id"]
        for code, name, proj in [("AVH-TA", "Tower A", "AVH"), ("AVH-TB", "Tower B", "AVH"), ("AVG-P1", "Phase 1", "AVG")]:
            sites[code] = (
                await api.call(admin, "POST", "/masters/sites", json={"code": code, "name": name, "project_id": projects[proj]})
            )["id"]
        stores = {s["code"]: s["id"] for s in await api.call(admin, "GET", "/masters/stores?limit=100")}

        await _ensure_users({**projects, **sites, **stores})
        for email, *_ in DEMO_USERS:
            await api.login(email, settings.DEMO_PASSWORD)

        P, SM, SK, DIR = "procurement@avgroup.in", "store.manager@avgroup.in", "store.keeper@avgroup.in", "director@avgroup.in"
        TA, TB, P1 = "site.towera@avgroup.in", "site.towerb@avgroup.in", "site.phase1@avgroup.in"
        t = today()

        # ---------------- (1) market research
        for mat, vendor, rate, uom, days_ago in [
            ("CEM-OPC53", "V-SBCT", 342, "BAG", 40),
            ("CEM-OPC53", "V-SVBM", 355, "BAG", 25),
            ("CEM-OPC53", "V-SBCT", 348, "BAG", 5),
            ("STL-TMT12", "V-OSSS", 61.5, None, 20),
            ("STL-TMT12", "V-SVBM", 63, None, 12),
            ("STL-TMT8", "V-OSSS", 63.5, None, 20),
            ("AGG-20", "V-GAGG", 1420, None, 15),
            ("SND-RIVER", "V-GAGG", 2050, None, 15),
            ("BLK-AAC", "V-SVBM", 51, None, 10),
        ]:
            await api.call(
                P,
                "POST",
                "/market-rates",
                json={
                    "material_id": mats[mat],
                    "vendor_id": vendors[vendor],
                    "rate": rate,
                    "uom_id": uoms[uom] if uom else None,
                    "quoted_on": (t - timedelta(days=days_ago)).isoformat(),
                    "source": "VENDOR_QUOTE",
                },
            )

        # ---------------- (2) BOQ — central store master
        boq = await api.call(
            SM,
            "POST",
            "/boqs",
            json={
                "title": "AV Group central material plan FY 2026-27",
                "remarks": "Anticipated requirement across AV Heights and AV Greens",
                "items": [
                    {
                        "material_id": mats["CEM-OPC53"],
                        "planned_qty": 100000,
                        "estimated_rate": 7.00,
                        "allocations": [
                            {"project_id": projects["AVH"], "site_id": sites["AVH-TA"], "allocated_qty": 35000},
                            {"project_id": projects["AVH"], "site_id": sites["AVH-TB"], "allocated_qty": 25000},
                            {"project_id": projects["AVG"], "site_id": sites["AVG-P1"], "allocated_qty": 30000},
                        ],
                    },
                    {
                        "material_id": mats["STL-TMT12"],
                        "planned_qty": 50000,
                        "estimated_rate": 62,
                        "allocations": [
                            {"project_id": projects["AVH"], "allocated_qty": 32000},
                            {"project_id": projects["AVG"], "allocated_qty": 15000},
                        ],
                    },
                    {"material_id": mats["STL-TMT8"], "planned_qty": 20000, "estimated_rate": 64},
                    {"material_id": mats["AGG-20"], "planned_qty": 800, "estimated_rate": 1450},
                    {"material_id": mats["SND-RIVER"], "planned_qty": 600, "estimated_rate": 2100},
                    {"material_id": mats["BLK-AAC"], "planned_qty": 40000, "estimated_rate": 52},
                    {"material_id": mats["ELE-WIRE25"], "planned_qty": 15000, "estimated_rate": 38},
                    {"material_id": mats["PLB-CPVC1"], "planned_qty": 4000, "estimated_rate": 145},
                ],
            },
        )
        boq = await api.call(SM, "POST", f"/boqs/{boq['id']}/submit", json={"version": boq["version"]})
        boq = await api.call(
            DIR, "POST", f"/boqs/{boq['id']}/approve", json={"comment": "Approved for FY 2026-27", "version": boq["version"]}
        )

        # ---------------- (3) inward: keeper drafts, manager posts (maker-checker)
        async def grn(vendor, items, days_ago, invoice):
            g = await api.call(
                SK,
                "POST",
                "/grns",
                json={
                    "vendor_id": vendors[vendor],
                    "received_date": (t - timedelta(days=days_ago)).isoformat(),
                    "invoice_no": invoice,
                    "vehicle_no": "MH12AB1234",
                    "items": items,
                },
            )
            return await api.call(SM, "POST", f"/grns/{g['id']}/post", json={"version": g["version"]})

        await grn("V-SBCT", [{"material_id": mats["CEM-OPC53"], "received_qty": 10000, "rate": 6.80}], 12, "SBCT/2026/118")
        await grn(
            "V-SVBM",
            [
                {"material_id": mats["CEM-OPC53"], "received_qty": 5000, "rate": 7.10},
                {"material_id": mats["BLK-AAC"], "received_qty": 6200, "rejected_qty": 200, "rate": 51, "rejection_reason": "Broken edges"},
            ],
            8,
            "SVBM/0457",
        )
        await grn(
            "V-OSSS",
            [
                {"material_id": mats["STL-TMT12"], "received_qty": 8000, "rate": 61.5},
                {"material_id": mats["STL-TMT8"], "received_qty": 1500, "rate": 63.5},
            ],
            7,
            "OSSS-2291",
        )
        await grn(
            "V-GAGG",
            [
                {"material_id": mats["AGG-20"], "received_qty": 120, "rate": 1420},
                {"material_id": mats["SND-RIVER"], "received_qty": 25, "rate": 2050},
            ],
            6,
            "GA-77",
        )
        # A draft waiting for the store manager to post
        await api.call(
            SK,
            "POST",
            "/grns",
            json={
                "vendor_id": vendors["V-GAGG"],
                "invoice_no": "GA-81",
                "items": [{"material_id": mats["AGG-20"], "received_qty": 40, "rate": 1430}],
            },
        )

        # ---------------- (4) site requests → approvals → (5) issue → (6) receipt
        rfq = await api.call(
            TA,
            "POST",
            "/rfqs",
            json={
                "site_id": sites["AVH-TA"],
                "priority": "URGENT",
                "purpose": "Slab casting — Tower A, level 5",
                "submit": True,
                "required_by": (t + timedelta(days=2)).isoformat(),
                "items": [
                    {"material_id": mats["CEM-OPC53"], "requested_qty": 3000},
                    {"material_id": mats["AGG-20"], "requested_qty": 20},
                    {"material_id": mats["STL-TMT12"], "requested_qty": 2500},
                ],
            },
        )
        rfq = await api.call(
            "pm.heights@avgroup.in", "POST", f"/rfqs/{rfq['id']}/pm-approve", json={"comment": "OK for L5 slab", "version": rfq["version"]}
        )
        rfq = await api.call(SM, "POST", f"/rfqs/{rfq['id']}/store-approve", json={"version": rfq["version"]})
        note = await api.call(
            SK,
            "POST",
            "/issue-notes",
            json={
                "request_id": rfq["id"],
                "vehicle_no": "MH12CD5678",
                "driver_name": "Ganesh",
                "driver_phone": "+91 90110 00000",
                "items": [{"request_item_id": i["id"], "issued_qty": i["approved_qty"]} for i in rfq["items"]],
            },
        )
        note = await api.call(SM, "POST", f"/issue-notes/{note['id']}/issue", json={"version": note["version"]})
        cement_line = next(i for i in note["items"] if i["material"]["code"] == "CEM-OPC53")
        await api.call(
            TA,
            "POST",
            f"/issue-notes/{note['id']}/receive",
            json={
                "items": [{"item_id": cement_line["id"], "received_qty": 2950, "damaged_qty": 50}],
                "remarks": "1 bag torn, 50 kg damaged",
                "version": note["version"],
            },
        )

        # (7) daily consumption at Tower A
        for days_ago, qty, activity in [(3, 500, "Column casting L5"), (2, 450, "Slab casting L5"), (1, 550, "Slab casting L5")]:
            await api.call(
                TA,
                "POST",
                "/consumptions",
                json={
                    "site_id": sites["AVH-TA"],
                    "entry_date": (t - timedelta(days=days_ago)).isoformat(),
                    "activity": activity,
                    "items": [{"material_id": mats["CEM-OPC53"], "qty": qty}, {"material_id": mats["AGG-20"], "qty": 4}],
                },
            )

        # Work in the queues for the other roles
        r2 = await api.call(
            TB,
            "POST",
            "/rfqs",
            json={
                "site_id": sites["AVH-TB"],
                "purpose": "Blockwork — Tower B, floors 2–3",
                "submit": True,
                "items": [
                    {"material_id": mats["BLK-AAC"], "requested_qty": 2500},
                    {"material_id": mats["CEM-OPC53"], "requested_qty": 1500},
                ],
            },
        )
        r3 = await api.call(
            P1,
            "POST",
            "/rfqs",
            json={
                "site_id": sites["AVG-P1"],
                "purpose": "Footings",
                "submit": True,
                "items": [
                    {"material_id": mats["CEM-OPC53"], "requested_qty": 4000},
                    {"material_id": mats["SND-RIVER"], "requested_qty": 10},
                ],
            },
        )
        await api.call("pm.greens@avgroup.in", "POST", f"/rfqs/{r3['id']}/pm-approve", json={"version": r3["version"]})
        log.info("demo data created (BOQ %s, requests %s / %s)", boq["boq_no"], r2["request_no"], r3["request_no"])
