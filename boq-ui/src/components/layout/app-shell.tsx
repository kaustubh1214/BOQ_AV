import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  BarChart3,
  Bell,
  Boxes,
  ClipboardList,
  Database,
  FileSpreadsheet,
  Gauge,
  HardHat,
  History,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  PackageCheck,
  ScanLine,
  Scale,
  Search,
  Shield,
  Sun,
  Truck,
  Undo2,
  Users,
} from "lucide-react";
import { type ComponentType, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import { Dialog, SheetContent } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  perms: string[];
}

const NAV: { group: string; items: NavItem[] }[] = [
  { group: "", items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, perms: ["reports.read"] }] },
  {
    group: "Planning",
    items: [
      { to: "/market-rates", label: "Market research", icon: Search, perms: ["market.read"] },
      { to: "/boqs", label: "BOQ (central store)", icon: FileSpreadsheet, perms: ["boq.read"] },
    ],
  },
  {
    group: "Central store",
    items: [
      { to: "/grns", label: "Inward (GRN)", icon: ArrowDownToLine, perms: ["grn.read"] },
      { to: "/rfqs", label: "Requests (RFQ)", icon: ClipboardList, perms: ["rfq.read"] },
      { to: "/issue-notes", label: "Issue notes", icon: Truck, perms: ["issue.read"] },
      { to: "/returns", label: "Returns", icon: Undo2, perms: ["site.read", "return.receive"] },
      { to: "/stock", label: "Stock & ledger", icon: Boxes, perms: ["stock.read"] },
      { to: "/adjustments", label: "Adjustments", icon: Scale, perms: ["adjustment.create", "adjustment.approve"] },
    ],
  },
  {
    group: "Site",
    items: [
      { to: "/site", label: "Site stock", icon: HardHat, perms: ["site.read"] },
      { to: "/consumption", label: "Daily consumption", icon: Gauge, perms: ["site.read"] },
    ],
  },
  { group: "Insights", items: [{ to: "/reports", label: "Reports", icon: BarChart3, perms: ["reports.read"] }] },
  {
    group: "Setup",
    items: [
      { to: "/masters", label: "Masters", icon: Database, perms: ["masters.read"] },
      { to: "/admin/users", label: "Users", icon: Users, perms: ["users.manage"] },
      { to: "/admin/roles", label: "Roles & permissions", icon: Shield, perms: ["roles.manage", "users.manage"] },
      { to: "/admin/audit", label: "Audit trail", icon: History, perms: ["audit.read"] },
    ],
  },
];

const SITE_TABS: NavItem[] = [
  { to: "/", label: "Home", icon: LayoutDashboard, perms: [] },
  { to: "/rfqs", label: "Request", icon: ClipboardList, perms: [] },
  { to: "/issue-notes?queue=receive", label: "Receive", icon: PackageCheck, perms: [] },
  { to: "/consumption", label: "Consume", icon: Gauge, perms: [] },
  { to: "/site", label: "Stock", icon: Boxes, perms: [] },
];

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("boq-theme", next ? "dark" : "light");
    } catch {
      /* private mode */
    }
    setDark(next);
  };
  return { dark, toggle };
}

function Brand({ compact }: { compact?: boolean }) {
  const { me } = useAuth();
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <span className="grid size-9 shrink-0 place-items-center overflow-hidden rounded-md bg-white ring-1 ring-black/10">
        <img src="/logo.webp" alt="AV Group" className="size-8 object-contain" />
      </span>
      {!compact && (
        <div className="leading-tight">
          <p className="text-sm font-semibold">{me?.company_name ?? "AV Group"}</p>
          <p className="text-[11px] opacity-70">BOQ & Material · FY {me?.financial_year}</p>
        </div>
      )}
    </Link>
  );
}

