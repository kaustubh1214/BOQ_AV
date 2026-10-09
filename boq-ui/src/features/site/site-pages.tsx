import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Gauge, Plus, Send, Trash2, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MaterialPicker, type PickedMaterial, SiteSelect, useMySites } from "@/components/common/pickers";
import { ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney, fmtQty, todayIso } from "@/lib/format";
import { post, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { Consumption, ReturnNote, SiteStockLine } from "@/lib/types";
import { cn, newIdempotencyKey } from "@/lib/utils";

function useDefaultSite(): [string, (s: string) => void] {
  const sites = useMySites();
  const { values, set } = useUrlFilters({ site_id: "" });
  useEffect(() => {
    if (!values.site_id && sites.length) set({ site_id: sites[0].id });
  }, [sites, values.site_id]); // eslint-disable-line react-hooks/exhaustive-deps
  return [values.site_id, (s) => set({ site_id: s })];
}

export function SiteStockPage() {
  const [site, setSite] = useDefaultSite();
  const { me } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["site-stock", site],
    queryFn: async () => (await get<SiteStockLine[]>("/site/stock", { site_id: site })).data,
    enabled: !!site,
  });
  const cols: Column<SiteStockLine>[] = [
    { key: "m", header: "Material", cell: (l) => <div><p className="font-medium">{l.material.name}</p><p className="text-xs text-muted-foreground">{l.material.code}</p></div> },
    { key: "q", header: "In stock", align: "right", cell: (l) => <span className="font-semibold tabular-nums">{fmtQty(l.qty)} <span className="text-xs font-normal text-muted-foreground">{l.material.uom}</span></span> },
    { key: "t", header: "In transit", align: "right", cell: (l) => (l.in_transit ? fmtQty(l.in_transit) : "—") },
    { key: "a7", header: "Avg / day (7d)", align: "right", cell: (l) => fmtQty(l.avg_daily_7d) },
    { key: "a30", header: "Avg / day (30d)", align: "right", cell: (l) => fmtQty(l.avg_daily_30d) },
    {
      key: "cover",
      header: "Days of cover",
      align: "right",
      cell: (l) =>
        l.days_of_cover == null ? (
          "—"
        ) : (
          <span className={cn("inline-flex items-center gap-1 tabular-nums", l.days_of_cover < 3 && "font-semibold text-destructive")}>
            {l.days_of_cover < 3 && <AlertTriangle className="size-3.5" aria-label="Low cover" />}
            {l.days_of_cover.toFixed(1)}
          </span>
        ),
    },
    ...(me?.show_finance ? [{ key: "v", header: "Value", align: "right" as const, cell: (l: SiteStockLine) => fmtMoney(l.value) }] : []),
  ];
  return (
    <>
      <PageHeader
        title="Site stock"
        subtitle="What's at site, how fast it's being used and how many days it will last"
        actions={
          <>
            <Button variant="outline" asChild><Link to="/returns/new"><Undo2 /> Return to store</Link></Button>
            <Button asChild><Link to="/rfqs/new"><Plus /> Request material</Link></Button>
          </>
        }
      />
      <div className="mb-3 sm:max-w-sm"><SiteSelect value={site} onChange={setSite} /></div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList rows={data} columns={cols} rowKey={(l) => l.material.id} loading={isLoading} empty={<EmptyState title="No stock at this site yet" hint="Material arrives here when an issue note is received." />} />
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ consumption

interface CLine {
  key: string;
  material: PickedMaterial | null;
  qty: string;
}

export function ConsumptionPage() {
  const [site, setSite] = useDefaultSite();
  const { can, me } = useAuth();
  const [page, setPage] = useState(1);
  const [entering, setEntering] = useState(false);
  const { data, isLoading } = useList<Consumption>("consumptions", "/consumptions", { site_id: site, status: "POSTED", page, limit: 25 }, !!site);
  const cancel = useAction(({ id, reason }: { id: string; reason: string }) => post(`/consumptions/${id}/cancel`, { reason }), { success: "Entry reversed", invalidate: ["consumptions", "site-stock"] });
  const cols: Column<Consumption>[] = [
    { key: "m", header: "Material", cell: (c) => <div><p className="font-medium">{c.material.name}</p><p className="text-xs text-muted-foreground">{c.activity ?? "—"}</p></div> },
    { key: "d", header: "Date", cell: (c) => fmtDate(c.entry_date) },
    { key: "q", header: "Qty", align: "right", cell: (c) => `${fmtQty(c.qty)} ${c.material.uom}` },
    { key: "by", header: "By", cell: (c) => c.created_by_name ?? "—" },
    ...(me?.show_finance ? [{ key: "v", header: "Value", align: "right" as const, cell: (c: Consumption) => fmtMoney(c.amount) }] : []),
    ...(can("site.consume")
      ? [{ key: "x", header: "", mobile: false, align: "right" as const, cell: (c: Consumption) => <ConfirmAction label="Reverse" size="sm" variant="ghost" title="Reverse this entry?" description="The quantity goes back into site stock." comment="required" onConfirm={(reason) => cancel.mutateAsync({ id: c.id, reason })} /> }]
      : []),
  ];
  return (
    <>
      <PageHeader
        title="Daily consumption"
        subtitle="Log what was used at site each day — drives average use and days of cover"
        actions={can("site.consume") && <Button onClick={() => setEntering(true)} disabled={!site}><Gauge /> Log consumption</Button>}
      />
      <div className="mb-3 sm:max-w-sm"><SiteSelect value={site} onChange={(s) => (setSite(s), setPage(1))} /></div>
      <Card className="overflow-hidden">
        <DataList rows={data?.data} columns={cols} rowKey={(c) => c.id} loading={isLoading} empty={<EmptyState title="No consumption logged" />} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={setPage} />
      </Card>
      {entering && <ConsumptionDialog siteId={site} onClose={() => setEntering(false)} />}
    </>
  );
}

function ConsumptionDialog({ siteId, onClose }: { siteId: string; onClose: () => void }) {
  const [date, setDate] = useState(todayIso());
  const [activity, setActivity] = useState("");
  const [lines, setLines] = useState<CLine[]>([{ key: crypto.randomUUID(), material: null, qty: "" }]);
  const [idem] = useState(newIdempotencyKey);
  const { data: stock } = useQuery({ queryKey: ["site-stock", siteId], queryFn: async () => (await get<SiteStockLine[]>("/site/stock", { site_id: siteId })).data });
  const atSite = new Set((stock ?? []).filter((s) => s.qty > 0).map((s) => s.material.id));
  const qtyAt = Object.fromEntries((stock ?? []).map((s) => [s.material.id, s.qty]));
  const save = useAction(
    () => post("/consumptions", { site_id: siteId, entry_date: date, activity: activity || null, items: lines.filter((l) => l.material && Number(l.qty) > 0).map((l) => ({ material_id: l.material!.id, qty: Number(l.qty) })) }, { "Idempotency-Key": idem }),
    { success: "Consumption logged", invalidate: ["consumptions", "site-stock"], onSuccess: onClose },
  );
  const upd = (k: string, p: Partial<CLine>) => setLines((ls) => ls.map((l) => (l.key === k ? { ...l, ...p } : l)));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Log consumption" description="Only material currently at the site can be consumed.">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date"><Input type="date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Activity"><Input value={activity} onChange={(e) => setActivity(e.target.value)} placeholder="e.g. Slab L6" /></Field>
        </div>
        <ul className="grid gap-3">
          {lines.map((l) => (
            <li key={l.key} className="grid grid-cols-[1fr_6.5rem_auto] items-end gap-2">
              <Field label="Material">
                <MaterialPicker value={l.material} onChange={(m) => upd(l.key, { material: m })} restrictTo={atSite} />
              </Field>
              <Field label={l.material ? `Qty (max ${fmtQty(qtyAt[l.material.id])})` : "Qty"}>
                <QtyInput value={l.qty} onChange={(e) => upd(l.key, { qty: e.target.value })} />
              </Field>
              <Button variant="ghost" size="icon" aria-label="Remove" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))} disabled={lines.length === 1}><Trash2 /></Button>
            </li>
          ))}
        </ul>
        <Button variant="outline" size="sm" className="justify-self-start" onClick={() => setLines((ls) => [...ls, { key: crypto.randomUUID(), material: null, qty: "" }])}><Plus /> Add material</Button>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button loading={save.isPending} disabled={!lines.some((l) => l.material && Number(l.qty) > 0)} onClick={() => save.mutate(undefined)}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ returns

