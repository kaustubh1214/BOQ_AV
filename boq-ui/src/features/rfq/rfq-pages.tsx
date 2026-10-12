import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Pencil, Plus, Send, Trash2, Truck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MaterialPicker, type PickedMaterial, SiteSelect, UnitSelect, useMySites } from "@/components/common/pickers";
import { ApprovalTimeline, Attachments, ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { errorCode, errorMessage, get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtQty, todayIso } from "@/lib/format";
import { post, put, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { Rfq, RfqSummary } from "@/lib/types";
import { cn, newIdempotencyKey } from "@/lib/utils";

const QUEUES: { key: string; label: string; perm: string }[] = [
  { key: "", label: "All", perm: "rfq.read" },
  { key: "mine", label: "My requests", perm: "rfq.create" },
  { key: "store", label: "Awaiting my approval", perm: "rfq.approve_store" },
  { key: "issue", label: "Ready to issue", perm: "issue.create" },
];

export function RfqListPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { values, page, set } = useUrlFilters({ queue: "", status: "", site_id: "" });
  const { data, isLoading, error } = useList<RfqSummary>("rfqs", "/rfqs", { ...values, page, limit: 20 });
  const columns: Column<RfqSummary>[] = [
    {
      key: "no",
      header: "Request",
      cell: (r) => (
        <div>
          <p className="flex items-center gap-1.5 font-medium">
            {r.request_no} {r.priority === "URGENT" && <Badge tone="red">Urgent</Badge>}
          </p>
          <p className="text-xs text-muted-foreground">{r.site.name} · {r.project_name}</p>
        </div>
      ),
    },
    { key: "date", header: "Raised", cell: (r) => fmtDate(r.request_date) },
    { key: "need", header: "Needed by", cell: (r) => fmtDate(r.required_by) },
    { key: "items", header: "Items", align: "right", cell: (r) => r.item_count },
    { key: "by", header: "Raised by", cell: (r) => r.created_by_name ?? "—" },
    { key: "status", header: "Status", mobile: false, cell: (r) => <StatusBadge status={r.status} /> },
  ];
  const queues = QUEUES.filter((q) => can(q.perm));
  return (
    <>
      <PageHeader
        title="Material requests (RFQ)"
        subtitle="Sites request material from the central store; the Store Manager approves and issues"
        actions={can("rfq.create") && (
          <Button asChild>
            <Link to="/rfqs/new">
              <Plus /> New request
            </Link>
          </Button>
        )}
      />
      <div className="-mx-4 mb-3 overflow-x-auto px-4 md:mx-0 md:px-0">
        <div className="flex w-max gap-1 rounded-lg bg-muted p-1">
          {queues.map((q) => (
            <button key={q.key} onClick={() => set({ queue: q.key })} className={cn("rounded-md px-3 py-1.5 text-sm whitespace-nowrap", values.queue === q.key ? "bg-card font-medium shadow-sm" : "text-muted-foreground")}>
              {q.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <SiteSelect value={values.site_id} onChange={(v) => set({ site_id: v })} allowEmpty className="w-full sm:w-64" />
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">Any status</option>
          {["DRAFT", "SUBMITTED", "RETURNED", "STORE_APPROVED", "PARTIALLY_ISSUED", "ISSUED", "REJECTED", "CANCELLED", "CLOSED"].map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>
          ))}
        </Select>
      </div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList rows={data?.data} columns={columns} rowKey={(r) => r.id} loading={isLoading} onRowClick={(r) => nav(`/rfqs/${r.id}`)} mobileMeta={(r) => <StatusBadge status={r.status} />} empty={<EmptyState title="No requests here" />} />
          <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ detail + approvals

interface AllocLine {
  material_id: string;
  material_name: string;
  has_allocation: boolean;
  allocated: number;
  approved_elsewhere: number;
  issued: number;
  this_request: number;
  remaining_after: number | null;
  exceeds: boolean;
}

export function RfqDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const { data: r, isLoading, error } = useOne<Rfq>("rfq", `/rfqs/${id}`);
  const { data: alloc } = useQuery({
    queryKey: ["rfq-alloc", id, r?.version],
    queryFn: async () => (await get<AllocLine[]>(`/rfqs/${id}/allocation-check`)).data,
    enabled: !!r && ["SUBMITTED", "PM_APPROVED"].includes(r.status) && can("rfq.approve_store"),
  });
  const [approving, setApproving] = useState<null | "approve" | "issue">(null);
  const inv = ["rfq", "rfqs", "rfq-alloc"];
  const submit = useAction(() => post(`/rfqs/${id}/submit`, { version: r!.version }), { success: "Submitted to the Store Manager", invalidate: inv });
  const reject = useAction((c: string) => post(`/rfqs/${id}/reject`, { version: r!.version, comment: c }), { success: "Request rejected", invalidate: inv });
  const sendBack = useAction((c: string) => post(`/rfqs/${id}/return`, { version: r!.version, comment: c }), { success: "Returned to site for changes", invalidate: inv });
  const cancel = useAction((c: string) => post(`/rfqs/${id}/cancel`, { version: r!.version, comment: c || null }), { success: "Request cancelled", invalidate: inv });
  const close = useAction((c: string) => post(`/rfqs/${id}/close`, { version: r!.version, comment: c || null }), { success: "Request closed", invalidate: inv });

  if (isLoading) return <LoadingRows />;
  if (error || !r) return <ErrorState error={error} />;
    const editable = ["DRAFT", "RETURNED"].includes(r.status) && can("rfq.create");
  const canStore = ["SUBMITTED", "PM_APPROVED"].includes(r.status) && can("rfq.approve_store");
  const canApproveAndIssue = canStore && can("issue.post");
  const canIssue = ["STORE_APPROVED", "PARTIALLY_ISSUED"].includes(r.status) && can("issue.create");
  const allocBy = Object.fromEntries((alloc ?? []).map((a) => [a.material_id, a]));

  const cols: Column<Rfq["items"][number]>[] = [
    {
      key: "m",
      header: "Material",
      cell: (i) => (
        <div>
          <p className="font-medium">{i.material.name}</p>
          {allocBy[i.material.id]?.exceeds && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
              <AlertTriangle className="size-3" /> Exceeds BOQ allocation by {fmtQty(-(allocBy[i.material.id].remaining_after ?? 0))}
            </p>
          )}
          {i.remarks && <p className="text-xs text-muted-foreground">{i.remarks}</p>}
        </div>
      ),
    },
    { key: "req", header: "Requested", align: "right", cell: (i) => `${fmtQty(i.requested_qty)} ${i.material.uom}` },
    { key: "app", header: "Approved", align: "right", cell: (i) => fmtQty(i.approved_qty) },
    { key: "iss", header: "Issued", align: "right", cell: (i) => fmtQty(i.issued_qty) },
    { key: "pend", header: "To issue", align: "right", cell: (i) => (i.pending_issue_qty > 0 ? <span className="font-semibold">{fmtQty(i.pending_issue_qty)}</span> : "—") },
    { key: "stock", header: "Central stock", align: "right", cell: (i) => <span className={i.central_stock < i.pending_issue_qty ? "text-destructive" : ""}>{fmtQty(i.central_stock)}</span> },
  ];

  return (
    <>
      <PageHeader
        back="/rfqs"
        title={r.request_no}
        badge={<><StatusBadge status={r.status} />{r.priority !== "NORMAL" && <StatusBadge status={r.priority} />}</>}
        subtitle={`${r.site.name} · ${r.project_name}`}
        actions={
          <>
            {editable && (
              <Button variant="outline" asChild>
                <Link to={`/rfqs/${r.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {editable && <ConfirmAction label="Submit" icon={<Send />} title="Submit request?" description="The Store Manager will be notified for approval." onConfirm={() => submit.mutateAsync(undefined)} />}
            {canStore && (
              <>
                <ConfirmAction label="Send back" variant="outline" title="Return to site for changes" comment="required" onConfirm={(c) => sendBack.mutateAsync(c)} />
                <ConfirmAction label="Reject" variant="outline" title="Reject request" comment="required" onConfirm={(c) => reject.mutateAsync(c)} />
                <Button variant={canApproveAndIssue ? "outline" : "success"} onClick={() => setApproving("approve")}>
                  <CheckCircle2 /> {canApproveAndIssue ? "Approve only" : "Approve"}
                </Button>
                {canApproveAndIssue && (
                  <Button variant="success" onClick={() => setApproving("issue")}>
                    <Truck /> Approve &amp; issue
                  </Button>
                )}
              </>
            )}
            {canIssue && (
              <Button onClick={() => nav(`/issue-notes/new?request=${r.id}`)}>
                <Truck /> Create issue note
              </Button>
            )}
            {canIssue && can("rfq.approve_store") && r.status === "PARTIALLY_ISSUED" && <ConfirmAction label="Close" variant="ghost" title="Close request?" description="No further material will be issued against it." comment="optional" onConfirm={(c) => close.mutateAsync(c)} />}
            {((["DRAFT", "SUBMITTED", "RETURNED"].includes(r.status) && can("rfq.create")) || (["SUBMITTED", "PM_APPROVED", "STORE_APPROVED"].includes(r.status) && can("rfq.approve_store"))) && (
              <ConfirmAction label="Cancel request" variant="ghost" title="Cancel this request?" comment="optional" onConfirm={(c) => cancel.mutateAsync(c)} />
            )}
          </>
        }
      />
      <div className="grid gap-4 md:gap-6">
        <Card className="p-4 md:p-5">
          <InfoGrid
            items={[
              ["Raised", `${fmtDate(r.request_date)} · ${r.created_by_name ?? ""}`],
              ["Needed by", fmtDate(r.required_by)],
              ["Store", r.store.name],
              ["First issue", fmtDateTime(r.first_issued_at)],
            ]}
          />
          {r.purpose && <p className="mt-3 text-sm"><span className="text-muted-foreground">Purpose: </span>{r.purpose}</p>}
        </Card>
        <Section title="Requested material">
          <DataList rows={r.items} columns={cols} rowKey={(i) => i.id} dense />
        </Section>
        {r.issue_notes.length > 0 && (
          <Section title="Issue notes">
            <ul className="divide-y">
              {r.issue_notes.map((n) => (
                <li key={n.id}>
                  <Link to={`/issue-notes/${n.id}`} className="flex items-center justify-between px-4 py-3 text-sm hover:bg-accent md:px-5">
                    <span className="font-medium">{n.issue_no}</span>
                    <span className="flex items-center gap-2">
                      <span className="text-muted-foreground">{fmtDate(n.issue_date)}</span>
                      <StatusBadge status={n.status} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Section>
        )}
        <div className="grid gap-4 md:gap-6 lg:grid-cols-2">
          <Section title="Approval history">
            <ApprovalTimeline items={r.approvals} />
          </Section>
          <Section title="Attachments">
            <Attachments entityType="rfq" entityId={r.id} canUpload={can("rfq.create", "rfq.approve_store")} />
          </Section>
        </div>
      </div>
      {approving && <ApproveDialog rfq={r} mode={approving} alloc={allocBy} onClose={() => setApproving(null)} onIssued={(noteId) => nav(`/issue-notes/${noteId}`)} />}
    </>
  );
}

function ApproveDialog({
  rfq,
  mode,
  alloc,
  onClose,
  onIssued,
}: {
  rfq: Rfq;
  mode: "approve" | "issue";
  alloc: Record<string, AllocLine>;
  onClose: () => void;
  onIssued: (noteId: string) => void;
}) {
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(rfq.items.map((i) => [i.id, String(i.approved_qty ?? i.requested_qty)])));
  const [comment, setComment] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [driver, setDriver] = useState("");
  const [override, setOverride] = useState(false);
  const [needsOverride, setNeedsOverride] = useState(false);
  const issuing = mode === "issue";
  const [idem] = useState(newIdempotencyKey);
  const approve = useAction(
    () => {
      const body = {
        version: rfq.version,
        comment: comment || null,
        override_allocation: override,
        items: rfq.items.map((i) => ({ item_id: i.id, approved_qty: Number(qty[i.id] || 0) })),
      };
      return issuing
        ? post<{ id: string }>(`/rfqs/${rfq.id}/approve-and-issue`, { ...body, vehicle_no: vehicle || null, driver_name: driver || null }, { "Idempotency-Key": idem })
        : post<unknown>(`/rfqs/${rfq.id}/store-approve`, body);
    },
    {
      success: issuing ? "Approved and issued — material is on its way" : "Approved — ready to issue",
      invalidate: ["rfq", "rfqs", "rfq-alloc", "issue-notes", "stock"],
      onSuccess: (out) => {
        onClose();
        if (issuing) onIssued((out as { id: string }).id);
      },
    },
  );
  const run = async () => {
    try {
      await approve.mutateAsync(undefined);
    } catch (e) {
      if (errorCode(e) === "ALLOCATION_EXCEEDED") setNeedsOverride(true);
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        title={issuing ? "Approve & issue" : "Approve request"}
        description={issuing ? "Reduce quantities if needed. Stock leaves the central store as soon as you confirm." : "You can reduce quantities. Set 0 to drop a line."}
        className="sm:max-w-xl"
      >
        <ul className="divide-y rounded-lg border">
          {rfq.items.map((i) => {
            const max = i.approved_qty ?? i.requested_qty;
            const a = alloc[i.material.id];
            const short = issuing && Number(qty[i.id] || 0) > i.central_stock;
            return (
              <li key={i.id} className="grid grid-cols-[1fr_7.5rem] items-center gap-3 p-3">
                <div className="min-w-0 text-sm">
                  <p className="truncate font-medium">{i.material.name}</p>
                  <p className={cn("text-xs text-muted-foreground", short && "text-destructive")}>
                    Asked {fmtQty(i.requested_qty)} {i.material.uom} · central stock {fmtQty(i.central_stock)}
                    {short && " — not enough stock"}
                    {a?.has_allocation && ` · allocation left ${fmtQty((a.allocated ?? 0) - (a.approved_elsewhere ?? 0))}`}
                  </p>
                </div>
                <QtyInput value={qty[i.id]} max={max} onChange={(e) => setQty((s) => ({ ...s, [i.id]: e.target.value }))} aria-label={`Approved quantity for ${i.material.name}`} />
              </li>
            );
          })}
        </ul>
        {issuing && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Vehicle no.">
              <Input value={vehicle} onChange={(e) => setVehicle(e.target.value.toUpperCase())} placeholder="MH12AB1234" maxLength={20} />
            </Field>
            <Field label="Driver name">
              <Input value={driver} onChange={(e) => setDriver(e.target.value)} maxLength={80} />
            </Field>
          </div>
        )}
        <Field label="Comment (optional)">
          <Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        {(needsOverride || Object.values(alloc).some((a) => a.exceeds)) && (
          <label className="flex items-start gap-2 rounded-md bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
            <input type="checkbox" className="mt-0.5 size-4" checked={override} onChange={(e) => setOverride(e.target.checked)} />
            This exceeds the project's BOQ allocation. Approve anyway (recorded as an override in the audit trail).
          </label>
        )}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button variant="success" loading={approve.isPending} onClick={run}>
            {issuing ? "Approve & issue" : "Approve"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ form

interface Line {
  key: string;
  material: PickedMaterial | null;
  qty: string;
  remarks: string;
  uom_id: string;
}
const blank = (): Line => ({ key: crypto.randomUUID(), material: null, qty: "", remarks: "", uom_id: "" });

export function RfqFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const editing = !!id;
  const sites = useMySites();
  const { data: existing } = useOne<Rfq>("rfq", id ? `/rfqs/${id}` : null);
  const [site, setSite] = useState("");
  const [requiredBy, setRequiredBy] = useState("");
  const [priority, setPriority] = useState("NORMAL");
  const [purpose, setPurpose] = useState("");
  const [lines, setLines] = useState<Line[]>([blank()]);
  const [idem] = useState(newIdempotencyKey);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!site && sites.length === 1) setSite(sites[0].id);
  }, [sites, site]);
  useEffect(() => {
    if (!existing) return;
    setSite(existing.site.id);
    setRequiredBy(existing.required_by ?? "");
    setPriority(existing.priority);
    setPurpose(existing.purpose ?? "");
    setLines(existing.items.map((i) => ({ key: i.id, material: i.material, qty: String(i.requested_qty), remarks: i.remarks ?? "", uom_id: "" })));
  }, [existing]);

  const update = (key: string, p: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...p } : l)));
  const items = lines.filter((l) => l.material && Number(l.qty) > 0).map((l) => ({ material_id: l.material!.id, requested_qty: Number(l.qty), uom_id: l.uom_id || null, remarks: l.remarks || null }));
  const valid = site && items.length > 0;

  const save = async (submit: boolean) => {
    setBusy(true);
    try {
      let r: Rfq;
      if (editing) {
        r = await put<Rfq>(`/rfqs/${id}`, { required_by: requiredBy || null, priority, purpose: purpose || null, items, version: existing!.version });
        if (submit) r = await post<Rfq>(`/rfqs/${id}/submit`, { version: r.version });
      } else {
        r = await post<Rfq>("/rfqs", { site_id: site, required_by: requiredBy || null, priority, purpose: purpose || null, items, submit }, { "Idempotency-Key": idem });
      }
      toast.success(submit ? `Request ${r.request_no} submitted` : "Draft saved");
      nav(`/rfqs/${r.id}`, { replace: true });
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader back title={editing ? `Edit ${existing?.request_no ?? "request"}` : "New material request"} subtitle="Ask the central store for material for your site" />
      <div className="grid gap-4">
        <Card className="grid gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-3">
          <Field label="Site *">
            {editing ? <Input value={existing?.site.name ?? ""} disabled /> : <SiteSelect value={site} onChange={setSite} />}
          </Field>
          <Field label="Needed by">
            <Input type="date" min={todayIso()} value={requiredBy} onChange={(e) => setRequiredBy(e.target.value)} />
          </Field>
          <Field label="Priority">
            <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="LOW">Low</option>
              <option value="NORMAL">Normal</option>
              <option value="URGENT">Urgent</option>
            </Select>
          </Field>
          <Field label="Purpose / activity" className="sm:col-span-2 lg:col-span-3">
            <Textarea rows={2} value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. Slab casting — Tower A level 6" />
          </Field>
        </Card>
        <Section title="Material needed">
          <ul className="divide-y">
            {lines.map((l, idx) => (
              <li key={l.key} className="grid gap-3 p-4 sm:grid-cols-[2fr_1fr_auto] sm:items-end md:px-5">
                <Field label={`${idx + 1}. Material`}>
                  <MaterialPicker value={l.material} onChange={(m) => update(l.key, { material: m, uom_id: "" })} />
                </Field>
                <Field label="Quantity">
                  <div className="flex gap-2">
                    <QtyInput value={l.qty} onChange={(e) => update(l.key, { qty: e.target.value })} />
                    <UnitSelect material={l.material} value={l.uom_id} className="w-24 shrink-0" onChange={(uom_id) => update(l.key, { uom_id })} />
                  </div>
                </Field>
                <Button type="button" variant="ghost" size="icon" className="justify-self-end" aria-label="Remove line" onClick={() => setLines((ls) => (ls.length > 1 ? ls.filter((x) => x.key !== l.key) : [blank()]))}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
          <div className="border-t p-3 md:px-5">
            <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, blank()])}>
              <Plus /> Add material
            </Button>
          </div>
        </Section>
        <ActionBar>
          <Button variant="outline" disabled={!valid || busy} onClick={() => save(false)}>
            Save draft
          </Button>
          <Button loading={busy} disabled={!valid} onClick={() => save(true)}>
            <Send /> Submit request
          </Button>
        </ActionBar>
      </div>
    </>
  );
}