function SideNav({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  return (
    <nav className="flex-1 overflow-y-auto px-3 py-4">
      {NAV.map((g) => {
        const items = g.items.filter((i) => can(...i.perms));
        if (!items.length) return null;
        return (
          <div key={g.group || "home"} className="mb-4">
            {g.group && <p className="mb-1 px-3 text-[11px] font-medium uppercase tracking-[0.08em] text-sidebar-foreground/45">{g.group}</p>}
            {items.map((i) => (
              <NavLink
                key={i.to}
                to={i.to}
                end={i.to === "/"}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-1.5 text-[13px] text-sidebar-foreground/70 transition-colors hover:bg-white/[0.06] hover:text-sidebar-foreground",
                    isActive && "bg-white/[0.09] font-medium text-sidebar-foreground [&>svg]:text-brand",
                  )
                }
              >
                <i.icon className="size-4 shrink-0" />
                {i.label}
              </NavLink>
            ))}
          </div>
        );
      })}
    </nav>
  );
}

function NotificationBell() {
  const { data } = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => get<{ unread: number }>("/notifications/unread-count"),
    refetchInterval: 60_000,
  });
  const n = data?.data.unread ?? 0;
  return (
    <Button variant="ghost" size="icon" asChild aria-label={`Notifications (${n} unread)`}>
      <Link to="/notifications" className="relative">
        <Bell />
        {n > 0 && (
          <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-4 text-white">
            {n > 99 ? "99+" : n}
          </span>
        )}
      </Link>
    </Button>
  );
}

function UserMenu() {
  const { me, logout } = useAuth();
  const { dark, toggle } = useTheme();
  const initials = (me?.name ?? "?").split(" ").map((p) => p[0]).slice(0, 2).join("");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="grid size-9 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground" aria-label="Account menu">
          {initials}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>
          <p className="text-sm font-medium text-foreground">{me?.name}</p>
          <p className="truncate">{me?.email}</p>
          <p className="mt-1">{me?.roles.map((r) => r.replace(/_/g, " ").toLowerCase()).join(", ")}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={toggle}>
          {dark ? <Sun /> : <Moon />} {dark ? "Light mode" : "Dark mode"}
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/change-password">
            <KeyRound /> Change password
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => logout()} className="text-destructive">
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell() {
  const { isSiteUser } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [loc.pathname]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[256px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col bg-sidebar text-sidebar-foreground lg:flex">
        <div className="border-b border-white/10 px-5 py-4">
          <Brand />
        </div>
        <SideNav />
        <p className="px-5 py-3 text-[11px] text-sidebar-foreground/40">v1.0 · API /api/v1</p>
      </aside>

      <Dialog open={open} onOpenChange={setOpen}>
        <SheetContent className="bg-sidebar text-sidebar-foreground">
          <div className="border-b border-white/10 px-5 py-4">
            <Brand />
          </div>
          <SideNav onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Dialog>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/90 px-3 backdrop-blur md:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu />
          </Button>
          <div className="lg:hidden">
            <Brand compact />
          </div>
          <div className="flex-1" />
          <Button variant="ghost" size="icon" asChild aria-label="Scan gate pass" className="sm:hidden">
            <Link to="/issue-notes?queue=receive">
              <ScanLine />
            </Link>
          </Button>
          <NotificationBell />
          <UserMenu />
        </header>
        <main className={cn("mx-auto w-full max-w-[1400px] flex-1 px-4 py-4 md:px-6 md:py-6", isSiteUser && "pb-24 lg:pb-6")}>
          <Outlet />
        </main>
      </div>

      {isSiteUser && (
        <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t bg-card pb-safe lg:hidden">
          {SITE_TABS.map((t) => {
            const active = t.to === "/" ? loc.pathname === "/" : `${loc.pathname}${loc.search}`.startsWith(t.to.split("?")[0]) && (!t.to.includes("?") || loc.search.includes(t.to.split("?")[1]));
            return (
              <Link key={t.to} to={t.to} className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px]", active ? "text-primary" : "text-muted-foreground")}>
                <t.icon className="size-5" />
                {t.label}
              </Link>
            );
          })}
        </nav>
      )}
    </div>
  );
}