export function ReturnsListPage() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { values, page, set } = useUrlFilters({ queue: "", status: "" });
  const { data, isLoading } = useList<ReturnNote>("returns", "/returns", { ...values, page, limit: 20 });
  const cols: Column<ReturnNote>[] = [
    { key: "no", header: "Return", cell: (r) => <div><p className="font-medium">{r.return_no}</p><p className="text-xs text-muted-foreground">{r.site.name}</p></div> },
    { key: "d", header: "Date", cell: (r) => fmtDate(r.return_date) },
    { key: "reason", header: "Reason", cell: (r) => r.reason ?? "—" },
    { key: "items", header: "Items", align: "right", cell: (r) => r.items.length },
    { key: "s", header: "Status", mobile: false, cell: (r) => <StatusBadge status={r.status} /> },
  ];
  return (
    <>
      <PageHeader
        title="Returns to central store"
        subtitle="Excess or unused material sent back from sites"
        actions={can("site.return") && <Button asChild><Link to="/returns/new"><Plus /> New return</Link></Button>}
      />
      <div className="mb-3 flex gap-2">
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">Any status</option>
          {["DRAFT", "DISPATCHED", "RECEIVED", "CANCELLED"].map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}
        </Select>
        {can("return.receive") && (
          <Button variant={values.queue === "receive" ? "default" : "outline"} onClick={() => set({ queue: values.queue === "receive" ? "" : "receive" })}>To receive</Button>
        )}
      </div>
      <Card className="overflow-hidden">
        <DataList rows={data?.data} columns={cols} rowKey={(r) => r.id} loading={isLoading} onRowClick={(r) => nav(`/returns/${r.id}`)} mobileMeta={(r) => <StatusBadge status={r.status} />} empty={<EmptyState title="No returns" />} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
      </Card>
    </>
  );
}

