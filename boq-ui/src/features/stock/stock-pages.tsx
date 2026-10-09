import { useInfiniteQuery } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Plus, Scale, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial } from "@/components/common/pickers";
import { ApprovalTimeline, ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtQty, fmtRate, humanize, todayIso } from "@/lib/format";
import { post, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { Adjustment, Balance, LedgerRow } from "@/lib/types";
import { cn, newIdempotencyKey } from "@/lib/utils";

export function StockPage() {
  const { me } = useAuth();
  const nav = useNavigate();
  const { values, page, set } = useUrlFilters({ store_id: "", q: "", below_reorder: "", category_id: "" });
  const { data, isLoading, error } = useList<Balance>("balances", "/stock/balances", { ...values, below_reorder: values.below_reorder === "true", page, limit: 50 });
  const cols: Column<Balance>[] = [
    {
      key: "m",
      header: "Material",
      cell: (b) => (
        <div>
          <p className="flex items-center gap-1.5 font-medium">
            {b.material.name}
            {b.below_reorder && <AlertTriangle className="size-3.5 text-warning" aria-label="Below reorder level" />}
          </p>
          <p className="text-xs text-muted-foreground">{b.category}</p>
        </div>
      ),
    },
    { key: "s", header: "Store", cell: (b) => b.store.name },
    { key: "q", header: "Qty", align: "right", cell: (b) => <span className="font-semibold tabular-nums">{fmtQty(b.qty)} <span className="text-xs font-normal text-muted-foreground">{b.material.uom}</span></span> },
    { key: "r", header: "Reorder at", align: "right", cell: (b) => (b.reorder_level ? fmtQty(b.reorder_level) : "—") },
    ...(me?.show_finance
      ? [
          { key: "a", header: "Avg rate", align: "right" as const, cell: (b: Balance) => fmtRate(b.avg_rate) },
          { key: "v", header: "Value", align: "right" as const, cell: (b: Balance) => fmtMoney(b.value) },
        ]
      : []),
    { key: "l", header: "Last movement", cell: (b) => fmtDateTime(b.last_movement_at) },
  ];
  return (
    <>
      <PageHeader title="Stock" subtitle="Live balances (weighted-average valuation) across the central store and site stores" />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input placeholder="Search material" value={values.q} onChange={(e) => set({ q: e.target.value })} className="w-full sm:w-60" />
        <MasterSelect path="stores" value={values.store_id} onChange={(v) => set({ store_id: v })} placeholder="All stores" className="w-full sm:w-56" params={{ active: true }} />
        <MasterSelect path="categories" value={values.category_id} onChange={(v) => set({ category_id: v })} placeholder="All categories" className="w-full sm:w-48" />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4" checked={values.below_reorder === "true"} onChange={(e) => set({ below_reorder: e.target.checked ? "true" : "" })} />
          Below reorder only
        </label>
      </div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList
            rows={data?.data}
            columns={cols}
            rowKey={(b) => `${b.store.id}-${b.material.id}`}
            loading={isLoading}
            onRowClick={(b) => nav(`/stock/ledger?store_id=${b.store.id}&material_id=${b.material.id}&name=${encodeURIComponent(b.material.name)}`)}
            empty={<EmptyState title="No stock" />}
          />
          <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
        </Card>
      )}
    </>
  );
}

