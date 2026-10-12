import { lazy, type ReactNode, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { AppShell } from "@/components/layout/app-shell";
import { EmptyState } from "@/components/common/page";
import { Skeleton } from "@/components/ui/misc";
import { ChangePasswordPage, LoginPage } from "@/features/auth/auth-pages";
import { DashboardPage } from "@/features/dashboard/dashboard-page";
import { useAuth } from "@/lib/auth";

// Route-level code splitting: phones on site networks only download the screens they open.
const AdjustmentDetailPage = lazy(() => import("@/features/stock/stock-pages").then((m) => ({ default: m.AdjustmentDetailPage })));
const AdjustmentFormPage = lazy(() => import("@/features/stock/stock-pages").then((m) => ({ default: m.AdjustmentFormPage })));
const AdjustmentListPage = lazy(() => import("@/features/stock/stock-pages").then((m) => ({ default: m.AdjustmentListPage })));
const LedgerPage = lazy(() => import("@/features/stock/stock-pages").then((m) => ({ default: m.LedgerPage })));
const StockPage = lazy(() => import("@/features/stock/stock-pages").then((m) => ({ default: m.StockPage })));
const AuditPage = lazy(() => import("@/features/admin/admin-pages").then((m) => ({ default: m.AuditPage })));
const SystemPage = lazy(() => import("@/features/admin/admin-pages").then((m) => ({ default: m.SystemPage })));
const RolesPage = lazy(() => import("@/features/admin/admin-pages").then((m) => ({ default: m.RolesPage })));
const UsersPage = lazy(() => import("@/features/admin/admin-pages").then((m) => ({ default: m.UsersPage })));
const BoqDetailPage = lazy(() => import("@/features/boq/boq-pages").then((m) => ({ default: m.BoqDetailPage })));
const BoqFormPage = lazy(() => import("@/features/boq/boq-pages").then((m) => ({ default: m.BoqFormPage })));
const BoqListPage = lazy(() => import("@/features/boq/boq-pages").then((m) => ({ default: m.BoqListPage })));
const GrnDetailPage = lazy(() => import("@/features/grn/grn-pages").then((m) => ({ default: m.GrnDetailPage })));
const GrnFormPage = lazy(() => import("@/features/grn/grn-pages").then((m) => ({ default: m.GrnFormPage })));
const GrnListPage = lazy(() => import("@/features/grn/grn-pages").then((m) => ({ default: m.GrnListPage })));
const IssueDetailPage = lazy(() => import("@/features/issue/issue-pages").then((m) => ({ default: m.IssueDetailPage })));
const IssueFormPage = lazy(() => import("@/features/issue/issue-pages").then((m) => ({ default: m.IssueFormPage })));
const IssueListPage = lazy(() => import("@/features/issue/issue-pages").then((m) => ({ default: m.IssueListPage })));
const MarketPage = lazy(() => import("@/features/market/market-page").then((m) => ({ default: m.MarketPage })));
const MastersPage = lazy(() => import("@/features/masters/masters-page").then((m) => ({ default: m.MastersPage })));
const NotificationsPage = lazy(() => import("@/features/notifications/notifications-page").then((m) => ({ default: m.NotificationsPage })));
const ReportsHubPage = lazy(() => import("@/features/reports/report-pages").then((m) => ({ default: m.ReportsHubPage })));
const ReportViewerPage = lazy(() => import("@/features/reports/report-pages").then((m) => ({ default: m.ReportViewerPage })));
const RfqDetailPage = lazy(() => import("@/features/rfq/rfq-pages").then((m) => ({ default: m.RfqDetailPage })));
const RfqFormPage = lazy(() => import("@/features/rfq/rfq-pages").then((m) => ({ default: m.RfqFormPage })));
const RfqListPage = lazy(() => import("@/features/rfq/rfq-pages").then((m) => ({ default: m.RfqListPage })));
const ConsumptionPage = lazy(() => import("@/features/site/site-pages").then((m) => ({ default: m.ConsumptionPage })));
const ReturnDetailPage = lazy(() => import("@/features/site/site-pages").then((m) => ({ default: m.ReturnDetailPage })));
const ReturnFormPage = lazy(() => import("@/features/site/site-pages").then((m) => ({ default: m.ReturnFormPage })));
const ReturnsListPage = lazy(() => import("@/features/site/site-pages").then((m) => ({ default: m.ReturnsListPage })));
const SiteStockPage = lazy(() => import("@/features/site/site-pages").then((m) => ({ default: m.SiteStockPage })));

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading)
    return (
      <div className="grid min-h-dvh place-items-center">
        <Skeleton className="h-10 w-40" />
      </div>
    );
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  if (me.must_change_password && loc.pathname !== "/change-password") return <Navigate to="/change-password" replace />;
  return <>{children}</>;
}

