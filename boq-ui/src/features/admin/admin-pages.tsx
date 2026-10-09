import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { KeyRound, Plus, Shield, Trash2, UserPlus } from "lucide-react";
import { Fragment, useState } from "react";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { EmptyState, PageHeader, StatusBadge } from "@/components/common/page";
import { MasterSelect } from "@/components/common/pickers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, humanize } from "@/lib/format";
import { patch, post, put, useAction, useDebounced, useList } from "@/lib/hooks";
import { cn } from "@/lib/utils";

interface Assignment {
  id?: string;
  role_id: string;
  role_code?: string;
  role_name?: string;
  scope_type: string;
  scope_id: string | null;
  scope_name?: string | null;
}
interface User {
  id: string;
  name: string;
  email: string;
  phone?: string | null;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at?: string | null;
  locked_until?: string | null;
  assignments: Assignment[];
}
interface Role {
  id: string;
  code: string;
  name: string;
  description?: string | null;
  is_system: boolean;
  default_scope: string;
  permissions: string[];
  user_count: number;
}
interface Perm {
  code: string;
  module: string;
  description: string;
}

export function UsersPage() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const { data, isLoading } = useList<User>("users", "/admin/users", { q: dq, page, limit: 25 });
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const cols: Column<User>[] = [
    { key: "n", header: "User", cell: (u) => <div><p className="font-medium">{u.name}</p><p className="text-xs text-muted-foreground">{u.email}</p></div> },
    {
      key: "r",
      header: "Roles & scope",
      cell: (u) => (
        <div className="flex flex-wrap gap-1">
          {u.assignments.map((a) => (
            <Badge key={a.id} tone="blue">{a.role_name}{a.scope_type !== "GLOBAL" ? ` · ${a.scope_name}` : ""}</Badge>
          ))}
          {!u.assignments.length && <span className="text-xs text-muted-foreground">No access</span>}
        </div>
      ),
    },
    { key: "l", header: "Last login", cell: (u) => fmtDateTime(u.last_login_at) },
    { key: "s", header: "Status", cell: (u) => (u.is_active ? (u.locked_until && new Date(u.locked_until) > new Date() ? <Badge tone="amber">Locked</Badge> : <Badge tone="green">Active</Badge>) : <Badge tone="red">Disabled</Badge>) },
  ];
  return (
    <>
      <PageHeader title="Users" subtitle="Each user gets roles scoped to the whole company, a project, a site or a store" actions={<Button onClick={() => setEditing("new")}><UserPlus /> New user</Button>} />
      <Input placeholder="Search name or email" value={q} onChange={(e) => (setQ(e.target.value), setPage(1))} className="mb-3 sm:max-w-sm" />
      <Card className="overflow-hidden">
        <DataList rows={data?.data} columns={cols} rowKey={(u) => u.id} loading={isLoading} onRowClick={(u) => setEditing(u)} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={setPage} />
      </Card>
      {editing && <UserDialog user={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function ScopePicker({ type, value, onChange }: { type: string; value: string; onChange: (v: string) => void }) {
  if (type === "GLOBAL") return <Input value="Whole company" disabled />;
  const path = { PROJECT: "projects", SITE: "sites", STORE: "stores" }[type]!;
  return <MasterSelect path={path} value={value} onChange={onChange} placeholder={`Select ${type.toLowerCase()}`} />;
}

function UserDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { me } = useAuth();
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: async () => (await get<Role[]>("/admin/roles")).data });
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [password, setPassword] = useState("");
  const [assignments, setAssignments] = useState<Assignment[]>(user?.assignments ?? []);
  const inv = ["users"];
  const create = useAction(
    () => post("/admin/users", { name, email, phone: phone || null, password, assignments: assignments.map(({ role_id, scope_type, scope_id }) => ({ role_id, scope_type, scope_id: scope_id || null })) }),
    { success: "User created — they must change the password at first login", invalidate: inv, onSuccess: onClose },
  );
  const saveAssign = useAction(
    async () => {
      await patch(`/admin/users/${user!.id}`, { name, phone: phone || null });
      return put(`/admin/users/${user!.id}/assignments`, assignments.map(({ role_id, scope_type, scope_id }) => ({ role_id, scope_type, scope_id: scope_id || null })));
    },
    { success: "User updated", invalidate: inv, onSuccess: onClose },
  );
  const toggle = useAction(() => patch(`/admin/users/${user!.id}`, { is_active: !user!.is_active }), { success: user?.is_active ? "User disabled" : "User enabled", invalidate: inv, onSuccess: onClose });
  const reset = useAction(() => post(`/admin/users/${user!.id}/reset-password`, { password }), { success: "Password reset — user must change it at next login", invalidate: inv, onSuccess: onClose });
  const self = user?.id === me?.id;
  const roleById = Object.fromEntries((roles ?? []).map((r) => [r.id, r]));

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={user ? user.name : "New user"} description={user?.email} className="sm:max-w-2xl">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Phone"><Input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
          {!user && <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>}
          <Field label={user ? "New temporary password" : "Temporary password"} hint="Min 10 chars, 3 of: lower, upper, digit, symbol. User must change it at first login.">
            <Input type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </div>
        <div className="grid gap-2">
          <p className="text-sm font-medium">Roles</p>
          {self && <p className="text-xs text-muted-foreground">You can't change your own roles.</p>}
          {assignments.map((a, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
              <Select
                disabled={self}
                value={a.role_id}
                onChange={(e) => {
                  const r = roleById[e.target.value];
                  setAssignments((s) => s.map((x, xi) => (xi === i ? { ...x, role_id: e.target.value, scope_type: r?.default_scope ?? "GLOBAL", scope_id: null } : x)));
                }}
              >
                <option value="">Role…</option>
                {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
              <div className="grid grid-cols-[6.5rem_1fr] gap-2">
                <Select disabled={self} value={a.scope_type} onChange={(e) => setAssignments((s) => s.map((x, xi) => (xi === i ? { ...x, scope_type: e.target.value, scope_id: null } : x)))}>
                  {["GLOBAL", "PROJECT", "SITE", "STORE"].map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
                </Select>
                <ScopePicker type={a.scope_type} value={a.scope_id ?? ""} onChange={(v) => setAssignments((s) => s.map((x, xi) => (xi === i ? { ...x, scope_id: v } : x)))} />
              </div>
              <Button variant="ghost" size="icon" disabled={self} aria-label="Remove role" onClick={() => setAssignments((s) => s.filter((_, xi) => xi !== i))}><Trash2 /></Button>
            </div>
          ))}
          {!self && <Button variant="outline" size="sm" className="justify-self-start" onClick={() => setAssignments((s) => [...s, { role_id: "", scope_type: "GLOBAL", scope_id: null }])}><Plus /> Add role</Button>}
        </div>
        <div className="flex flex-col-reverse gap-2 border-t pt-4 sm:flex-row sm:justify-between">
          <div className="flex gap-2">
            {user && !self && <Button variant="ghost" className={user.is_active ? "text-destructive" : ""} loading={toggle.isPending} onClick={() => toggle.mutate(undefined)}>{user.is_active ? "Disable user" : "Enable user"}</Button>}
            {user && password && <Button variant="outline" loading={reset.isPending} onClick={() => reset.mutate(undefined)}><KeyRound /> Reset password</Button>}
          </div>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button variant="outline" onClick={onClose}>Close</Button>
            {user ? (
              <Button loading={saveAssign.isPending} disabled={self && name === user.name && phone === (user.phone ?? "")} onClick={() => saveAssign.mutate(undefined)}>Save</Button>
            ) : (
              <Button loading={create.isPending} disabled={!name || !email || password.length < 10} onClick={() => create.mutate(undefined)}>Create user</Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RolesPage() {
  const { can } = useAuth();
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: async () => (await get<Role[]>("/admin/roles")).data });
  const { data: perms } = useQuery({ queryKey: ["permissions"], queryFn: async () => (await get<Perm[]>("/admin/permissions")).data });
  const [newRole, setNewRole] = useState<Role | null>(null);
  const modules = [...new Set((perms ?? []).map((p) => p.module))];
  const save = useAction((r: Role) => put(`/admin/roles/${r.id}/permissions`, { permissions: r.permissions }), { success: "Permissions saved", invalidate: ["roles"] });
  const create = useAction(
    (r: Role) => post("/admin/roles", { code: r.code, name: r.name, description: r.description, default_scope: r.default_scope, permissions: r.permissions }),
    { success: "Role created", invalidate: ["roles"], onSuccess: () => setNewRole(null) },
  );
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const permsOf = (r: Role) => draft[r.id] ?? r.permissions;
  const toggle = (r: Role, code: string) => setDraft((d) => ({ ...d, [r.id]: permsOf(r).includes(code) ? permsOf(r).filter((c) => c !== code) : [...permsOf(r), code] }));
  const list = roles ?? [];

  return (
    <>
      <PageHeader
        title="Roles & permissions"
        subtitle="System roles are fixed (they define the standard matrix). Copy one to make a custom role you can edit."
        actions={can("roles.manage") && list.length > 0 && (
          <Button variant="outline" onClick={() => setNewRole({ ...list[0], id: "", code: "", name: "", is_system: false, user_count: 0 })}>
            <Plus /> Custom role
          </Button>
        )}
      />
      <Card className="overflow-hidden">
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
              <tr>
                <th className="sticky left-0 z-10 min-w-56 bg-muted px-3 py-2 text-left font-medium">Permission</th>
                {list.map((r) => (
                  <th key={r.id} className="min-w-24 px-2 py-2 text-center font-medium">
                    <div className="flex flex-col items-center gap-1">
                      {r.is_system && <Shield className="size-3.5" aria-label="System role" />}
                      <span className="whitespace-nowrap">{r.name}</span>
                      <span className="text-[10px] font-normal">{r.user_count} user(s)</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {modules.map((m) => (
                <Fragment key={m}>
                  <tr className="bg-muted/40">
                    <td colSpan={list.length + 1} className="sticky left-0 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{m}</td>
                  </tr>
                  {(perms ?? []).filter((p) => p.module === m).map((p) => (
                    <tr key={p.code} className="border-t">
                      <td className="sticky left-0 bg-card px-3 py-1.5">
                        <p className="font-mono text-xs">{p.code}</p>
                        <p className="text-xs text-muted-foreground">{p.description}</p>
                      </td>
                      {list.map((r) => (
                        <td key={r.id} className="text-center">
                          <input
                            type="checkbox"
                            className="size-4"
                            aria-label={`${r.name}: ${p.code}`}
                            checked={permsOf(r).includes(p.code)}
                            disabled={r.is_system || !can("roles.manage")}
                            onChange={() => toggle(r, p.code)}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {Object.keys(draft).length > 0 && (
          <div className="flex justify-end gap-2 border-t p-3">
            <Button variant="outline" onClick={() => setDraft({})}>Discard</Button>
            <Button
              loading={save.isPending}
              onClick={async () => {
                for (const r of list.filter((x) => draft[x.id])) await save.mutateAsync({ ...r, permissions: draft[r.id] });
                setDraft({});
              }}
            >
              Save changes
            </Button>
          </div>
        )}
      </Card>
      {newRole && (
        <Dialog open onOpenChange={(o) => !o && setNewRole(null)}>
          <DialogContent title="New custom role" description="Starts with the permissions of the role you copy.">
            <Field label="Copy permissions from">
              <Select onChange={(e) => setNewRole({ ...newRole, permissions: list.find((r) => r.id === e.target.value)?.permissions ?? [], default_scope: list.find((r) => r.id === e.target.value)?.default_scope ?? "GLOBAL" })}>
                {list.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
            </Field>
            <Field label="Code" hint="UPPER_SNAKE_CASE"><Input value={newRole.code} onChange={(e) => setNewRole({ ...newRole, code: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></Field>
            <Field label="Name"><Input value={newRole.name} onChange={(e) => setNewRole({ ...newRole, name: e.target.value })} /></Field>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setNewRole(null)}>Cancel</Button>
              <Button loading={create.isPending} disabled={!newRole.code || !newRole.name} onClick={() => create.mutate(newRole)}>Create</Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

interface AuditRow {
  id: number;
  user_name?: string | null;
  action: string;
  entity_type: string;
  entity_id?: string | null;
  summary?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  ip?: string | null;
  created_at: string;
}

export function AuditPage() {
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["audit", entity, action],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => get<AuditRow[]>("/admin/audit-logs", { entity_type: entity, action, cursor: pageParam, limit: 50 }),
    getNextPageParam: (last) => last.meta.nextCursor ?? undefined,
  });
  const rows = q.data?.pages.flatMap((p) => p.data) ?? [];
  return (
    <>
      <PageHeader title="Audit trail" subtitle="Who changed what, when and from where — every create, approval, posting, cancellation and login" />
      <div className="mb-3 flex flex-wrap gap-2">
        <Select value={entity} onChange={(e) => setEntity(e.target.value)} className="w-auto">
          <option value="">All records</option>
          {["boq", "grn", "rfq", "issue_note", "return", "adjustment", "consumption", "materials", "vendors", "user", "role", "report"].map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
        </Select>
        <Input value={action} onChange={(e) => setAction(e.target.value.toUpperCase())} placeholder="Action e.g. APPROVE" className="w-48" />
      </div>
      <Card className="overflow-hidden">
        {!rows.length && !q.isLoading && <EmptyState title="No audit entries" />}
        <ul className="divide-y">
          {rows.map((r) => (
            <li key={r.id}>
              <button className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-accent/50" onClick={() => setOpen(open === r.id ? null : r.id)}>
                <StatusBadge status={r.action} className="mt-0.5" />
                <div className="min-w-0 flex-1 text-sm">
                  <p className="line-clamp-2 break-words"><span className="font-medium">{r.user_name ?? "System"}</span> <span className="text-muted-foreground">· {humanize(r.entity_type)}</span> {r.summary && `· ${r.summary}`}</p>
                  <p className="text-xs text-muted-foreground">{fmtDateTime(r.created_at)} · {r.ip ?? "—"}</p>
                </div>
              </button>
              {open === r.id && (r.before || r.after) && (
                <div className="grid gap-2 bg-muted/40 px-4 py-3 text-xs sm:grid-cols-2">
                  <pre className={cn("overflow-auto rounded bg-card p-2", !r.before && "opacity-50")}>{JSON.stringify(r.before ?? {}, null, 2)}</pre>
                  <pre className="overflow-auto rounded bg-card p-2">{JSON.stringify(r.after ?? {}, null, 2)}</pre>
                </div>
              )}
            </li>
          ))}
        </ul>
        {q.hasNextPage && <div className="border-t p-3 text-center"><Button variant="outline" loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>Load older</Button></div>}
      </Card>
    </>
  );
}