export function LedgerPage() {
  const { me } = useAuth();
  const { values, set } = useUrlFilters({ store_id: "", material_id: "", name: "", date_from: "", date_to: "" });
  const [material, setMaterial] = useState<PickedMaterial | null>(values.material_id ? { id: values.material_id, code: "", name: values.name, uom: "" } : null);
  const q = useInfiniteQuery({
    queryKey: ["ledger", values],
    enabled: !!values.store_id,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => get<LedgerRow[]>("/stock/ledger", { store_id: values.store_id, material_id: values.material_id, date_from: values.date_from, date_to: values.date_to, cursor: pageParam, limit: 50 }),
    getNextPageParam: (last) => last.meta.nextCursor ?? undefined,
  });
  const rows = q.data?.pages.flatMap((p) => p.data) ?? [];
  const cols: Column<LedgerRow>[] = [
    { key: "d", header: "Date", cell: (r) => <div><p className="font-medium">{fmtDate(r.txn_date)}</p><p className="text-xs text-muted-foreground">{r.material.name}</p></div> },
    { key: "t", header: "Type", cell: (r) => <span className="text-xs font-medium">{humanize(r.txn_type)}</span> },
    { key: "ref", header: "Document", cell: (r) => <RefLink r={r} /> },
    { key: "in", header: "In", align: "right", cell: (r) => (r.qty_in ? <span className="text-success">+{fmtQty(r.qty_in)}</span> : "") },
    { key: "out", header: "Out", align: "right", cell: (r) => (r.qty_out ? <span className="text-destructive">−{fmtQty(r.qty_out)}</span> : "") },
    { key: "bal", header: "Balance", align: "right", cell: (r) => <span className="font-semibold tabular-nums">{fmtQty(r.balance_qty)}</span> },
    ...(me?.show_finance
      ? [
          { key: "rate", header: "Rate", align: "right" as const, cell: (r: LedgerRow) => fmtRate(r.rate) },
          { key: "avg", header: "Avg after", align: "right" as const, cell: (r: LedgerRow) => fmtRate(r.avg_rate_after) },
          { key: "bv", header: "Balance value", align: "right" as const, cell: (r: LedgerRow) => fmtMoney(r.balance_value) },
        ]
      : []),
    { key: "by", header: "By", cell: (r) => r.created_by_name ?? "—" },
  ];
  return (
    <>
      <PageHeader back="/stock" title="Stock ledger (bin card)" subtitle="Every movement, newest first. The ledger is append-only — corrections appear as reversals." />
      <Card className="mb-3 grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Store">
          <MasterSelect path="stores" value={values.store_id} onChange={(v) => set({ store_id: v })} placeholder="Select store" />
        </Field>
        <Field label="Material">
          <MaterialPicker value={material} onChange={(m) => (setMaterial(m), set({ material_id: m.id, name: m.name }))} placeholder="All materials" />
        </Field>
        <Field label="From"><Input type="date" value={values.date_from} onChange={(e) => set({ date_from: e.target.value })} /></Field>
        <Field label="To"><Input type="date" value={values.date_to} onChange={(e) => set({ date_to: e.target.value })} /></Field>
      </Card>
      {!values.store_id ? (
        <Card><EmptyState title="Choose a store" hint="Then optionally a material to see its bin card." /></Card>
      ) : (
        <Card className="overflow-hidden">
          <DataList rows={rows} columns={cols} rowKey={(r) => String(r.id)} loading={q.isLoading} dense empty={<EmptyState title="No movements" />} />
          {q.hasNextPage && (
            <div className="border-t p-3 text-center">
              <Button variant="outline" loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>Load older</Button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}

function RefLink({ r }: { r: LedgerRow }) {
  const to = { grn: `/grns/${r.ref_id}`, issue_note: `/issue-notes/${r.ref_id}`, return: `/returns/${r.ref_id}`, adjustment: `/adjustments/${r.ref_id}` }[r.ref_type];
  const label = r.ref_no ?? humanize(r.ref_type);
  return to ? <Link to={to} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{label}</Link> : <span>{label}</span>;
}

// ------------------------------------------------------------------ adjustments

export function AdjustmentListPage() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { values, page, set } = useUrlFilters({ status: "", reason: "" });
  const { data, isLoading } = useList<Adjustment>("adjustments", "/stock-adjustments", { ...values, page, limit: 20 });
  const cols: Column<Adjustment>[] = [
    { key: "no", header: "Adjustment", cell: (a) => <div><p className="font-medium">{a.adj_no}</p><p className="text-xs text-muted-foreground">{a.store.name}</p></div> },
    { key: "r", header: "Reason", cell: (a) => humanize(a.reason) },
    { key: "d", header: "Date", cell: (a) => fmtDate(a.adj_date) },
    { key: "n", header: "Lines", align: "right", cell: (a) => a.items.length },
    { key: "by", header: "Prepared by", cell: (a) => a.created_by_name ?? "—" },
    { key: "s", header: "Status", mobile: false, cell: (a) => <StatusBadge status={a.status} /> },
  ];
  return (
    <>
      <PageHeader
        title="Stock adjustments"
        subtitle="Opening stock, physical counts and write-offs — all need a second person's approval"
        actions={can("adjustment.create") && <Button asChild><Link to="/adjustments/new"><Plus /> New adjustment</Link></Button>}
      />
      <div className="mb-3 flex flex-wrap gap-2">
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">Any status</option>
          {["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED"].map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}
        </Select>
        <Select value={values.reason} onChange={(e) => set({ reason: e.target.value })} className="w-auto">
          <option value="">Any reason</option>
          {["OPENING", "PHYSICAL_COUNT", "DAMAGE", "WASTAGE", "THEFT"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
        </Select>
      </div>
      <Card className="overflow-hidden">
        <DataList rows={data?.data} columns={cols} rowKey={(a) => a.id} loading={isLoading} onRowClick={(a) => nav(`/adjustments/${a.id}`)} mobileMeta={(a) => <StatusBadge status={a.status} />} empty={<EmptyState title="No adjustments" />} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
      </Card>
    </>
  );
}

export function AdjustmentDetailPage() {
  const { id } = useParams();
  const { can, me } = useAuth();
  const { data: a, isLoading, error } = useOne<Adjustment>("adjustment", `/stock-adjustments/${id}`);
  const inv = ["adjustment", "adjustments", "balances"];
  const submit = useAction(() => post(`/stock-adjustments/${id}/submit`, { version: a!.version }), { success: "Submitted for approval", invalidate: inv });
  const approve = useAction((c: string) => post(`/stock-adjustments/${id}/approve`, { version: a!.version, comment: c || null }), { success: "Approved — stock adjusted", invalidate: inv });
  const reject = useAction((c: string) => post(`/stock-adjustments/${id}/reject`, { version: a!.version, comment: c }), { success: "Rejected", invalidate: inv });
  const cancel = useAction(() => post(`/stock-adjustments/${id}/cancel`, { version: a!.version }), { success: "Cancelled", invalidate: inv });
  if (isLoading) return <LoadingRows />;
  if (error || !a) return <ErrorState error={error} />;
  const cols: Column<Adjustment["items"][number]>[] = [
    { key: "m", header: "Material", cell: (i) => i.material.name },
    ...(a.reason === "PHYSICAL_COUNT"
      ? [
          { key: "sys", header: "System", align: "right" as const, cell: (i: Adjustment["items"][number]) => fmtQty(i.system_qty) },
          { key: "phy", header: "Counted", align: "right" as const, cell: (i: Adjustment["items"][number]) => fmtQty(i.physical_qty) },
        ]
      : []),
    { key: "d", header: "Change", align: "right", cell: (i) => (i.qty_delta == null ? "on approval" : <span className={cn("tabular-nums", i.qty_delta < 0 ? "text-destructive" : "text-success")}>{i.qty_delta > 0 ? "+" : ""}{fmtQty(i.qty_delta)} {i.material.uom}</span>) },
    { key: "r", header: "Rate", align: "right", cell: (i) => fmtRate(i.rate) },
  ];
  const canApprove = a.status === "SUBMITTED" && can("adjustment.approve") && a.created_by_id !== me?.id;
  return (
    <>
      <PageHeader
        back="/adjustments"
        title={a.adj_no}
        badge={<StatusBadge status={a.status} />}
        subtitle={`${humanize(a.reason)} · ${a.store.name}`}
        actions={
          <>
            {["DRAFT", "REJECTED"].includes(a.status) && can("adjustment.create") && (
              <>
                <ConfirmAction label="Cancel" variant="ghost" title="Cancel adjustment?" onConfirm={() => cancel.mutateAsync(undefined)} />
                <ConfirmAction label="Submit" icon={<Scale />} title="Submit for approval?" onConfirm={() => submit.mutateAsync(undefined)} />
              </>
            )}
            {canApprove && (
              <>
                <ConfirmAction label="Reject" variant="outline" title="Reject adjustment" comment="required" onConfirm={(c) => reject.mutateAsync(c)} />
                <ConfirmAction label="Approve" variant="success" title="Approve and post to stock?" description={a.reason === "PHYSICAL_COUNT" ? "The difference between counted and system quantity is posted now." : undefined} comment="optional" onConfirm={(c) => approve.mutateAsync(c)} />
              </>
            )}
          </>
        }
      />
      <div className="grid gap-4">
        <Card className="p-4 md:p-5">
          <InfoGrid items={[["Date", fmtDate(a.adj_date)], ["Prepared by", a.created_by_name], ["Approved by", a.approved_by_name ?? "—"], ["Approved at", fmtDateTime(a.approved_at)]]} />
          {a.remarks && <p className="mt-3 text-sm text-muted-foreground">{a.remarks}</p>}
        </Card>
        <Section title="Lines"><DataList rows={a.items} columns={cols} rowKey={(i) => i.id} dense /></Section>
        <Section title="Approval history"><ApprovalTimeline items={a.approvals} /></Section>
      </div>
    </>
  );
}

interface ALine {
  key: string;
  material: PickedMaterial | null;
  qty: string;
  rate: string;
  remarks: string;
}

export function AdjustmentFormPage() {
  const nav = useNavigate();
  const [store, setStore] = useState("");
  const [reason, setReason] = useState("PHYSICAL_COUNT");
  const [date, setDate] = useState(todayIso());
  const [remarks, setRemarks] = useState("");
  const [lines, setLines] = useState<ALine[]>([{ key: crypto.randomUUID(), material: null, qty: "", rate: "", remarks: "" }]);
  const [idem] = useState(newIdempotencyKey);
  const count = reason === "PHYSICAL_COUNT";
  const save = useAction(
    () =>
      post<Adjustment>(
        "/stock-adjustments",
        {
          store_id: store,
          reason,
          adj_date: date,
          remarks: remarks || null,
          items: lines.filter((l) => l.material && l.qty !== "").map((l) => ({
            material_id: l.material!.id,
            ...(count ? { physical_qty: Number(l.qty) } : { qty: Number(l.qty) }),
            rate: reason === "OPENING" ? Number(l.rate) : null,
            remarks: l.remarks || null,
          })),
        },
        { "Idempotency-Key": idem },
      ),
    { success: (a) => `${a.adj_no} saved`, invalidate: ["adjustments"], onSuccess: (a) => nav(`/adjustments/${a.id}`, { replace: true }) },
  );
  const upd = (k: string, p: Partial<ALine>) => setLines((ls) => ls.map((l) => (l.key === k ? { ...l, ...p } : l)));
  return (
    <>
      <PageHeader back title="New stock adjustment" subtitle="Saved as a draft; a different user approves before stock changes" />
      <div className="grid gap-4">
        <Card className="grid gap-4 p-4 sm:grid-cols-3 md:p-5">
          <Field label="Store *"><MasterSelect path="stores" value={store} onChange={setStore} placeholder="Select store" /></Field>
          <Field label="Reason">
            <Select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="PHYSICAL_COUNT">Physical count</option>
              <option value="OPENING">Opening stock</option>
              <option value="DAMAGE">Damage</option>
              <option value="WASTAGE">Wastage</option>
              <option value="THEFT">Theft / loss</option>
            </Select>
          </Field>
          <Field label="Date"><Input type="date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Remarks" className="sm:col-span-3"><Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
        </Card>
        <Section title="Lines">
          <ul className="divide-y">
            {lines.map((l) => (
              <li key={l.key} className={cn("grid items-end gap-2 p-4 md:px-5", reason === "OPENING" ? "grid-cols-[1fr_6.5rem_6.5rem_auto]" : "grid-cols-[1fr_7rem_auto]")}>
                <Field label="Material"><MaterialPicker value={l.material} onChange={(m) => upd(l.key, { material: m })} /></Field>
                <Field label={count ? "Counted qty" : reason === "OPENING" ? "Qty" : "Qty lost"}><QtyInput value={l.qty} onChange={(e) => upd(l.key, { qty: e.target.value })} /></Field>
                {reason === "OPENING" && <Field label="Rate ₹"><QtyInput value={l.rate} onChange={(e) => upd(l.key, { rate: e.target.value })} /></Field>}
                <Button variant="ghost" size="icon" aria-label="Remove" disabled={lines.length === 1} onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}><Trash2 /></Button>
              </li>
            ))}
          </ul>
          <div className="border-t p-3 md:px-5"><Button variant="outline" onClick={() => setLines((ls) => [...ls, { key: crypto.randomUUID(), material: null, qty: "", rate: "", remarks: "" }])}><Plus /> Add line</Button></div>
        </Section>
        <ActionBar>
          <Button variant="outline" onClick={() => nav(-1)}>Cancel</Button>
          <Button loading={save.isPending} disabled={!store || !lines.some((l) => l.material && l.qty !== "")} onClick={() => save.mutate(undefined)}><BookOpen /> Save draft</Button>
        </ActionBar>
      </div>
    </>
  );
}