/** UI-side guard for convenience only — the API enforces every permission and scope. */
function Can({ perms, children }: { perms: string[]; children: ReactNode }) {
  const { can } = useAuth();
  return !perms.length || can(...perms) ? <>{children}</> : <EmptyState title="You don't have access to this page" hint="Ask an administrator if you need it." />;
}

const g = (perms: string[], el: ReactNode) => (
  <Can perms={perms}>
    <Suspense fallback={<Skeleton className="h-40 w-full" />}>{el}</Suspense>
  </Can>
);

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={g(["reports.read"], <DashboardPage />)} />
        <Route path="change-password" element={<ChangePasswordPage />} />
        <Route path="notifications" element={g([], <NotificationsPage />)} />

        <Route path="market-rates" element={g(["market.read"], <MarketPage />)} />
        <Route path="boqs" element={g(["boq.read"], <BoqListPage />)} />
        <Route path="boqs/new" element={g(["boq.create"], <BoqFormPage />)} />
        <Route path="boqs/:id" element={g(["boq.read"], <BoqDetailPage />)} />
        <Route path="boqs/:id/edit" element={g(["boq.create"], <BoqFormPage />)} />

        <Route path="grns" element={g(["grn.read"], <GrnListPage />)} />
        <Route path="grns/new" element={g(["grn.create"], <GrnFormPage />)} />
        <Route path="grns/:id" element={g(["grn.read"], <GrnDetailPage />)} />
        <Route path="grns/:id/edit" element={g(["grn.create"], <GrnFormPage />)} />

        <Route path="rfqs" element={g(["rfq.read"], <RfqListPage />)} />
        <Route path="rfqs/new" element={g(["rfq.create"], <RfqFormPage />)} />
        <Route path="rfqs/:id" element={g(["rfq.read"], <RfqDetailPage />)} />
        <Route path="rfqs/:id/edit" element={g(["rfq.create"], <RfqFormPage />)} />

        <Route path="issue-notes" element={g(["issue.read"], <IssueListPage />)} />
        <Route path="issue-notes/new" element={g(["issue.create"], <IssueFormPage />)} />
        <Route path="issue-notes/:id" element={g(["issue.read"], <IssueDetailPage />)} />
        <Route path="issue-notes/:id/edit" element={g(["issue.create"], <IssueFormPage />)} />

        <Route path="site" element={g(["site.read"], <SiteStockPage />)} />
        <Route path="consumption" element={g(["site.read"], <ConsumptionPage />)} />
        <Route path="returns" element={g(["site.read", "return.receive"], <ReturnsListPage />)} />
        <Route path="returns/new" element={g(["site.return"], <ReturnFormPage />)} />
        <Route path="returns/:id" element={g(["site.read", "return.receive"], <ReturnDetailPage />)} />

        <Route path="stock" element={g(["stock.read"], <StockPage />)} />
        <Route path="stock/ledger" element={g(["stock.read"], <LedgerPage />)} />
        <Route path="adjustments" element={g(["stock.read"], <AdjustmentListPage />)} />
        <Route path="adjustments/new" element={g(["adjustment.create"], <AdjustmentFormPage />)} />
        <Route path="adjustments/:id" element={g(["stock.read"], <AdjustmentDetailPage />)} />

        <Route path="reports" element={g(["reports.read"], <ReportsHubPage />)} />
        <Route path="reports/:key" element={g(["reports.read"], <ReportViewerPage />)} />

        <Route path="masters" element={g(["masters.read"], <MastersPage />)} />
        <Route path="masters/:tab" element={g(["masters.read"], <MastersPage />)} />
        <Route path="admin/users" element={g(["users.manage"], <UsersPage />)} />
        <Route path="admin/roles" element={g(["roles.manage"], <RolesPage />)} />
        <Route path="admin/system" element={g(["system.manage"], <SystemPage />)} />
        <Route path="admin/audit" element={g(["audit.read"], <AuditPage />)} />

        <Route path="*" element={<EmptyState title="Page not found" />} />
      </Route>
    </Routes>
  );
}
