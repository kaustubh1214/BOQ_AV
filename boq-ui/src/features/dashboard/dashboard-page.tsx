import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowDownToLine, ArrowRight, BookOpenCheck, CheckCircle2, ClipboardList } from "lucide-react";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState, PageHeader, Section } from "@/components/common/page";
import { KpiCard } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtInt, fmtMoney, fmtMoneyShort, fmtQty } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

export function DashboardPage() {
  const { me, can } = useAuth();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["dashboard"], queryFn: () => get<Dashboard>("/dashboard"), refetchInterval: 120_000 });
  const d = data?.data;
  const greeting = new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening";

  return (
    <>
      <PageHeader title={`${greeting}, ${me?.name.split(" ")[0]}`} subtitle={`FY ${d?.financial_year ?? me?.financial_year}${d?.boq_no ? ` · Live BOQ ${d.boq_no}` : ""}`} />
      {can("grn.post") && (
        <div className="mb-4 flex flex-wrap gap-2">
          <Button asChild variant="outline">
            <Link to="/rfqs?queue=store">
              <ClipboardList /> Site requests to approve
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/grns/new">
              <ArrowDownToLine /> Post inward (GRN)
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/stock/ledger">
              <BookOpenCheck /> Central ledger
            </Link>
          </Button>
        </div>
      )}
      {error && <ErrorState error={error} retry={refetch} />}
      {isLoading && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}
      {d && (
        <div className="grid gap-4 md:gap-6">
          <Tasks tasks={d.tasks} />
          {d.kpis.length > 0 && (
            <div className="grid grid-cols-2 gap-3 md:gap-4 lg:grid-cols-3">
              {d.kpis.map((k) => (
                <KpiCard key={k.key} label={k.label} value={k.value} format={k.format} hint={k.hint} />
              ))}
            </div>
          )}
          <div className="grid gap-4 md:gap-6 xl:grid-cols-2">
            {d.boq_chart.length > 0 && <BoqProgress rows={d.boq_chart} />}
            {d.project_chart.length > 0 && <ProjectChart rows={d.project_chart} />}
          </div>
          {d.low_stock.length > 0 && (
            <Section title="Below reorder level" actions={<Link to="/stock?below_reorder=true" className="text-sm text-primary hover:underline">View stock</Link>}>
              <ul className="divide-y">
                {d.low_stock.map((s) => (
                  <li key={s.material + s.store} className="flex items-center gap-3 px-4 py-2.5 text-sm md:px-5">
                    <AlertTriangle className="size-4 shrink-0 text-warning" aria-label="Warning" />
                    <span className="min-w-0 flex-1 truncate font-medium">{s.material}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {fmtQty(s.qty)} / {fmtQty(s.reorder_level)} {s.uom}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </>
  );
}

function Tasks({ tasks }: { tasks: Dashboard["tasks"] }) {
  if (!tasks.length) return null;
  const open = tasks.filter((t) => t.count > 0);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b px-4 py-3 md:px-5">
        <h2 className="font-semibold">Your tasks</h2>
        <span className="text-xs text-muted-foreground">{open.length ? `${open.reduce((a, t) => a + t.count, 0)} waiting` : "All clear"}</span>
      </div>
      {open.length === 0 ? (
        <p className="flex items-center gap-2 px-4 py-4 text-sm text-muted-foreground md:px-5">
          <CheckCircle2 className="size-4 text-success" /> Nothing needs your action right now.
        </p>
      ) : (
        <ul className="grid divide-y sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-3">
          {open.map((t) => (
            <li key={t.key} className="sm:border-b sm:border-r">
              <Link to={t.link} className="flex items-center gap-3 px-4 py-3 hover:bg-accent md:px-5">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold tabular-nums text-primary">{t.count}</span>
                <span className="min-w-0 flex-1 text-sm">{t.label}</span>
                <ArrowRight className="size-4 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Planned = full track; procured = soft bar; issued = solid bar (issued ⊂ procured). Numbers stay in text ink. */
function BoqProgress({ rows }: { rows: Dashboard["boq_chart"] }) {
  return (
    <Section
      title="BOQ progress — top materials"
      actions={
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: "var(--series-1-soft)" }} /> Procured
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: "var(--series-1)" }} /> Issued
          </span>
        </div>
      }
    >
      <ul className="grid gap-3 px-4 py-4 md:px-5">
        {rows.map((r) => {
          const procured = Math.min(100, r.values.procured_pct ?? 0);
          const issued = Math.min(100, r.values.issued_pct ?? 0);
          return (
            <li key={r.label} title={`${r.label}: procured ${procured.toFixed(1)}%, issued ${issued.toFixed(1)}% of planned`}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate font-medium">{r.label}</span>
                <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                  {procured.toFixed(0)}% in · {issued.toFixed(0)}% out
                </span>
              </div>
              <div className="relative h-2.5 overflow-hidden rounded-full bg-muted">
                <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${procured}%`, background: "var(--series-1-soft)" }} />
                <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${issued}%`, background: "var(--series-1)" }} />
              </div>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function ProjectChart({ rows }: { rows: Dashboard["project_chart"] }) {
  const finance = rows.some((r) => r.values.issued_value != null);
  const key = finance ? "issued_value" : "issue_notes";
  const data = rows.map((r) => ({ name: r.label, v: r.values[key] ?? 0 }));
  const fmt = finance ? fmtMoneyShort : fmtInt;
  return (
    <Section title={finance ? "Material issued by project (value, this FY)" : "Issue notes by project (this FY)"}>
      <div className="h-64 px-2 py-4" role="img" aria-label="Bar chart of material issued per project">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }} barCategoryGap={10}>
            <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="0" />
            <XAxis type="number" tickFormatter={(v) => fmt(v)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" width={96} tick={{ fontSize: 12, fill: "var(--foreground)" }} axisLine={false} tickLine={false} />
            <Tooltip
              cursor={{ fill: "var(--accent)" }}
              contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: "var(--foreground)", fontWeight: 600 }}
              itemStyle={{ color: "var(--foreground)" }}
              formatter={(v) => [finance ? fmtMoney(Number(v)) : fmtInt(Number(v)), finance ? "Issued value" : "Issue notes"]}
            />
            <Bar dataKey="v" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={28} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Section>
  );
}
