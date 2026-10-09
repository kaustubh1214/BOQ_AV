import { KeyRound, Lock } from "lucide-react";
import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { errorMessage, setAccessToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { post } from "@/lib/hooks";

export function LoginPage() {
  const { me, login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (me) return <Navigate to={me.must_change_password ? "/change-password" : ((loc.state as { from?: string })?.from ?? "/")} replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const m = await login(email.trim(), password);
      nav(m.must_change_password ? "/change-password" : ((loc.state as { from?: string })?.from ?? "/"), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <div className="relative flex items-center gap-3">
          <p className="text-xs font-medium tracking-[0.14em] text-brand">BOQ & MATERIAL MANAGEMENT</p>
        </div>
        <div className="relative mx-auto grid place-items-center">
          <div className="rounded-2xl bg-white p-6 ring-1 ring-white/10">
            <img src="/logo.webp" alt="AV Group Private Limited — Delivering Your Dream Homes" className="size-72 object-contain" />
          </div>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-2xl font-semibold leading-tight">Plan centrally. Issue precisely. Know every kilogram.</h2>
          <p className="mt-3 text-sm leading-relaxed opacity-75">
            Market research → BOQ → inward → site requests → issue notes → daily consumption. One ledger, every site, full audit trail.
          </p>
          <p className="mt-6 text-xs opacity-50">© {new Date().getFullYear()} AV Group Private Limited</p>
        </div>
      </div>
      <div className="flex items-center justify-center p-4 sm:p-8">
        <Card className="w-full max-w-sm p-6 sm:p-8">
          <div className="mb-6 flex flex-col items-center gap-1 lg:hidden">
            <img src="/logo.webp" alt="AV Group Private Limited" className="size-40 object-contain" />
          </div>
          <h1 className="text-xl font-semibold">Sign in</h1>
          <p className="mb-6 mt-1 text-sm text-muted-foreground">Use your AV Group work account.</p>
          <form onSubmit={submit} className="grid gap-4">
            <Field label="Email">
              <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@avgroup.in" />
            </Field>
            <Field label="Password">
              <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {error && <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
            <Button type="submit" size="lg" loading={busy} className="w-full">
              <Lock /> Sign in
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}

export function ChangePasswordPage() {
  const { me, reload } = useAuth();
  const nav = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  if (!me) return <Navigate to="/login" replace />;
  const mismatch = confirm.length > 0 && next !== confirm;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    setBusy(true);
    try {
      const r = await post<{ access_token: string }>("/auth/change-password", { current_password: current, new_password: next });
      setAccessToken(r.access_token);
      await reload();
      toast.success("Password changed. Other sessions were signed out.");
      nav("/", { replace: true });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[70dvh] items-center justify-center">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound className="size-5 text-primary" />
          <h1 className="text-lg font-semibold">Change password</h1>
        </div>
        {me.must_change_password && <p className="mb-4 rounded-md bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">Please set a new password before continuing.</p>}
        <form onSubmit={submit} className="grid gap-4">
          <Field label="Current password">
            <Input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
          <Field label="New password" hint="At least 10 characters, mixing 3 of: lowercase, uppercase, digits, symbols.">
            <Input type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Field label="Confirm new password" error={mismatch ? "Passwords don't match" : undefined}>
            <Input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch} />
          </Field>
          <Button type="submit" loading={busy} disabled={mismatch}>
            Update password
          </Button>
        </form>
      </Card>
    </div>
  );
}
