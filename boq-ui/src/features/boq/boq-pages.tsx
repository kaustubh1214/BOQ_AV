import { useQuery } from "@tanstack/react-query";
import { CopyPlus, Pencil, Plus, Send, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { ActionBar, EmptyState, ErrorState, InfoGrid, LoadingRows, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial, UnitSelect } from "@/components/common/pickers";
import { ApprovalTimeline, Attachments, ConfirmAction, QtyInput } from "@/components/common/widgets";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Progress } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney, fmtPct, fmtQty, fmtRate } from "@/lib/format";
import { post, put, useAction, useList, useOne, useUrlFilters } from "@/lib/hooks";
import type { Boq, BoqSummary, ProgressLine, Site } from "@/lib/types";
import { newIdempotencyKey } from "@/lib/utils";

export function BoqListPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { values, page, set } = useUrlFilters({ status: "" });
  const { data, isLoading, error } = useList<BoqSummary>("boqs", "/boqs", { status: values.status, page, limit: 20 });
  const columns: Column<BoqSummary>[] = [
    { key: "no", header: "BOQ", cell: (b) => <div><p className="font-medium">{b.boq_no}</p><p className="text-xs text-muted-foreground">{b.title}</p></div> },
    { key: "fy", header: "FY", cell: (b) => b.financial_year },
    { key: "items", header: "Lines", align: "right", cell: (b) => b.item_count },
    { key: "value", header: "Estimated value", align: "right", cell: (b) => fmtMoney(b.total_estimated_value) },
    { key: "by", header: "Prepared by", cell: (b) => b.created_by_name ?? "—" },
    { key: "status", header: "Status", mobile: false, cell: (b) => <StatusBadge status={b.status} /> },
  ];
  return (
    <>
      <PageHeader
        title="BOQ — central store master"
        subtitle="What management plans to need this financial year, with estimated rates"
        actions={can("boq.create") && (
          <Button asChild>
            <Link to="/boqs/new">
              <Plus /> New BOQ
            </Link>
          </Button>
        )}
      />
      <div className="mb-3 flex gap-2">
        <Select value={values.status} onChange={(e) => set({ status: e.target.value })} className="w-auto">
          <option value="">All statuses</option>
          {["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "SUPERSEDED", "CLOSED"].map((s) => (
            <option key={s} value={s}>
              {s[0] + s.slice(1).toLowerCase()}
            </option>
          ))}
        </Select>
      </div>
      {error ? <ErrorState error={error} /> : (
        <Card className="overflow-hidden">
          <DataList rows={data?.data} columns={columns} rowKey={(b) => b.id} loading={isLoading} onRowClick={(b) => nav(`/boqs/${b.id}`)} mobileMeta={(b) => <StatusBadge status={b.status} />} empty={<EmptyState title="No BOQs yet" hint="Create the central BOQ for this financial year." />} />
          <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={(p) => set({ page: p })} />
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ detail

export function BoqDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can, me } = useAuth();
  const { data: boq, isLoading, error } = useOne<Boq>("boq", `/boqs/${id}`);
  const { data: progress } = useQuery({ queryKey: ["boq-progress", id], queryFn: async () => (await get<{ lines: ProgressLine[]; totals: Record<string, number | null> }>(`/boqs/${id}/progress`)).data, enabled: !!boq && boq.status !== "DRAFT" });
  const inv = ["boq", "boqs", "boq-progress"];
  const submit = useAction(() => post<Boq>(`/boqs/${id}/submit`, { version: boq!.version }), { success: (b) => (b.status === "APPROVED" ? "BOQ approved and live" : "Submitted for approval"), invalidate: inv });
  const approve = useAction((comment: string) => post(`/boqs/${id}/approve`, { version: boq!.version, comment: comment || null }), { success: "BOQ approved — it is now the live central store master", invalidate: inv });
  const reject = useAction((comment: string) => post(`/boqs/${id}/reject`, { version: boq!.version, comment }), { success: "BOQ rejected", invalidate: inv });
  const revise = useAction(() => post<Boq>(`/boqs/${id}/revise`), { success: (b) => `Revision ${b.boq_no} created`, invalidate: inv, onSuccess: (b) => nav(`/boqs/${b.id}/edit`) });
  const close = useAction((comment: string) => post(`/boqs/${id}/close`, { version: boq!.version, comment: comment || null }), { success: "BOQ closed", invalidate: inv });
  const cancel = useAction(() => post(`/boqs/${id}/cancel`, { version: boq!.version }), { success: "Draft discarded", invalidate: inv });

  if (isLoading) return <LoadingRows />;
  if (error || !boq) return <ErrorState error={error} />;
  const editable = ["DRAFT", "REJECTED"].includes(boq.status) && can("boq.create");
  const autoApprove = can("boq.approve");
  const lines = progress?.lines ?? [];

  return (
    <>
      <PageHeader
        back="/boqs"
        title={boq.boq_no}
        badge={<StatusBadge status={boq.status} />}
        subtitle={`${boq.title} · FY ${boq.financial_year}${boq.revision ? ` · Revision ${boq.revision}` : ""}`}
        actions={
          <>
            {editable && (
              <Button variant="outline" asChild>
                <Link to={`/boqs/${boq.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {editable && <ConfirmAction label="Discard" variant="ghost" title="Discard this draft?" onConfirm={() => cancel.mutateAsync(undefined)} />}
            {editable && <ConfirmAction label={autoApprove ? "Submit & approve" : "Submit"} icon={<Send />} title={autoApprove ? "Submit and approve this BOQ?" : "Submit BOQ for approval?"} description={autoApprove ? "It becomes the live central store master for the year; any earlier revision is superseded." : "The approver will be notified."} onConfirm={() => submit.mutateAsync(undefined)} />}
            {boq.status === "SUBMITTED" && can("boq.approve") && (
              <>
                <ConfirmAction label="Reject" variant="outline" title="Reject BOQ" comment="required" onConfirm={(c) => reject.mutateAsync(c)} />
                <ConfirmAction label="Approve" variant="success" title={`Approve ${boq.boq_no}?`} description="It becomes the live central store master for the year; any earlier revision is superseded." comment="optional" onConfirm={(c) => approve.mutateAsync(c)} />
              </>
            )}
            {boq.status === "APPROVED" && can("boq.create") && <ConfirmAction label="Revise" icon={<CopyPlus />} variant="outline" title="Start a revision?" description="Creates an editable copy. The current BOQ stays live until the revision is approved." onConfirm={() => revise.mutateAsync(undefined)} />}
            {boq.status === "APPROVED" && can("boq.approve") && <ConfirmAction label="Close" variant="ghost" title="Close BOQ for the year?" description="No further GRNs can be received against it." comment="optional" onConfirm={(c) => close.mutateAsync(c)} />}
          </>
        }
      />
      <div className="grid gap-4 md:gap-6">
        <Card className="p-4 md:p-5">
          <InfoGrid
            items={[
              ["Estimated value", fmtMoney(boq.total_estimated_value)],
              ["Materials", boq.items.length],
              ["Prepared by", boq.created_by_name],
              ["Approved", boq.approved_at ? `${boq.approved_by_name} · ${fmtDate(boq.approved_at)}` : "—"],
            ]}
          />
          {boq.remarks && <p className="mt-3 text-sm text-muted-foreground">{boq.remarks}</p>}
        </Card>

        {lines.length > 0 ? <ProgressTable lines={lines} totals={progress!.totals} showFinance={!!me?.show_finance} /> : <PlannedTable boq={boq} />}

        {boq.items.some((i) => i.allocations.length) && <AllocationTable boq={boq} />}

        <div className="grid gap-4 md:gap-6 lg:grid-cols-2">
          <Section title="Approval history">
            <ApprovalTimeline items={boq.approvals} />
          </Section>
          <Section title="Attachments">
            <Attachments entityType="boq" entityId={boq.id} canUpload={can("boq.create", "boq.approve")} />
          </Section>
        </div>
      </div>
    </>
  );
}

/** The sketch: Planned 1,00,000 → Inward 10,000 → Pending 90,000 → Issued → In stock. */
function ProgressTable({ lines, totals, showFinance }: { lines: ProgressLine[]; totals: Record<string, number | null>; showFinance: boolean }) {
  const cols: Column<ProgressLine>[] = [
    {
      key: "mat",
      header: "Material",
      cell: (l) => (
        <div className="min-w-44">
          <p className="font-medium">{l.material_name}</p>
          <div className="mt-1.5 flex items-center gap-2">
            <Progress value={l.procurement_pct} className="h-1.5 max-w-40" />
            <span className="text-xs tabular-nums text-muted-foreground">{fmtPct(l.procurement_pct)} in</span>
          </div>
        </div>
      ),
    },
    { key: "planned", header: "Planned", align: "right", cell: (l) => <Q v={l.planned_qty} u={l.uom} /> },
    { key: "inward", header: "Inward", align: "right", cell: (l) => <Q v={l.accepted_inward} u={l.uom} /> },
    { key: "pending", header: "Pending to procure", align: "right", cell: (l) => <span className={l.pending_to_procure > 0 ? "font-semibold text-amber-600 dark:text-amber-400" : ""}><Q v={l.pending_to_procure} u={l.uom} /></span> },
    { key: "issued", header: "Issued to sites", align: "right", cell: (l) => <Q v={l.issued_qty} u={l.uom} /> },
    { key: "stock", header: "Central stock", align: "right", cell: (l) => <Q v={l.central_stock} u={l.uom} /> },
    ...(showFinance
      ? [
          { key: "est", header: "BOQ rate", align: "right" as const, cell: (l: ProgressLine) => fmtRate(l.estimated_rate) },
          { key: "act", header: "Actual avg", align: "right" as const, cell: (l: ProgressLine) => fmtRate(l.actual_avg_rate) },
          {
            key: "var",
            header: "Variance",
            align: "right" as const,
            cell: (l: ProgressLine) => (l.rate_variance_pct == null ? "—" : <span className={l.rate_variance_pct > 0 ? "text-destructive" : "text-success"}>{l.rate_variance_pct > 0 ? "+" : ""}{fmtPct(l.rate_variance_pct)}</span>),
          },
        ]
      : []),
  ];
  return (
    <Section
      title="Planned vs inward vs issued"
      actions={showFinance && totals.estimated_value != null && <span className="text-xs text-muted-foreground">Procured {fmtMoney(totals.procured_value)} of {fmtMoney(totals.estimated_value)}</span>}
    >
      <DataList rows={lines} columns={cols} rowKey={(l) => l.material_id} dense />
    </Section>
  );
}

const Q = ({ v, u }: { v: number; u: string }) => (
  <span className="tabular-nums">
    {fmtQty(v)} <span className="text-xs text-muted-foreground">{u}</span>
  </span>
);

function PlannedTable({ boq }: { boq: Boq }) {
  const cols: Column<Boq["items"][number]>[] = [
    { key: "mat", header: "Material", cell: (i) => <div><p className="font-medium">{i.material.name}</p><p className="text-xs text-muted-foreground">{i.material.code}</p></div> },
    { key: "qty", header: "Planned", align: "right", cell: (i) => <Q v={i.planned_qty} u={i.material.uom} /> },
    { key: "rate", header: "Est. rate", align: "right", cell: (i) => fmtRate(i.estimated_rate) },
    { key: "bench", header: "Market benchmark", align: "right", cell: (i) => fmtRate(i.benchmark_rate) },
    { key: "amt", header: "Amount", align: "right", cell: (i) => fmtMoney(i.estimated_amount) },
  ];
  return (
    <Section title="Planned materials">
      <DataList rows={boq.items} columns={cols} rowKey={(i) => i.id} dense />
    </Section>
  );
}

function AllocationTable({ boq }: { boq: Boq }) {
  const { data } = useQuery({
    queryKey: ["boq-alloc", boq.id],
    queryFn: async () => (await get<Record<string, string | number | null>[]>(`/boqs/${boq.id}/allocation-progress`)).data,
    enabled: boq.status !== "DRAFT",
  });
  const rows = data ?? [];
  if (!rows.length) return null;
  const cols: Column<Record<string, string | number | null>>[] = [
    { key: "p", header: "Project", cell: (r) => <div><p className="font-medium">{r.project_name}</p><p className="text-xs text-muted-foreground">{r.material_name}</p></div> },
    { key: "a", header: "Allocated", align: "right", cell: (r) => <Q v={Number(r.allocated_qty)} u={String(r.uom)} /> },
    { key: "ap", header: "Approved in requests", align: "right", cell: (r) => <Q v={Number(r.approved_qty)} u={String(r.uom)} /> },
    { key: "i", header: "Issued", align: "right", cell: (r) => <Q v={Number(r.issued_qty)} u={String(r.uom)} /> },
    { key: "rem", header: "Remaining", align: "right", cell: (r) => <span className={Number(r.remaining_allocation) < 0 ? "text-destructive" : ""}><Q v={Number(r.remaining_allocation)} u={String(r.uom)} /></span> },
  ];
  return (
    <Section title="Project allocations">
      <DataList rows={rows} columns={cols} rowKey={(r) => `${r.project_id}-${r.material_id}`} dense />
    </Section>
  );
}

// ------------------------------------------------------------------ form

interface AllocRow {
  project_id: string;
  site_id: string;
  allocated_qty: string;
}
interface LineRow {
  key: string;
  material: PickedMaterial | null;
  planned_qty: string;
  estimated_rate: string;
  remarks: string;
  allocations: AllocRow[];
  open: boolean;
  uom_id: string;
}

const blankLine = (): LineRow => ({ key: crypto.randomUUID(), material: null, planned_qty: "", estimated_rate: "", remarks: "", allocations: [], open: false, uom_id: "" });

export function BoqFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { me, can } = useAuth();
  const editing = !!id;
  const { data: existing } = useOne<Boq>("boq", id ? `/boqs/${id}` : null);
  const [title, setTitle] = useState("");
  const [fy, setFy] = useState(me?.financial_year ?? "");
  const [remarks, setRemarks] = useState("");
  const [lines, setLines] = useState<LineRow[]>([blankLine()]);
  const [idem] = useState(newIdempotencyKey);

  useEffect(() => {
    if (!existing) return;
    setTitle(existing.title);
    setFy(existing.financial_year);
    setRemarks(existing.remarks ?? "");
    setLines(
      existing.items.map((i) => ({
        key: i.id,
        material: { id: i.material.id, code: i.material.code, name: i.material.name, uom: i.material.uom },
        planned_qty: String(i.planned_qty),
        estimated_rate: String(i.estimated_rate ?? ""),
        remarks: i.remarks ?? "",
        allocations: i.allocations.map((a) => ({ project_id: a.project_id, site_id: a.site_id ?? "", allocated_qty: String(a.allocated_qty) })),
        open: false,
        uom_id: "",
      })),
    );
  }, [existing]);

  const { data: sitesData } = useList<Site>("master-select", "/masters/sites", { limit: 500 });
  const sites = sitesData?.data ?? [];
  const total = useMemo(() => lines.reduce((s, l) => s + (Number(l.planned_qty) || 0) * (Number(l.estimated_rate) || 0), 0), [lines]);
  const update = (key: string, patch: Partial<LineRow>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const save = useAction(
    async (publish: boolean) => {
      const body = {
        title,
        financial_year: fy || null,
        remarks: remarks || null,
        items: lines
          .filter((l) => l.material)
          .map((l) => ({
            material_id: l.material!.id,
            planned_qty: Number(l.planned_qty),
            uom_id: l.uom_id || null,
            estimated_rate: Number(l.estimated_rate || 0),
            remarks: l.remarks || null,
            allocations: l.allocations.filter((a) => a.project_id && Number(a.allocated_qty) > 0).map((a) => ({ project_id: a.project_id, site_id: a.site_id || null, allocated_qty: Number(a.allocated_qty) })),
          })),
      };
      let b = editing ? await put<Boq>(`/boqs/${id}`, { ...body, version: existing!.version }) : await post<Boq>("/boqs", body, { "Idempotency-Key": idem });
      if (publish) b = await post<Boq>(`/boqs/${b.id}/submit`, { version: b.version });
      return b;
    },
    { success: (b) => (b.status === "APPROVED" ? "BOQ approved and live" : b.status === "SUBMITTED" ? "Submitted for approval" : "BOQ saved"), invalidate: ["boqs", "boq"], onSuccess: (b) => nav(`/boqs/${b.id}`) },
  );

  const autoApprove = can("boq.approve");
  const valid = title.trim().length >= 3 && lines.some((l) => l.material && Number(l.planned_qty) > 0);

  return (
    <>
      <PageHeader back title={editing ? `Edit ${existing?.boq_no ?? "BOQ"}` : "New BOQ"} subtitle="Planned quantity and estimated rate per material. Optionally allocate to projects / sites." />
      <div className="grid gap-4">
        <Card className="grid gap-4 p-4 md:grid-cols-3 md:p-5">
          <Field label="Title *" className="md:col-span-2">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Central material plan FY 2026-27" />
          </Field>
          <Field label="Financial year">
            <Input value={fy} onChange={(e) => setFy(e.target.value)} placeholder="2026-27" disabled={!!existing?.revision} />
          </Field>
          <Field label="Remarks" className="md:col-span-3">
            <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </Field>
        </Card>

        <Section title={`Materials (${lines.filter((l) => l.material).length})`} actions={me?.show_finance && <span className="text-sm font-medium tabular-nums">{fmtMoney(total)}</span>}>
          <ul className="divide-y">
            {lines.map((l, idx) => (
              <li key={l.key} className="grid gap-3 p-4 md:grid-cols-[2fr_1fr_1fr_auto] md:items-end md:px-5">
                <Field label={`${idx + 1}. Material`}>
                  <MaterialPicker value={l.material} onChange={(m) => update(l.key, { material: m, uom_id: "" })} />
                </Field>
                <Field label="Planned qty" hint={l.uom_id ? "Allocations below use the same unit" : undefined}>
                  <div className="flex gap-2">
                    <QtyInput value={l.planned_qty} onChange={(e) => update(l.key, { planned_qty: e.target.value })} />
                    <UnitSelect material={l.material} value={l.uom_id} className="w-24 shrink-0" onChange={(uom_id) => update(l.key, { uom_id })} />
                  </div>
                </Field>
                <Field label={`Estimated rate (₹${l.material ? ` per ${l.uom_id ? "selected unit" : l.material.uom}` : ""})`}>
                  <QtyInput value={l.estimated_rate} onChange={(e) => update(l.key, { estimated_rate: e.target.value })} />
                </Field>
                <div className="flex gap-1">
                  <Button type="button" variant="outline" size="sm" onClick={() => update(l.key, { open: !l.open })}>
                    Allocate{l.allocations.length ? ` (${l.allocations.length})` : ""}
                  </Button>
                  <Button type="button" variant="ghost" size="icon" aria-label="Remove line" onClick={() => setLines((ls) => (ls.length > 1 ? ls.filter((x) => x.key !== l.key) : [blankLine()]))}>
                    <Trash2 />
                  </Button>
                </div>
                {l.open && (
                  <div className="grid gap-2 rounded-lg bg-muted/50 p-3 md:col-span-4">
                    <p className="text-xs text-muted-foreground">Optional: earmark part of the planned quantity for projects (and sites). Requests beyond the allocation need a store override.</p>
                    {l.allocations.map((a, ai) => (
                      <div key={ai} className="grid grid-cols-[1fr_1fr_7rem_auto] gap-2">
                        <MasterSelect path="projects" value={a.project_id} onChange={(v) => update(l.key, { allocations: l.allocations.map((x, xi) => (xi === ai ? { ...x, project_id: v, site_id: "" } : x)) })} placeholder="Project" />
                        <Select value={a.site_id} onChange={(e) => update(l.key, { allocations: l.allocations.map((x, xi) => (xi === ai ? { ...x, site_id: e.target.value } : x)) })}>
                          <option value="">Whole project</option>
                          {sites.filter((s) => s.project_id === a.project_id).map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name}
                            </option>
                          ))}
                        </Select>
                        <QtyInput value={a.allocated_qty} placeholder="Qty" onChange={(e) => update(l.key, { allocations: l.allocations.map((x, xi) => (xi === ai ? { ...x, allocated_qty: e.target.value } : x)) })} />
                        <Button type="button" variant="ghost" size="icon" aria-label="Remove allocation" onClick={() => update(l.key, { allocations: l.allocations.filter((_, xi) => xi !== ai) })}>
                          <X />
                        </Button>
                      </div>
                    ))}
                    <Button type="button" variant="outline" size="sm" className="justify-self-start" onClick={() => update(l.key, { allocations: [...l.allocations, { project_id: "", site_id: "", allocated_qty: "" }] })}>
                      <Plus /> Add allocation
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <div className="border-t p-3 md:px-5">
            <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, blankLine()])}>
              <Plus /> Add material
            </Button>
          </div>
        </Section>
        <ActionBar>
          <Button variant="outline" onClick={() => nav(-1)}>
            Cancel
          </Button>
          <Button variant={autoApprove ? "outline" : "default"} loading={save.isPending && !save.variables} disabled={!valid} onClick={() => save.mutate(false)}>
            Save draft
          </Button>
          {autoApprove && (
            <Button variant="success" loading={save.isPending && save.variables === true} disabled={!valid} onClick={() => save.mutate(true)}>
              <Send /> Save &amp; approve
            </Button>
          )}
        </ActionBar>
      </div>
    </>
  );
}
