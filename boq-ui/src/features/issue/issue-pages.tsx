import { FileText, PackageCheck, Truck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { SiteSelect } from "@/components/common/pickers";
import { Attachments, ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { download, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtQty, fmtRate, todayIso } from "@/lib/format";
import { post, put, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { IssueNote, IssueSummary, Rfq } from "@/lib/types";
import { cn, newIdempotencyKey } from "@/lib/utils";

export function IssueListPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { values, page, set } = useUrlFilters({ queue: "", status: "", site_id: "" });
  const { data, isLoading, error } = useList<IssueSummary>("issues", "/issue-notes", { ...values, page, limit: 20 });
  const tabs = [
    { key: "", label: "All", show: true },
    { key: "post", label: "Drafts to dispatch", show: can("issue.post") },
    { key: "receive", label: "To receive at site", show: can("issue.receive") },
  ].filter((t) => t.show);
  const columns: Column<IssueSummary>[] = [
    { key: "no", header: "Issue note", cell: (n) => <div><p className="font-medium">{n.issue_no}</p><p className="text-xs text-muted-foreground">{n.site.name} · {n.request_no}</p></div> },
    { key: "date", header: "Date", cell: (n) => fmtDate(n.issue_date) },
    { key: "veh", header: "Vehicle", cell: (n) => n.vehicle_no ?? "—" },
    { key: "items", header: "Items", align: "right", cell: (n) => n.item_count },
    { key: "val", header: "Value", align: "right", cell: (n) => fmtMoney(n.total_value) },
    { key: "status", header: "Status", mobile: false, cell: (n) => <StatusBadge status={n.status} /> },
  ];
  return (
    <>
      <PageHeader title="Issue notes" subtitle="Material dispatched from the central store to sites" />
      {tabs.length > 1 && (
        <div className="-mx-4 mb-3 overflow-x-auto px-4 md:mx-0 md:px-0">
          <div className="flex w-max gap-1 rounded-lg bg-muted p-1">
            {tabs.map((t) => (
              <button key={t.key} onClick={() => set({ queue: t.key })} className={cn("whitespace-nowrap rounded-md px-3 py-1.5 text-sm", values.queue === t.key ? "bg-card font-medium shadow-sm" : "text-muted-foreground")}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <SiteSelect value={values.site_id} onChange={(v) => set({ site_id: v })} allowEmpty className="w-full sm:w-64" />
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">Any status</option>
          {["DRAFT", "ISSUED", "RECEIVED", "PARTIALLY_RECEIVED", "CANCELLED"].map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>
          ))}
        </Select>
      </div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList rows={data?.data} columns={columns} rowKey={(n) => n.id} loading={isLoading} onRowClick={(n) => nav(`/issue-notes/${n.id}`)} mobileMeta={(n) => <StatusBadge status={n.status} />} empty={<EmptyState title={values.queue === "receive" ? "Nothing on the way to your site" : "No issue notes"} />} />
          <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
        </Card>
      )}
    </>
  );
}

export function IssueDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const { data: n, isLoading, error } = useOne<IssueNote>("issue", `/issue-notes/${id}`);
  const [receiving, setReceiving] = useState(false);
  const inv = ["issue", "issues", "rfq", "rfqs"];
  const issue = useAction(() => post(`/issue-notes/${id}/issue`, { version: n!.version }), { success: "Issued — material is on the way", invalidate: inv });
  const cancel = useAction((reason: string) => post(`/issue-notes/${id}/cancel`, { version: n!.version, reason }), { success: "Issue note cancelled", invalidate: inv });
  if (isLoading) return <LoadingRows />;
  if (error || !n) return <ErrorState error={error} />;
  const cols: Column<IssueNote["items"][number]>[] = [
    { key: "m", header: "Material", cell: (i) => <div><p className="font-medium">{i.material.name}</p><p className="text-xs text-muted-foreground">{i.material.code}</p></div> },
    { key: "iss", header: "Issued", align: "right", cell: (i) => `${fmtQty(i.issued_qty)} ${i.material.uom}` },
    ...(n.status === "DRAFT" ? [{ key: "st", header: "Central stock", align: "right" as const, cell: (i: IssueNote["items"][number]) => <span className={i.central_stock < i.issued_qty ? "font-semibold text-destructive" : ""}>{fmtQty(i.central_stock)}</span> }] : []),
    { key: "rec", header: "Received", align: "right", cell: (i) => fmtQty(i.received_qty) },
    { key: "loss", header: "Short / damaged", align: "right", cell: (i) => (i.shortage_qty || i.damaged_qty ? <span className="text-amber-600 dark:text-amber-400">{fmtQty(i.shortage_qty)} / {fmtQty(i.damaged_qty)}</span> : "—") },
    { key: "rate", header: "Rate (avg)", align: "right", cell: (i) => fmtRate(i.rate) },
    { key: "amt", header: "Amount", align: "right", cell: (i) => fmtMoney(i.amount) },
  ];
  return (
    <>
      <PageHeader
        back="/issue-notes"
        title={n.issue_no}
        badge={<StatusBadge status={n.status} />}
        subtitle={`${n.from_store.name} → ${n.site.name} (${n.project_name})`}
        actions={
          <>
            <Button variant="outline" onClick={() => download(`/issue-notes/${n.id}/pdf`, undefined, "open").catch((e) => toast.error(errorMessage(e)))}>
              <FileText /> Gate pass PDF
            </Button>
            {(n.status === "DRAFT" ? can("issue.create", "issue.post") : n.status === "ISSUED" && can("issue.post")) && (
              <ConfirmAction
                label={n.status === "ISSUED" ? "Recall" : "Cancel"}
                variant="outline"
                title={n.status === "ISSUED" ? "Recall this issue note?" : "Cancel draft?"}
                description={n.status === "ISSUED" ? "Stock goes back into the central store and the request lines reopen." : undefined}
                comment="required"
                onConfirm={(r) => cancel.mutateAsync(r)}
              />
            )}
            {n.status === "DRAFT" && can("issue.post") && (
              <ConfirmAction
                label="Issue / dispatch"
                icon={<Truck />}
                variant="success"
                title="Dispatch material?"
                description={"Central stock is reduced at the current weighted-average rate; the site is notified."}
                onConfirm={() => issue.mutateAsync(undefined)}
              />
            )}
            {n.status === "ISSUED" && can("issue.receive") && (
              <Button variant="success" onClick={() => setReceiving(true)}>
                <PackageCheck /> Receive at site
              </Button>
            )}
          </>
        }
      />
      <div className="grid gap-4 md:gap-6">
        <Card className="p-4 md:p-5">
          <InfoGrid
            items={[
              ["Request", <Link to={`/rfqs/${n.request_id}`} className="text-primary hover:underline">{n.request_no}</Link>],
              ["Issue date", fmtDate(n.issue_date)],
              ["Vehicle", n.vehicle_no],
              ["Driver", [n.driver_name, n.driver_phone].filter(Boolean).join(" · ") || "—"],
              ["Value", fmtMoney(n.total_value)],
              ["Prepared by", n.created_by_name],
              ["Dispatched", n.issued_at ? `${n.issued_by_name} · ${fmtDateTime(n.issued_at)}` : "—"],
              ["Received", n.received_at ? `${n.received_by_name} · ${fmtDateTime(n.received_at)}` : "—"],
            ]}
          />
          {n.remarks && <p className="mt-3 text-sm text-muted-foreground">{n.remarks}</p>}
          {n.receipt_remarks && <p className="mt-2 text-sm"><span className="text-muted-foreground">Site remarks: </span>{n.receipt_remarks}</p>}
          {n.cancel_reason && <p className="mt-2 text-sm text-destructive">Cancelled: {n.cancel_reason}</p>}
        </Card>
        <Section title="Items">
          <DataList rows={n.items} columns={cols} rowKey={(i) => i.id} dense />
        </Section>
        <Section title="Delivery photos / signed gate pass">
          <Attachments entityType="issue_note" entityId={n.id} canUpload={can("issue.create", "issue.post", "issue.receive")} />
        </Section>
      </div>
      {receiving && <ReceiveDialog note={n} onClose={() => setReceiving(false)} />}
    </>
  );
}

function ReceiveDialog({ note, onClose }: { note: IssueNote; onClose: () => void }) {
  const [rows, setRows] = useState(() => Object.fromEntries(note.items.map((i) => [i.id, { received: String(i.issued_qty), short: "", damaged: "" }])));
  const [remarks, setRemarks] = useState("");
  const receive = useAction(
    () =>
      post(`/issue-notes/${note.id}/receive`, {
        version: note.version,
        remarks: remarks || null,
        items: note.items.map((i) => ({
          item_id: i.id,
          received_qty: Number(rows[i.id].received || 0),
          shortage_qty: Number(rows[i.id].short || 0),
          damaged_qty: Number(rows[i.id].damaged || 0),
        })),
      }),
    { success: "Receipt recorded — site stock updated", invalidate: ["issue", "issues", "site-stock"], onSuccess: onClose },
  );
  const mismatch = note.items.filter((i) => {
    const r = rows[i.id];
    return Math.abs(Number(r.received || 0) + Number(r.short || 0) + Number(r.damaged || 0) - i.issued_qty) > 0.0005;
  });
  const set = (id: string, k: "received" | "short" | "damaged", v: string) => setRows((s) => ({ ...s, [id]: { ...s[id], [k]: v } }));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Receive material" description="Received + short + damaged must equal the issued quantity." className="sm:max-w-xl">
        <ul className="grid gap-3">
          {note.items.map((i) => (
            <li key={i.id} className={cn("rounded-lg border p-3", mismatch.includes(i) && "border-destructive")}>
              <p className="text-sm font-medium">{i.material.name}</p>
              <p className="mb-2 text-xs text-muted-foreground">Issued {fmtQty(i.issued_qty)} {i.material.uom}</p>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Received"><QtyInput value={rows[i.id].received} onChange={(e) => set(i.id, "received", e.target.value)} /></Field>
                <Field label="Short"><QtyInput value={rows[i.id].short} onChange={(e) => set(i.id, "short", e.target.value)} /></Field>
                <Field label="Damaged"><QtyInput value={rows[i.id].damaged} onChange={(e) => set(i.id, "damaged", e.target.value)} /></Field>
              </div>
            </li>
          ))}
        </ul>
        <Field label="Remarks">
          <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} placeholder="e.g. 1 bag torn" />
        </Field>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button variant="success" disabled={mismatch.length > 0} loading={receive.isPending} onClick={() => receive.mutate(undefined)}>
            <PackageCheck /> Confirm receipt
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function IssueFormPage() {
  const { id } = useParams();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const editing = !!id;
  const { data: existing } = useOne<IssueNote>("issue", id ? `/issue-notes/${id}` : null);
  const requestId = existing?.request_id ?? sp.get("request");
  const { data: rfq, isLoading } = useOne<Rfq>("rfq", requestId ? `/rfqs/${requestId}` : null);
  const [qty, setQty] = useState<Record<string, string>>({});
  const [date, setDate] = useState(todayIso());
  const [vehicle, setVehicle] = useState("");
  const [driver, setDriver] = useState("");
  const [phone, setPhone] = useState("");
  const [remarks, setRemarks] = useState("");
  const [idem] = useState(newIdempotencyKey);

  useEffect(() => {
    if (existing) {
      setQty(Object.fromEntries(existing.items.map((i) => [i.request_item_id, String(i.issued_qty)])));
      setDate(existing.issue_date);
      setVehicle(existing.vehicle_no ?? "");
      setDriver(existing.driver_name ?? "");
      setPhone(existing.driver_phone ?? "");
      setRemarks(existing.remarks ?? "");
    } else if (rfq) {
      setQty(Object.fromEntries(rfq.items.map((i) => [i.id, String(Math.min(i.pending_issue_qty, i.central_stock))])));
    }
  }, [existing, rfq]);

  const items = Object.entries(qty).filter(([, v]) => Number(v) > 0).map(([k, v]) => ({ request_item_id: k, issued_qty: Number(v) }));
  const save = useAction(
    () => {
      const body = { issue_date: date, vehicle_no: vehicle || null, driver_name: driver || null, driver_phone: phone || null, remarks: remarks || null, items };
      return editing ? put<IssueNote>(`/issue-notes/${id}`, { ...body, version: existing!.version }) : post<IssueNote>("/issue-notes", { ...body, request_id: requestId }, { "Idempotency-Key": idem });
    },
    { success: (n) => `${n.issue_no} saved as draft`, invalidate: ["issues", "issue", "rfq"], onSuccess: (n) => nav(`/issue-notes/${n.id}`, { replace: true }) },
  );

  if (!requestId) return <><PageHeader back title="New issue note" /><EmptyState title="Pick an approved request first" hint="Issue notes are created from a store-approved material request." action={<Button asChild><Link to="/rfqs?queue=issue">Requests ready to issue</Link></Button>} /></>;
  if (isLoading || !rfq) return <LoadingRows />;

  return (
    <>
      <PageHeader back title={editing ? `Edit ${existing?.issue_no}` : "New issue note"} subtitle={`${rfq.request_no} · ${rfq.site.name} (${rfq.project_name})`} />
      <div className="grid gap-4">
        <Section title="Quantities to issue">
          <ul className="divide-y">
            {rfq.items.map((i) => {
              const remaining = i.pending_issue_qty; // approved − already dispatched (drafts are not dispatched yet)
              const v = Number(qty[i.id] || 0);
              return (
                <li key={i.id} className="grid grid-cols-[1fr_8rem] items-center gap-3 px-4 py-3 md:px-5">
                  <div className="min-w-0 text-sm">
                    <p className="truncate font-medium">{i.material.name}</p>
                    <p className="text-xs text-muted-foreground">
                      Approved {fmtQty(i.approved_qty)} · left {fmtQty(remaining)} · stock <span className={i.central_stock < v ? "font-semibold text-destructive" : ""}>{fmtQty(i.central_stock)}</span> {i.material.uom}
                    </p>
                  </div>
                  <QtyInput value={qty[i.id] ?? ""} max={remaining} onChange={(e) => setQty((s) => ({ ...s, [i.id]: e.target.value }))} aria-label={`Issue quantity for ${i.material.name}`} />
                </li>
              );
            })}
          </ul>
        </Section>
        <Card className="grid gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-4">
          <Field label="Issue date"><Input type="date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Vehicle no."><Input value={vehicle} onChange={(e) => setVehicle(e.target.value.toUpperCase())} /></Field>
          <Field label="Driver"><Input value={driver} onChange={(e) => setDriver(e.target.value)} /></Field>
          <Field label="Driver phone"><Input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
          <Field label="Remarks" className="sm:col-span-2 lg:col-span-4"><Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
        </Card>
        <ActionBar>
          <Button variant="outline" onClick={() => nav(-1)}>Cancel</Button>
          <Button loading={save.isPending} disabled={!items.length} onClick={() => save.mutate(undefined)}>Save draft</Button>
        </ActionBar>
      </div>
    </>
  );
}
