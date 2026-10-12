import { useQuery } from "@tanstack/react-query";
import { FileDown, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial, UnitSelect } from "@/components/common/pickers";
import { Attachments, ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtQty, fmtRate, todayIso } from "@/lib/format";
import { post, put, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { Boq, Grn, GrnSummary } from "@/lib/types";
import { newIdempotencyKey } from "@/lib/utils";

export function GrnListPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { values, page, set } = useUrlFilters({ status: "", q: "" });
  const { data, isLoading, error } = useList<GrnSummary>("grns", "/grns", { ...values, page, limit: 20 });
  const columns: Column<GrnSummary>[] = [
    { key: "no", header: "GRN", cell: (g) => <div><p className="font-medium">{g.grn_no}</p><p className="text-xs text-muted-foreground">{g.vendor_name}</p></div> },
    { key: "date", header: "Received", cell: (g) => fmtDate(g.received_date) },
    { key: "inv", header: "Invoice", cell: (g) => g.invoice_no ?? "—" },
    { key: "lines", header: "Lines", align: "right", cell: (g) => g.item_count },
    { key: "amt", header: "Amount", align: "right", cell: (g) => fmtMoney(g.total_amount) },
    { key: "status", header: "Status", mobile: false, cell: (g) => <StatusBadge status={g.status} /> },
  ];
  return (
    <>
      <PageHeader
        title="Inward (GRN)"
        subtitle="Material received into the central store against the BOQ"
        actions={can("grn.create") && (
          <Button asChild>
            <Link to="/grns/new">
              <Plus /> New GRN
            </Link>
          </Button>
        )}
      />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input placeholder="GRN / invoice / challan no." value={values.q} onChange={(e) => set({ q: e.target.value })} className="w-full sm:w-64" />
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="POSTED">Posted</option>
          <option value="CANCELLED">Cancelled</option>
        </Select>
      </div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList rows={data?.data} columns={columns} rowKey={(g) => g.id} loading={isLoading} onRowClick={(g) => nav(`/grns/${g.id}`)} mobileMeta={(g) => <StatusBadge status={g.status} />} empty={<EmptyState title="No goods receipts" />} />
          <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
        </Card>
      )}
    </>
  );
}

