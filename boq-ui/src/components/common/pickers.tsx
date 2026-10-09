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
                    onChange({ id: m.id, code: m.code, name: m.name, uom: m.base_uom.code ?? "" });
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
