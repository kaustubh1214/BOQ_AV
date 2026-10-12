import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { useState } from "react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Select, inputClass } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced } from "@/lib/hooks";
import type { Material, Site } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PickedMaterial {
  id: string;
  code: string;
  name: string;
  uom: string;
  uom_id?: string;
}

/** Searchable material picker — a bottom sheet on phones, a dialog on desktop. */
export function MaterialPicker({
  value,
  onChange,
  restrictTo,
  placeholder = "Select material",
  invalid,
}: {
  value: PickedMaterial | null;
  onChange: (m: PickedMaterial) => void;
  /** Only offer these material ids (e.g. materials in the BOQ). */
  restrictTo?: Set<string>;
  placeholder?: string;
  invalid?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const { data, isFetching } = useQuery({
    queryKey: ["materials-pick", dq],
    queryFn: () => get<Material[]>("/masters/materials", { q: dq, limit: 50 }),
    enabled: open,
  });
  const items = (data?.data ?? []).filter((m) => !restrictTo || restrictTo.has(m.id));

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-invalid={invalid}
        className={cn(inputClass, "items-center justify-between gap-2 text-left", !value && "text-muted-foreground")}
      >
        <span className="truncate">{value ? `${value.name}` : placeholder}</span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          {value?.uom}
          <ChevronsUpDown className="size-4" />
        </span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="Choose material" className="sm:max-w-xl">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input autoFocus placeholder="Search by name, code or HSN" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" />
          </div>
          <ul className="-mx-2 max-h-[55dvh] overflow-y-auto">
            {items.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange({ id: m.id, code: m.code, name: m.name, uom: m.base_uom.code ?? "", uom_id: m.base_uom.id });
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left hover:bg-accent"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{m.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {m.code} · {m.category.name} · {m.base_uom.code}
                    </p>
                  </div>
                  {value?.id === m.id && <Check className="size-4 text-primary" />}
                </button>
              </li>
            ))}
            {!items.length && <li className="px-3 py-6 text-center text-sm text-muted-foreground">{isFetching ? "Searching…" : "No materials found"}</li>}
          </ul>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Generic master select (vendors, projects, stores, categories, uoms). */
export function MasterSelect({
  path,
  value,
  onChange,
  placeholder = "Select…",
  params,
  allowEmpty = true,
  label = (x: { code?: string; name: string }) => x.name,
  className,
}: {
  path: string;
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
  params?: Record<string, unknown>;
  allowEmpty?: boolean;
  label?: (x: { id: string; code?: string; name: string }) => string;
  className?: string;
}) {
  const { data } = useQuery({
    queryKey: ["master-select", path, params],
    queryFn: () => get<{ id: string; code?: string; name: string }[]>(`/masters/${path}`, { limit: 500, ...params }),
    staleTime: 60_000,
  });
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className={className}>
      {allowEmpty && <option value="">{placeholder}</option>}
      {(data?.data ?? []).map((x) => (
        <option key={x.id} value={x.id}>
          {label(x)}
        </option>
      ))}
    </Select>
  );
}

export interface UnitOption {
  id: string; // "" = the material's base unit
  code: string;
  factor: number; // 1 of this unit = factor base units
}

/** Base unit first (the default), then every unit that has a conversion for this material. */
export function useUnitOptions(material: PickedMaterial | null): UnitOption[] {
  const { data: uoms } = useQuery({
    queryKey: ["master-select", "uoms", undefined],
    queryFn: () => get<{ id: string; code?: string; name: string }[]>("/masters/uoms", { limit: 500 }),
    staleTime: 60_000,
    enabled: !!material,
  });
  const { data: convs } = useQuery({
    queryKey: ["uom-conversions-all"],
    queryFn: () => get<{ material_id: string | null; from_uom_id: string; to_uom_id: string; factor: number }[]>("/masters/uom-conversions", { limit: 500 }),
    staleTime: 60_000,
    enabled: !!material,
  });
  if (!material) return [];
  const list = uoms?.data ?? [];
  const baseId = material.uom_id ?? list.find((u) => u.code === material.uom)?.id;
  const out: UnitOption[] = [{ id: "", code: material.uom, factor: 1 }];
  if (!baseId) return out;
  const code = (id: string) => list.find((u) => u.id === id)?.code ?? "?";
  const seen = new Map<string, { opt: UnitOption; own: boolean }>();
  for (const c of convs?.data ?? []) {
    if (c.material_id && c.material_id !== material.id) continue;
    const own = !!c.material_id;
    let alt: string | null = null;
    let factor = 0;
    if (c.to_uom_id === baseId) {
      alt = c.from_uom_id;
      factor = Number(c.factor);
    } else if (c.from_uom_id === baseId) {
      alt = c.to_uom_id;
      factor = 1 / Number(c.factor);
    }
    if (!alt || alt === baseId) continue;
    const prev = seen.get(alt);
    if (!prev || (own && !prev.own)) seen.set(alt, { opt: { id: alt, code: code(alt), factor: Number(factor.toFixed(6)) }, own });
  }
  return [...out, ...[...seen.values()].map((s) => s.opt)];
}

/** Unit dropdown for a document line: defaults to the material's base unit, switchable when conversions exist. */
export function UnitSelect({
  material,
  value,
  onChange,
  className,
}: {
  material: PickedMaterial | null;
  value: string;
  onChange: (uomId: string, factor: number) => void;
  className?: string;
}) {
  const options = useUnitOptions(material);
  if (!material) return null;
  return (
    <Select
      value={value}
      disabled={options.length < 2}
      onChange={(e) => onChange(e.target.value, options.find((o) => o.id === e.target.value)?.factor ?? 1)}
      className={className}
      aria-label="Unit"
    >
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.code}
        </option>
      ))}
    </Select>
  );
}

/** Sites the current user may act on (all sites for global users). */
export function useMySites() {
  const { me } = useAuth();
  const all = !!me?.scopes.all;
  const { data } = useQuery({
    queryKey: ["my-sites", all],
    queryFn: () => get<Site[]>("/masters/sites", { limit: 500 }),
    enabled: all,
    staleTime: 60_000,
  });
  if (!me) return [];
  if (all) return (data?.data ?? []).map((s) => ({ id: s.id, code: s.code, name: s.name, project_id: s.project_id, project_name: s.project.name }));
  const projects = Object.fromEntries(me.scopes.projects.map((p) => [p.id, p.name]));
  return me.scopes.sites.map((s) => ({ ...s, project_name: projects[s.project_id] }));
}

export function SiteSelect({ value, onChange, allowEmpty = false, className }: { value: string; onChange: (id: string) => void; allowEmpty?: boolean; className?: string }) {
  const sites = useMySites();
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className={className}>
      {(allowEmpty || !value) && <option value="">{allowEmpty ? "All sites" : "Select site"}</option>}
      {sites.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
          {s.project_name ? ` — ${s.project_name}` : ""}
        </option>
      ))}
    </Select>
  );
}
