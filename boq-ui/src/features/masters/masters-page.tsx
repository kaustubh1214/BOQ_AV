import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Search, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { NavLink, useParams } from "react-router";
import { toast } from "sonner";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { PageHeader, StatusBadge } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial } from "@/components/common/pickers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { errorDetails, errorMessage, get, http } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtQty } from "@/lib/format";
import { del, patch, post, useAction, useDebounced, useList } from "@/lib/hooks";
import { cn } from "@/lib/utils";

type Row = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

interface FieldDef {
  key: string;
  label: string;
  type?: "text" | "number" | "date" | "textarea" | "select" | "status" | "material";
  path?: string; // for select
  createOnly?: boolean;
  required?: boolean;
  hint?: string;
  full?: boolean;
}

interface MasterDef {
  key: string;
  label: string;
  singular: string;
  manage: string;
  columns: Column<Row>[];
  fields: FieldDef[];
  importable?: boolean;
  importHint?: string;
}

const code = (r: Row) => <span className="font-mono text-xs">{r.code}</span>;

const MASTERS: MasterDef[] = [
  {
    key: "materials",
    label: "Materials",
    singular: "material",
    manage: "masters.manage",
    importable: true,
    importHint: "code,name,category_code,uom_code,hsn_code,gst_rate,reorder_level,specification",
    columns: [
      { key: "name", header: "Material", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="font-mono text-xs text-muted-foreground">{r.code}</p></div> },
      { key: "cat", header: "Category", cell: (r) => r.category?.name },
      { key: "uom", header: "UOM", cell: (r) => r.base_uom?.code },
      { key: "hsn", header: "HSN", cell: (r) => r.hsn_code ?? "—" },
      { key: "gst", header: "GST %", cell: (r) => `${r.gst_rate}%`, align: "right" },
      { key: "reorder", header: "Reorder level", cell: (r) => fmtQty(r.reorder_level), align: "right" },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
      { key: "category_id", label: "Category", type: "select", path: "categories", required: true },
      { key: "base_uom_id", label: "Base UOM", type: "select", path: "uoms", createOnly: true, required: true, hint: "Stock is kept in this unit" },
      { key: "hsn_code", label: "HSN code" },
      { key: "gst_rate", label: "GST %", type: "number" },
      { key: "reorder_level", label: "Reorder level (central store)", type: "number" },
      { key: "specification", label: "Specification", type: "textarea", full: true },
    ],
  },
  {
    key: "categories",
    label: "Categories",
    singular: "category",
    manage: "masters.manage",
    columns: [
      { key: "name", header: "Category", cell: (r) => r.name },
      { key: "code", header: "Code", cell: code },
      { key: "hsn", header: "Default HSN", cell: (r) => r.default_hsn_code ?? "—" },
      { key: "gst", header: "Default GST", cell: (r) => (r.default_gst_rate != null ? `${r.default_gst_rate}%` : "—"), align: "right" },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
      { key: "default_uom_id", label: "Default unit", type: "select", path: "uoms", hint: "Pre-fills the base unit when a material of this category is added" },
      { key: "default_hsn_code", label: "Default HSN code" },
      { key: "default_gst_rate", label: "Default GST %", type: "number" },
    ],
  },
  {
    key: "vendors",
    label: "Vendors",
    singular: "vendor",
    manage: "vendors.manage",
    importable: true,
    importHint: "code,name,gstin,pan,contact_person,phone,email,address",
    columns: [
      { key: "name", header: "Vendor", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="font-mono text-xs text-muted-foreground">{r.code}</p></div> },
      { key: "gstin", header: "GSTIN", cell: (r) => r.gstin ?? "—" },
      { key: "contact", header: "Contact", cell: (r) => r.contact_person ?? "—" },
      { key: "phone", header: "Phone", cell: (r) => r.phone ?? "—" },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
      { key: "gstin", label: "GSTIN", hint: "15 characters, e.g. 27ABCDE1234F1Z5" },
      { key: "pan", label: "PAN" },
      { key: "contact_person", label: "Contact person" },
      { key: "phone", label: "Phone" },
      { key: "email", label: "Email" },
      { key: "address", label: "Address", type: "textarea", full: true },
    ],
  },
  {
    key: "projects",
    label: "Projects",
    singular: "project",
    manage: "masters.manage",
    columns: [
      { key: "name", header: "Project", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="font-mono text-xs text-muted-foreground">{r.code}</p></div> },
      { key: "city", header: "City", cell: (r) => r.city ?? "—" },
      { key: "start", header: "Start", cell: (r) => fmtDate(r.start_date) },
      { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
      { key: "city", label: "City" },
      { key: "start_date", label: "Start date", type: "date" },
      { key: "end_date", label: "End date", type: "date" },
      { key: "status", label: "Status", type: "status" },
      { key: "address", label: "Address", type: "textarea", full: true },
    ],
  },
  {
    key: "sites",
    label: "Sites",
    singular: "site",
    manage: "masters.manage",
    columns: [
      { key: "name", header: "Site", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="font-mono text-xs text-muted-foreground">{r.code}</p></div> },
      { key: "project", header: "Project", cell: (r) => r.project?.name },
      { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
    ],
    fields: [
      { key: "project_id", label: "Project", type: "select", path: "projects", createOnly: true, required: true },
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
      { key: "status", label: "Status", type: "status" },
      { key: "address", label: "Address", type: "textarea", full: true },
    ],
  },
  {
    key: "stores",
    label: "Stores",
    singular: "central store",
    manage: "masters.manage",
    columns: [
      { key: "name", header: "Store", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="font-mono text-xs text-muted-foreground">{r.code}</p></div> },
      { key: "type", header: "Type", cell: (r) => <StatusBadge status={r.type} /> },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
    ],
  },
  {
    key: "uoms",
    label: "Units",
    singular: "unit",
    manage: "masters.manage",
    columns: [
      { key: "code", header: "Code", cell: (r) => <span className="font-mono">{r.code}</span> },
      { key: "name", header: "Name", cell: (r) => r.name },
    ],
    fields: [
      { key: "code", label: "Code", createOnly: true, required: true },
      { key: "name", label: "Name", required: true },
    ],
  },
  {
    key: "uom-conversions",
    label: "Conversions",
    singular: "conversion",
    manage: "masters.manage",
    columns: [
      { key: "from", header: "Conversion", cell: (r) => <ConversionLabel r={r} /> },
      { key: "scope", header: "Applies to", cell: (r) => (r.material_id ? "One material" : "All materials") },
    ],
    fields: [
      { key: "material_id", label: "Material (blank = all)", type: "material", createOnly: true },
      { key: "from_uom_id", label: "From UOM", type: "select", path: "uoms", createOnly: true, required: true },
      { key: "to_uom_id", label: "To UOM", type: "select", path: "uoms", createOnly: true, required: true },
      { key: "factor", label: "1 From = ? To", type: "number", required: true },
    ],
  },
];

function ConversionLabel({ r }: { r: Row }) {
  const { data } = useList<Row>("master-select", "/masters/uoms", { limit: 500 });
  const uoms = Object.fromEntries((data?.data ?? []).map((u) => [u.id, u.code]));
  return (
    <span className="tabular-nums">
      1 {uoms[r.from_uom_id] ?? "?"} = {r.factor} {uoms[r.to_uom_id] ?? "?"}
    </span>
  );
}

export function MastersPage() {
  const { tab = "materials" } = useParams();
  const def = MASTERS.find((m) => m.key === tab) ?? MASTERS[0];
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [showInactive, setShowInactive] = useState(false);
  const dq = useDebounced(q);
  const { data, isLoading } = useList<Row>(`masters-${def.key}`, `/masters/${def.key}`, { q: dq, page, limit: 25, active: showInactive ? undefined : true });
  const [editing, setEditing] = useState<Row | null | "new">(null);
  const manage = can(def.manage);

  const columns: Column<Row>[] = [
    ...def.columns,
    ...(showInactive ? [{ key: "active", header: "Active", cell: (r: Row) => (r.is_active ? "Yes" : <span className="text-destructive">No</span>) }] : []),
    ...(manage
      ? [
          {
            key: "edit",
            header: "",
            align: "right" as const,
            mobile: false,
            cell: (r: Row) => (
              <Button variant="ghost" size="icon" onClick={(e) => (e.stopPropagation(), setEditing(r))} aria-label="Edit">
                <Pencil />
              </Button>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title="Masters"
        subtitle="Materials, vendors, projects, sites and units used across every document"
        actions={
          manage && (
            <>
              {def.importable && <ImportButton path={def.key} hint={def.importHint!} />}
              <Button onClick={() => setEditing("new")}>
                <Plus /> New {def.singular}
              </Button>
            </>
          )
        }
      />
      <div className="-mx-4 mb-4 overflow-x-auto px-4 md:mx-0 md:px-0">
        <div className="flex w-max gap-1 rounded-lg bg-muted p-1">
          {MASTERS.map((m) => (
            <NavLink
              key={m.key}
              to={`/masters/${m.key}`}
              onClick={() => (setPage(1), setQ(""))}
              className={({ isActive }) => cn("rounded-md px-3 py-1.5 text-sm whitespace-nowrap", isActive || (m.key === "materials" && !tab) ? "bg-card font-medium shadow-sm" : "text-muted-foreground")}
            >
              {m.label}
            </NavLink>
          ))}
        </div>
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b p-3">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder={`Search ${def.label.toLowerCase()}`} value={q} onChange={(e) => (setQ(e.target.value), setPage(1))} className="pl-9" />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} className="size-4" /> Include inactive
          </label>
        </div>
        <DataList rows={data?.data} columns={columns} rowKey={(r) => r.id} loading={isLoading} onRowClick={manage ? (r) => setEditing(r) : undefined} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={setPage} />
      </Card>
      {editing && <MasterForm def={def} row={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function MasterForm({ def, row, onClose }: { def: MasterDef; row: Row | null; onClose: () => void }) {
  const isNew = !row;
  const [values, setValues] = useState<Row>(() => {
    const v: Row = {};
    for (const f of def.fields) {
      if (f.key === "category_id") v[f.key] = row?.category?.id ?? "";
      else if (f.key === "base_uom_id") v[f.key] = row?.base_uom?.id ?? "";
      else v[f.key] = row?.[f.key] ?? (f.type === "status" ? "ACTIVE" : "");
    }
    return v;
  });
  const [material, setMaterial] = useState<PickedMaterial | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = (k: string, v: unknown) => setValues((s) => ({ ...s, [k]: v }));

  // New material: picking a category / unit pre-fills the related fields (anything the user typed is kept).
  const smart = isNew && def.key === "materials";
  const touched = useRef(new Set<string>());
  const [altUom, setAltUom] = useState("");
  const [altFactor, setAltFactor] = useState("");
  const { data: cats } = useQuery({
    queryKey: ["master-select", "categories", undefined],
    queryFn: () => get<Row[]>("/masters/categories", { limit: 500 }),
    staleTime: 60_000,
    enabled: smart,
  });
  const { data: convs } = useQuery({
    queryKey: ["master-select", "uom-conversions", "global"],
    queryFn: () => get<Row[]>("/masters/uom-conversions", { limit: 500 }),
    staleTime: 60_000,
    enabled: smart,
  });
  const globalFactor = (alt: string, base: string): number | null => {
    for (const c of convs?.data ?? []) {
      if (c.material_id) continue;
      if (c.from_uom_id === alt && c.to_uom_id === base) return Number(c.factor);
      if (c.from_uom_id === base && c.to_uom_id === alt) return 1 / Number(c.factor);
    }
    return null;
  };
  const suggestAlt = (alt: string, base: string) => {
    const f = alt && base ? globalFactor(alt, base) : null;
    setAltFactor(f != null ? String(Number(f.toFixed(6))) : "");
  };
  const change = (key: string, v: string) => {
    set(key, v);
    if (!smart) return;
    if (key === "category_id") {
      const c = cats?.data.find((x) => x.id === v);
      if (!c) return;
      const fill = (k: string, val: unknown) => !touched.current.has(k) && val != null && set(k, String(val));
      fill("base_uom_id", c.default_uom_id);
      fill("hsn_code", c.default_hsn_code);
      fill("gst_rate", c.default_gst_rate);
      if (c.default_uom_id && !touched.current.has("base_uom_id")) {
        if (altUom === c.default_uom_id) setAltUom("");
        suggestAlt(altUom === c.default_uom_id ? "" : altUom, c.default_uom_id);
      }
    } else {
      touched.current.add(key);
      if (key === "base_uom_id") suggestAlt(altUom, v);
    }
  };
  const fields = def.fields.filter((f) => isNew || !f.createOnly);

  const save = useAction(
    async () => {
      const body: Row = {};
      for (const f of fields) {
        let v = f.type === "material" ? material?.id ?? null : values[f.key];
        if (v === "") v = null;
        if (f.type === "number" && v != null) v = Number(v);
        if (v != null || (!isNew && f.type !== "number" && !f.required)) body[f.key] = v;
      }
      if (isNew) {
        const created = await post<Row>(`/masters/${def.key}`, body);
        if (smart && altUom && Number(altFactor) > 0 && globalFactor(altUom, values.base_uom_id) !== Number(altFactor)) {
          await post("/masters/uom-conversions", { material_id: created.id, from_uom_id: altUom, to_uom_id: values.base_uom_id, factor: Number(altFactor) });
        }
        return created;
      }
      return patch(`/masters/${def.key}/${row!.id}`, { ...body, version: row!.version });
    },
    { success: `${def.singular[0].toUpperCase()}${def.singular.slice(1)} saved`, invalidate: [`masters-${def.key}`, "master-select", "materials-pick", "masters-uom-conversions"], onSuccess: onClose },
  );
  const deactivate = useAction(() => del(`/masters/${def.key}/${row!.id}`), { success: "Deactivated", invalidate: [`masters-${def.key}`, "master-select"], onSuccess: onClose });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors({});
    try {
      await save.mutateAsync(undefined);
    } catch (err) {
      const d = errorDetails(err);
      if (Array.isArray(d)) setErrors(Object.fromEntries(d.map((x: { field: string; message: string }) => [x.field, x.message])));
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={isNew ? `New ${def.singular}` : `Edit ${def.singular}`} description={row?.code ? `Code ${row.code}` : undefined} className="sm:max-w-2xl">
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          {fields.map((f) => (
            <Field key={f.key} label={f.label + (f.required ? " *" : "")} hint={f.hint} error={errors[f.key]} className={f.full ? "sm:col-span-2" : undefined}>
              {f.type === "select" ? (
                <MasterSelect path={f.path!} value={values[f.key]} onChange={(v) => change(f.key, v)} label={(x) => (x.code ? `${x.name} (${x.code})` : x.name)} />
              ) : f.type === "status" ? (
                <Select value={values[f.key]} onChange={(e) => set(f.key, e.target.value)}>
                  <option value="ACTIVE">Active</option>
                  <option value="ON_HOLD">On hold</option>
                  <option value="COMPLETED">Completed</option>
                </Select>
              ) : f.type === "textarea" ? (
                <Textarea value={values[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)} rows={2} />
              ) : f.type === "material" ? (
                <MaterialPicker value={material} onChange={setMaterial} placeholder="All materials" />
              ) : (
                <Input
                  type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
                  inputMode={f.type === "number" ? "decimal" : undefined}
                  step="any"
                  required={f.required}
                  value={values[f.key] ?? ""}
                  onChange={(e) => change(f.key, e.target.value)}
                  aria-invalid={!!errors[f.key]}
                />
              )}
            </Field>
          ))}
          {smart && (
            <div className="grid gap-3 rounded-lg border border-dashed p-3 sm:col-span-2 sm:grid-cols-2">
              <p className="text-xs text-muted-foreground sm:col-span-2">
                Optional — also receive this material in another unit (e.g. tonnes or bags). Stock stays in the base unit.
              </p>
              <Field label="Other unit">
                <MasterSelect
                  path="uoms"
                  value={altUom}
                  placeholder="None"
                  onChange={(v) => (setAltUom(v), suggestAlt(v, values.base_uom_id))}
                  label={(x) => (x.code ? `${x.name} (${x.code})` : x.name)}
                />
              </Field>
              <Field label={altUom && values.base_uom_id ? "1 other unit = ? base units" : "Conversion"} hint="Filled from the standard conversions when one exists">
                <Input type="number" inputMode="decimal" step="any" disabled={!altUom} value={altFactor} onChange={(e) => setAltFactor(e.target.value)} />
              </Field>
            </div>
          )}
          <div className="flex flex-col-reverse gap-2 sm:col-span-2 sm:flex-row sm:justify-between">
            {!isNew && row?.is_active ? (
              <Button type="button" variant="ghost" className="text-destructive" loading={deactivate.isPending} onClick={() => deactivate.mutate(undefined)}>
                Deactivate
              </Button>
            ) : (
              <span />
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" loading={save.isPending}>
                Save
              </Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ImportButton({ path, hint }: { path: string; hint: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    setBusy(true);
    try {
      const r = await http.post(`/masters/${path}/import`, fd);
      toast.success(`Imported: ${r.data.data.created} created, ${r.data.data.updated} updated`);
      qc.invalidateQueries({ queryKey: [`masters-${path}`] });
    } catch (e) {
      const d = errorDetails(e);
      toast.error(errorMessage(e), { description: Array.isArray(d) ? d.slice(0, 5).map((x: { row: number; message: string }) => `Row ${x.row}: ${x.message}`).join("\n") : undefined });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <input ref={ref} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => (e.target.files?.[0] && upload(e.target.files[0]), (e.target.value = ""))} />
      <Button variant="outline" loading={busy} onClick={() => ref.current?.click()} title={`CSV columns: ${hint}`}>
        <Upload /> Import CSV
      </Button>
    </>
  );
}