export function ReturnDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: r, isLoading, error } = useOne<ReturnNote>("return", `/returns/${id}`);
  const inv = ["return", "returns", "site-stock"];
  const dispatch = useAction(() => post(`/returns/${id}/dispatch`, { version: r!.version }), { success: "Return dispatched", invalidate: inv });
  const receive = useAction(() => post(`/returns/${id}/receive`, { version: r!.version }), { success: "Return received into central store", invalidate: inv });
  const cancel = useAction((reason: string) => post(`/returns/${id}/cancel`, { version: r!.version, reason }), { success: "Return cancelled", invalidate: inv });
  if (isLoading) return <LoadingRows />;
  if (error || !r) return <ErrorState error={error} />;
  const cols: Column<ReturnNote["items"][number]>[] = [
    { key: "m", header: "Material", cell: (i) => i.material.name },
    { key: "q", header: "Qty", align: "right", cell: (i) => `${fmtQty(i.qty)} ${i.material.uom}` },
    { key: "rq", header: "Received", align: "right", cell: (i) => fmtQty(i.received_qty) },
  ];
  return (
    <>
      <PageHeader
        back="/returns"
        title={r.return_no}
        badge={<StatusBadge status={r.status} />}
        subtitle={`${r.site.name} → central store`}
        actions={
          <>
            {["DRAFT", "DISPATCHED"].includes(r.status) && can("site.return") && <ConfirmAction label="Cancel" variant="outline" title="Cancel return?" comment="required" onConfirm={(c) => cancel.mutateAsync(c)} />}
            {r.status === "DRAFT" && can("site.return") && <ConfirmAction label="Dispatch" icon={<Send />} title="Dispatch to central store?" description="Quantities leave site stock now." onConfirm={() => dispatch.mutateAsync(undefined)} />}
            {r.status === "DISPATCHED" && can("return.receive") && <ConfirmAction label="Receive in full" variant="success" title="Receive returned material?" description="Quantities are added back to central stock at the site's average rate." onConfirm={() => receive.mutateAsync(undefined)} />}
          </>
        }
      />
      <div className="grid gap-4">
        <Card className="p-4 md:p-5">
          <InfoGrid items={[["Date", fmtDate(r.return_date)], ["Reason", r.reason], ["Vehicle", r.vehicle_no], ["Raised by", r.created_by_name]]} />
          {r.remarks && <p className="mt-3 text-sm text-muted-foreground">{r.remarks}</p>}
        </Card>
        <Section title="Items"><DataList rows={r.items} columns={cols} rowKey={(i) => i.id} dense /></Section>
      </div>
    </>
  );
}

