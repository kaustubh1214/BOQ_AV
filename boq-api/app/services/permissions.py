"""Permission catalog and the default role matrix (PLAN.md §6). Seeded idempotently; admins can edit
non-system roles from the UI. System roles are re-synced on every seed run."""

from app.models.enums import ScopeType

PERMISSIONS: dict[str, tuple[str, str]] = {
    # code: (module, description)
    "masters.read": ("masters", "View master data"),
    "masters.manage": ("masters", "Create/edit categories, UOMs, materials, projects, sites, stores"),
    "vendors.manage": ("masters", "Create/edit vendors"),
    "market.read": ("market", "View market research rates"),
    "market.manage": ("market", "Record market research rates"),
    "boq.read": ("boq", "View BOQs"),
    "boq.create": ("boq", "Draft, edit, submit and revise BOQs"),
    "boq.approve": ("boq", "Approve / reject / close BOQs"),
    "grn.read": ("grn", "View goods receipts"),
    "grn.create": ("grn", "Draft goods receipts"),
    "grn.post": ("grn", "Post / cancel goods receipts (updates stock)"),
    "rfq.read": ("rfq", "View material requests"),
    "rfq.create": ("rfq", "Raise / edit / submit / cancel own material requests"),
    "rfq.approve_store": ("rfq", "Store Manager approval / close of material requests"),
    "issue.read": ("issue", "View issue notes"),
    "issue.create": ("issue", "Draft issue notes"),
    "issue.post": ("issue", "Issue / cancel issue notes (updates stock)"),
    "issue.receive": ("issue", "Acknowledge receipt of issue notes at site"),
    "site.read": ("site", "View site stock, consumption and returns"),
    "site.consume": ("site", "Record daily consumption"),
    "site.return": ("site", "Create and dispatch returns to central store"),
    "return.receive": ("site", "Receive returns at central store"),
    "stock.read": ("stock", "View stock balances and ledger"),
    "adjustment.create": ("stock", "Draft and submit stock adjustments / physical counts"),
    "adjustment.approve": ("stock", "Approve / reject stock adjustments"),
    "reports.read": ("reports", "View reports and dashboards"),
    "finance.view": ("reports", "See rates, amounts and stock values"),
    "export": ("reports", "Export reports to Excel"),
    "users.manage": ("admin", "Manage users and their role assignments"),
    "roles.manage": ("admin", "Manage roles and the permission matrix"),
    "audit.read": ("admin", "View the audit trail"),
    "system.manage": ("admin", "System settings and health (Super Admin only)"),
}

# role code: (name, default scope, description, permissions)
# Three roles only. SUPER_ADMIN is the single IT account (seeded once, never creatable from the API).
ROLES: dict[str, tuple[str, ScopeType, str, list[str]]] = {
    "SUPER_ADMIN": (
        "Super Admin",
        ScopeType.GLOBAL,
        "IT team account: everything, incl. roles, system settings and Store Managers",
        list(PERMISSIONS),
    ),
    "STORE_MANAGER": (
        "Store Manager",
        ScopeType.GLOBAL,
        "Company admin: masters, sites, users (Site Users), BOQ, inward, request approval, issue, ledger, reports, audit",
        [
            "masters.read",
            "masters.manage",
            "vendors.manage",
            "market.read",
            "market.manage",
            "boq.read",
            "boq.create",
            "boq.approve",
            "grn.read",
            "grn.create",
            "grn.post",
            "rfq.read",
            "rfq.approve_store",
            "issue.read",
            "issue.create",
            "issue.post",
            "return.receive",
            "site.read",
            "stock.read",
            "adjustment.create",
            "adjustment.approve",
            "reports.read",
            "finance.view",
            "export",
            "users.manage",
            "audit.read",
        ],
    ),
    "SITE_USER": (
        "Site User",
        ScopeType.SITE,
        "Raises requests, receives material, logs consumption and returns for their site (quantities only, no rates)",
        [
            "masters.read",
            "rfq.read",
            "rfq.create",
            "issue.read",
            "issue.receive",
            "site.read",
            "site.consume",
            "site.return",
            "stock.read",
            "reports.read",
        ],
    ),
}

# Roles that existed before the simplification; the seed retires them.
LEGACY_ROLES = ("MANAGEMENT", "PROCUREMENT", "STORE_KEEPER", "PROJECT_MANAGER", "AUDITOR")
LEGACY_RENAMES = {"SITE_ENGINEER": "SITE_USER"}

# Response fields that are hidden (set to null) for users without finance.view.
FINANCE_FIELDS = frozenset(
    {
        "rate",
        "amount",
        "value",
        "avg_rate",
        "estimated_rate",
        "estimated_amount",
        "benchmark_rate",
        "total_estimated_value",
        "total_amount",
        "total_gst",
        "gst_amount",
        "total_value",
        "balance_value",
        "avg_rate_after",
        "stock_value",
        "issued_value",
        "consumed_value",
        "procured_value",
        "estimated_value",
        "loss_value",
        "actual_avg_rate",
        "rate_variance_pct",
        "benchmark_gap_pct",
        "last_rate",
        "min_rate",
        "max_rate",
    }
)