export function GrnDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: g, isLoading, error } = useOne<Grn>("grn", `/grns/${id}`);
  const inv = ["grn", "grns"];
  const postIt = useAction(() => post(`/grns/${id}/post`, { version: g!.version }), { success: "GRN posted — central stock updated", invalidate: inv });
  const cancel = useAction((reason: string) => post(`/grns/${id}/cancel`, { version: g!.version, reason }), { success: "GRN cancelled", invalidate: inv });
  if (isLoading) return <LoadingRows />;
  if (error || !g) return <ErrorState error={error} />;
  const cols: Column<Grn["items"][number]>[] = [
    { key: "m", header: "Material", cell: (i) => <div><p className="font-medium">{i.material.name}</p>{i.rejection_reason && <p className="text-xs text-destructive">Rejected: {i.rejection_reason}</p>}</div> },
    { key: "r", header: "Received", align: "right", cell: (i) => `${fmtQty(i.received_qty)} ${i.material.uom}` },
    { key: "rj", header: "Rejected", align: "right", cell: (i) => fmtQty(i.rejected_qty) },
    { key: "a", header: "Accepted", align: "right", cell: (i) => <span className="font-semibold">{fmtQty(i.accepted_qty)}</span> },
    { key: "rate", header: "Rate", align: "right", cell: (i) => fmtRate(i.rate) },
    { key: "gst", header: "GST", align: "right", cell: (i) => `${i.gst_rate}%` },
    { key: "amt", header: "Amount", align: "right", cell: (i) => fmtMoney(i.amount) },
  ];
  return (
    <>
      <PageHeader
        back="/grns"
        title={g.grn_no}
        badge={<StatusBadge status={g.status} />}
        subtitle={`${g.vendor_name} → ${g.store.name}`}
        actions={
          <>
            {g.status === "DRAFT" && can("grn.create") && (
              <Button variant="outline" asChild>
                <Link to={`/grns/${g.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {(g.status === "DRAFT" ? can("grn.create", "grn.post") : g.status === "POSTED" && can("grn.post")) && (
              <ConfirmAction
                label={g.status === "POSTED" ? "Reverse" : "Cancel"}
                variant="outline"
                title={g.status === "POSTED" ? "Reverse this GRN?" : "Cancel draft GRN?"}
                description={g.status === "POSTED" ? "Accepted quantity is taken back out of central stock at the GRN rate. Not possible if it was already issued." : undefined}
                comment="required"
                onConfirm={(r) => cancel.mutateAsync(r)}
              />
            )}
            {g.status === "DRAFT" && can("grn.post") && (
              <ConfirmAction
                label="Post to stock"
                variant="success"
                title="Post GRN?"
                description={"Accepted quantities are added to central stock and counted against the BOQ."}
                onConfirm={() => postIt.mutateAsync(undefined)}
              />
            )}
          </>
        }
      />
      <div className="grid gap-4 md:gap-6">
        <Card className="p-4 md:p-5">
          <InfoGrid
            items={[
              ["Received on", fmtDate(g.received_date)],
              ["BOQ", <Link className="text-primary hover:underline" to={`/boqs/${g.boq_id}`}>{g.boq_no}</Link>],
              ["Invoice", g.invoice_no ? `${g.invoice_no}${g.invoice_date ? ` · ${fmtDate(g.invoice_date)}` : ""}` : "—"],
              ["Challan / vehicle", [g.challan_no, g.vehicle_no].filter(Boolean).join(" · ") || "—"],
              ["Amount", fmtMoney(g.total_amount)],
              ["GST", fmtMoney(g.total_gst)],
              ["Prepared by", g.created_by_name],
              ["Posted", g.posted_at ? `${g.posted_by_name} · ${fmtDateTime(g.posted_at)}` : "—"],
            ]}
          />
          {g.remarks && <p className="mt-3 text-sm text-muted-foreground">{g.remarks}</p>}
          {g.cancel_reason && <p className="mt-3 text-sm text-destructive">Cancelled: {g.cancel_reason}</p>}
        </Card>
        <Section title="Items">
          <DataList rows={g.items} columns={cols} rowKey={(i) => i.id} dense />
        </Section>
        <Section title="Invoice / challan photos">
          <Attachments entityType="grn" entityId={g.id} canUpload={can("grn.create", "grn.post")} />
        </Section>
      </div>
    </>
  );
}

interface Line {
  key: string;
  material: PickedMaterial | null;
  received_qty: string;
  rejected_qty: string;
  rate: string;
  rejection_reason: string;
  uom_id: string;
  factor: number;
}
const blank = (): Line => ({ key: crypto.randomUUID(), material: null, received_qty: "", rejected_qty: "", rate: "", rejection_reason: "", uom_id: "", factor: 1 });

export function GrnFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const editing = !!id;
  const { data: existing } = useOne<Grn>("grn", id ? `/grns/${id}` : null);
  const { data: boq } = useQuery({ queryKey: ["boq-current"], queryFn: async () => (await get<Boq | null>("/boqs/current")).data });
  const [vendor, setVendor] = useState("");
  const [date, setDate] = useState(todayIso());
  const [invoice, setInvoice] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [challan, setChallan] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [remarks, setRemarks] = useState("");
  const [lines, setLines] = useState<Line[]>([blank()]);
  const [idem] = useState(newIdempotencyKey);
  const planned = useMemo(() => new Set(boq?.items.map((i) => i.material.id)), [boq]);
  const plannedBy = useMemo(() => Object.fromEntries((boq?.items ?? []).map((i) => [i.material.id, i])), [boq]);

  useEffect(() => {
    if (!existing) return;
    setVendor(existing.vendor_id);
    setDate(existing.received_date);
    setInvoice(existing.invoice_no ?? "");
    setInvoiceDate(existing.invoice_date ?? "");
    setChallan(existing.challan_no ?? "");
    setVehicle(existing.vehicle_no ?? "");
    setRemarks(existing.remarks ?? "");
    setLines(existing.items.map((i) => ({ key: i.id, material: i.material, received_qty: String(i.received_qty), rejected_qty: String(i.rejected_qty || ""), rate: String(i.rate ?? ""), rejection_reason: i.rejection_reason ?? "", uom_id: "", factor: 1 })));
  }, [existing]);

  const update = (key: string, p: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...p } : l)));
  const save = useAction(
    () => {
      const body = {
        vendor_id: vendor,
        received_date: date,
        invoice_no: invoice || null,
        invoice_date: invoiceDate || null,
        challan_no: challan || null,
        vehicle_no: vehicle || null,
        remarks: remarks || null,
        items: lines.filter((l) => l.material).map((l) => ({
          material_id: l.material!.id,
          received_qty: Number(l.received_qty),
          rejected_qty: Number(l.rejected_qty || 0),
          rate: Number(l.rate),
          uom_id: l.uom_id || null,
          rejection_reason: l.rejection_reason || null,
        })),
      };
      return editing ? put<Grn>(`/grns/${id}`, { ...body, version: existing!.version }) : post<Grn>("/grns", body, { "Idempotency-Key": idem });
    },
    { success: "GRN saved as draft", invalidate: ["grns", "grn"], onSuccess: (g) => nav(`/grns/${g.id}`) },
  );
  const valid = vendor && lines.some((l) => l.material && Number(l.received_qty) > 0 && l.rate !== "");

  if (boq === null) return <><PageHeader back title="New GRN" /><EmptyState title="No approved BOQ" hint="Goods are received against the approved BOQ of the financial year. Ask management to approve one first." /></>;

  return (
    <>
      <PageHeader back title={editing ? `Edit ${existing?.grn_no ?? "GRN"}` : "New GRN"} subtitle={boq ? `Receiving against ${boq.boq_no}` : undefined} />
      <div className="grid gap-4">
        <Card className="grid gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-3">
          <Field label="Vendor *">
            <MasterSelect path="vendors" value={vendor} onChange={setVendor} placeholder="Select vendor" />
          </Field>
          <Field label="Received on">
            <Input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="Vehicle no.">
            <Input value={vehicle} onChange={(e) => setVehicle(e.target.value.toUpperCase())} placeholder="MH12AB1234" />
          </Field>
          <Field label="Invoice no.">
            <Input value={invoice} onChange={(e) => setInvoice(e.target.value)} />
          </Field>
          <Field label="Invoice date">
            <Input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} />
          </Field>
          <Field label="Challan no.">
            <Input value={challan} onChange={(e) => setChallan(e.target.value)} />
          </Field>
          <Field label="Remarks" className="sm:col-span-2 lg:col-span-3">
            <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </Field>
        </Card>
        <Section title="Items received">
          <ul className="divide-y">
            {lines.map((l, idx) => {
              const p = l.material ? plannedBy[l.material.id] : undefined;
              const accepted = (Number(l.received_qty) || 0) - (Number(l.rejected_qty) || 0);
              return (
                <li key={l.key} className="grid gap-3 p-4 sm:grid-cols-2 md:px-5 lg:grid-cols-[2fr_1fr_1fr_1fr_auto] lg:items-end">
                  <Field label={`${idx + 1}. Material (from BOQ)`} hint={p ? `BOQ rate ₹${p.estimated_rate ?? "—"} · planned ${fmtQty(p.planned_qty)} ${p.material.uom}` : undefined} className="sm:col-span-2 lg:col-span-1">
                    <MaterialPicker value={l.material} onChange={(m) => update(l.key, { material: m, uom_id: "", factor: 1, rate: l.rate || String(plannedBy[m.id]?.estimated_rate ?? "") })} restrictTo={planned} />
                  </Field>
                  <Field label="Received">
                    <div className="flex gap-2">
                      <QtyInput value={l.received_qty} onChange={(e) => update(l.key, { received_qty: e.target.value })} />
                      <UnitSelect
                        material={l.material}
                        value={l.uom_id}
                        className="w-24 shrink-0"
                        onChange={(uom_id, factor) => {
                          // keep an auto-filled BOQ rate in step with the unit (rate is per selected unit)
                          const base = l.material ? Number(plannedBy[l.material.id]?.estimated_rate) : NaN;
                          const wasAuto = l.rate === "" || Math.abs(Number(l.rate) - base * l.factor) < 1e-6;
                          update(l.key, { uom_id, factor, ...(wasAuto && !Number.isNaN(base) ? { rate: String(Number((base * factor).toFixed(4))) } : {}) });
                        }}
                      />
                    </div>
                  </Field>
                  <Field label="Rejected">
                    <QtyInput value={l.rejected_qty} onChange={(e) => update(l.key, { rejected_qty: e.target.value })} />
                  </Field>
                  <Field label={`Rate excl. GST (₹${l.material ? ` per ${l.uom_id ? "selected unit" : l.material.uom}` : ""})`}>
                    <QtyInput value={l.rate} onChange={(e) => update(l.key, { rate: e.target.value })} />
                  </Field>
                  <div className="flex items-center justify-between gap-2 sm:col-span-2 lg:col-span-1">
                    <span className="text-xs text-muted-foreground lg:hidden">Accepted {fmtQty(accepted)}</span>
                    <Button type="button" variant="ghost" size="icon" aria-label="Remove line" onClick={() => setLines((ls) => (ls.length > 1 ? ls.filter((x) => x.key !== l.key) : [blank()]))}>
                      <Trash2 />
                    </Button>
                  </div>
                  {Number(l.rejected_qty) > 0 && (
                    <Field label="Rejection reason" className="sm:col-span-2 lg:col-span-5">
                      <Input value={l.rejection_reason} onChange={(e) => update(l.key, { rejection_reason: e.target.value })} />
                    </Field>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="border-t p-3 md:px-5">
            <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, blank()])}>
              <Plus /> Add item
            </Button>
          </div>
        </Section>
        <ActionBar>
          <Button variant="outline" onClick={() => nav(-1)}>
            Cancel
          </Button>
          <Button loading={save.isPending} disabled={!valid} onClick={() => save.mutate(undefined)}>
            <FileDown /> Save draft
          </Button>
        </ActionBar>
      </div>
    </>
  );
}