export function ReturnFormPage() {
  const nav = useNavigate();
  const [site, setSite] = useDefaultSite();
  const [reason, setReason] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [remarks, setRemarks] = useState("");
  const [lines, setLines] = useState<CLine[]>([{ key: crypto.randomUUID(), material: null, qty: "" }]);
  const [idem] = useState(newIdempotencyKey);
  const { data: stock } = useQuery({ queryKey: ["site-stock", site], queryFn: async () => (await get<SiteStockLine[]>("/site/stock", { site_id: site })).data, enabled: !!site });
  const atSite = new Set((stock ?? []).filter((s) => s.qty > 0).map((s) => s.material.id));
  const save = useAction(
    () => post<ReturnNote>("/returns", { site_id: site, reason: reason || null, vehicle_no: vehicle || null, remarks: remarks || null, items: lines.filter((l) => l.material && Number(l.qty) > 0).map((l) => ({ material_id: l.material!.id, qty: Number(l.qty) })) }, { "Idempotency-Key": idem }),
    { success: (r) => `${r.return_no} created`, invalidate: ["returns"], onSuccess: (r) => nav(`/returns/${r.id}`, { replace: true }) },
  );
  const upd = (k: string, p: Partial<CLine>) => setLines((ls) => ls.map((l) => (l.key === k ? { ...l, ...p } : l)));
  return (
    <>
      <PageHeader back title="Return material to central store" />
      <div className="grid gap-4">
        <Card className="grid gap-4 p-4 sm:grid-cols-3 md:p-5">
          <Field label="From site"><SiteSelect value={site} onChange={setSite} /></Field>
          <Field label="Reason"><Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Excess / wrong spec / work stopped" /></Field>
          <Field label="Vehicle no."><Input value={vehicle} onChange={(e) => setVehicle(e.target.value.toUpperCase())} /></Field>
          <Field label="Remarks" className="sm:col-span-3"><Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
        </Card>
        <Section title="Material">
          <ul className="divide-y">
            {lines.map((l) => (
              <li key={l.key} className="grid grid-cols-[1fr_7rem_auto] items-end gap-2 p-4 md:px-5">
                <Field label="Material"><MaterialPicker value={l.material} onChange={(m) => upd(l.key, { material: m })} restrictTo={atSite} /></Field>
                <Field label="Qty"><QtyInput value={l.qty} onChange={(e) => upd(l.key, { qty: e.target.value })} /></Field>
                <Button variant="ghost" size="icon" aria-label="Remove" disabled={lines.length === 1} onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}><Trash2 /></Button>
              </li>
            ))}
          </ul>
          <div className="border-t p-3 md:px-5"><Button variant="outline" onClick={() => setLines((ls) => [...ls, { key: crypto.randomUUID(), material: null, qty: "" }])}><Plus /> Add material</Button></div>
        </Section>
        <ActionBar>
          <Button variant="outline" onClick={() => nav(-1)}>Cancel</Button>
          <Button loading={save.isPending} disabled={!site || !lines.some((l) => l.material && Number(l.qty) > 0)} onClick={() => save.mutate(undefined)}>Save draft</Button>
        </ActionBar>
      </div>
    </>
  );
}
