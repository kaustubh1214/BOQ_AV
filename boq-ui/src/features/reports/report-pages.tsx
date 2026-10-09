import { useQuery } from "@tanstack/react-query";
import { BarChart3, Building2, ChevronRight, Download, HardHat, LineChart, ShieldCheck, Warehouse } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial, SiteSelect } from "@/components/common/pickers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { download, errorMessage, get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtCell } from "@/lib/format";
import { useUrlFilters } from "@/lib/hooks";
import type { ReportData, ReportMeta } from "@/lib/types";
import { cn } from "@/lib/utils";

const LEVELS: Record<string, { label: string; icon: typeof BarChart3; hint: string }> = {
  management: { label: "Management", icon: Building2, hint: "BOQ vs actual, budget, rates, turnaround" },
  store: { label: "Central store", icon: Warehouse, hint: "Stock, ledger, registers, pending requests" },
  project: { label: "Project", icon: BarChart3, hint: "Allocation, issues and consumption by project" },
  site: { label: "Site", icon: HardHat, hint: "Site stock, days of cover, consumption" },
  market: { label: "Market research", icon: LineChart, hint: "Vendor rate comparison" },
  audit: { label: "Audit", icon: ShieldCheck, hint: "Adjustments and physical counts" },
};

export function ReportsHubPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["reports"], queryFn: async () => (await get<ReportMeta[]>("/reports")).data });
  if (isLoading) return <LoadingRows />;
  if (error) return <ErrorState error={error} />;
  const groups = Object.entries(LEVELS)
    .map(([lvl, meta]) => ({ lvl, meta, reports: (data ?? []).filter((r) => r.level === lvl) }))
    .filter((g) => g.reports.length);
  return (
    <>
      <PageHeader title="Reports" subtitle="Every report can be filtered and exported to Excel (with the export permission)" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {groups.map(({ lvl, meta, reports }) => (
          <Card key={lvl} className="overflow-hidden">
            <div className="flex items-center gap-3 border-b px-4 py-3">
              <meta.icon className="size-5 text-primary" />
              <div>
                <h2 className="font-semibold">{meta.label}</h2>
                <p className="text-xs text-muted-foreground">{meta.hint}</p>
              </div>
            </div>
            <ul className="divide-y">
              {reports.map((r) => (
                <li key={r.key}>
                  <Link to={`/reports/${r.key}`} className="flex items-center gap-3 px-4 py-3 hover:bg-accent">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{r.title}</p>
                      <p className="truncate text-xs text-muted-foreground">{r.description}</p>
                    </div>
                    <ChevronRight className="size-4 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </>
  );
}

const FILTERS = { fy: "", date_from: "", date_to: "", project_id: "", site_id: "", store_id: "", material_id: "", category_id: "", vendor_id: "", days: "" };

export function ReportViewerPage() {
  const { key } = useParams();
  const { can, me } = useAuth();
  const { values, set } = useUrlFilters({ ...FILTERS, material_name: "" });
  const { material_name, ...params } = values;
  const [material, setMaterial] = useState<PickedMaterial | null>(values.material_id ? { id: values.material_id, name: material_name, code: "", uom: "" } : null);
  const { data: reports } = useQuery({ queryKey: ["reports"], queryFn: async () => (await get<ReportMeta[]>("/reports")).data });
  const meta = reports?.find((r) => r.key === key);
  const missing = (meta?.required_filters ?? []).filter((f) => !params[f as keyof typeof params]);
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["report", key, params],
    queryFn: async () => (await get<ReportData>(`/reports/${key}`, params)).data,
    enabled: !!meta && missing.length === 0,
  });
  const [exporting, setExporting] = useState(false);
  const has = (f: string) => meta?.filters.includes(f);

  const doExport = async () => {
    setExporting(true);
    try {
      await download(`/reports/${key}/export`, params);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageHeader
        back="/reports"
        title={meta?.title ?? "Report"}
        subtitle={meta?.description}
        actions={can("export") && (
          <Button variant="outline" loading={exporting} disabled={!data?.rows.length} onClick={doExport}>
            <Download /> Excel
          </Button>
        )}
      />
      {meta && (
        <Card className="mb-3 grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
          {has("fy") && <Field label="Financial year"><Input value={params.fy} placeholder={me?.financial_year} onChange={(e) => set({ fy: e.target.value })} /></Field>}
          {has("date_from") && <Field label="From"><Input type="date" value={params.date_from} onChange={(e) => set({ date_from: e.target.value })} /></Field>}
          {has("date_to") && <Field label="To"><Input type="date" value={params.date_to} onChange={(e) => set({ date_to: e.target.value })} /></Field>}
          {has("project_id") && <Field label="Project"><MasterSelect path="projects" value={params.project_id} onChange={(v) => set({ project_id: v })} placeholder="All projects" /></Field>}
          {has("site_id") && <Field label={`Site${meta.required_filters.includes("site_id") ? " *" : ""}`}><SiteSelect value={params.site_id} onChange={(v) => set({ site_id: v })} allowEmpty={!meta.required_filters.includes("site_id")} /></Field>}
          {has("store_id") && <Field label={`Store${meta.required_filters.includes("store_id") ? " *" : ""}`}><MasterSelect path="stores" value={params.store_id} onChange={(v) => set({ store_id: v })} placeholder="All stores" /></Field>}
          {has("material_id") && (
            <Field label={`Material${meta.required_filters.includes("material_id") ? " *" : ""}`}>
              <MaterialPicker value={material} onChange={(m) => (setMaterial(m), set({ material_id: m.id, material_name: m.name }))} placeholder="All materials" />
            </Field>
          )}
          {has("category_id") && <Field label="Category"><MasterSelect path="categories" value={params.category_id} onChange={(v) => set({ category_id: v })} placeholder="All categories" /></Field>}
          {has("vendor_id") && <Field label="Vendor"><MasterSelect path="vendors" value={params.vendor_id} onChange={(v) => set({ vendor_id: v })} placeholder="All vendors" /></Field>}
          {has("days") && <Field label="Idle for at least (days)"><Input type="number" inputMode="numeric" value={params.days} placeholder="90" onChange={(e) => set({ days: e.target.value })} /></Field>}
        </Card>
      )}
      {missing.length > 0 ? (
        <Card><EmptyState title="Choose the required filters" hint={`Select ${missing.map((m) => m.replace("_id", "")).join(" and ")} to run this report.`} /></Card>
      ) : error ? (
        <ErrorState error={error} />
      ) : isLoading || !data ? (
        <Card><LoadingRows /></Card>
      ) : (
        <ReportTable data={data} fetching={isFetching} />
      )}
    </>
  );
}

function ReportTable({ data, fetching }: { data: ReportData; fetching: boolean }) {
  const numeric = (t: string) => ["qty", "money", "rate", "pct", "int"].includes(t);
  if (!data.rows.length) return <Card><EmptyState title="No rows for these filters" /></Card>;
  return (
    <Card className={cn("overflow-hidden transition-opacity", fetching && "opacity-60")}>
      <div className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground">
        <span>{data.row_count} row(s)</span>
        <span className="md:hidden">Scroll sideways for more columns →</span>
      </div>
      <div className="max-h-[70dvh] overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
            <tr>
              {data.columns.map((c, i) => (
                <th key={c.key} className={cn("whitespace-nowrap px-3 py-2.5 font-medium", numeric(c.type) ? "text-right" : "text-left", i === 0 && "sticky left-0 bg-muted")}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.rows.map((r, ri) => (
              <tr key={ri} className="hover:bg-accent/40">
                {data.columns.map((c, i) => (
                  <td key={c.key} className={cn("whitespace-nowrap px-3 py-2", numeric(c.type) && "text-right tabular-nums", i === 0 && "sticky left-0 bg-card font-medium")}>
                    {fmtCell(r[c.key], c.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {Object.keys(data.totals).length > 0 && (
            <tfoot className="sticky bottom-0 bg-muted font-semibold">
              <tr>
                {data.columns.map((c, i) => (
                  <td key={c.key} className={cn("whitespace-nowrap px-3 py-2.5", numeric(c.type) && "text-right tabular-nums", i === 0 && "sticky left-0 bg-muted")}>
                    {i === 0 ? "Total" : c.key in data.totals ? fmtCell(data.totals[c.key], c.type) : ""}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </Card>
  );
}
