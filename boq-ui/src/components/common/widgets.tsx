import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDot, FileText, Paperclip, Trash2, Upload, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Textarea } from "@/components/ui/input";
import { download, errorMessage, get, http } from "@/lib/api";
import { fmtDateTime, fmtMoney, fmtMoneyShort, fmtPct, fmtQty, humanize } from "@/lib/format";
import { del } from "@/lib/hooks";
import type { Approval, Attachment } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Button that opens a confirm dialog, optionally collecting a comment / reason. */
export function ConfirmAction({
  label,
  title,
  description,
  confirmLabel,
  onConfirm,
  comment,
  variant = "default",
  icon,
  size,
  children,
}: {
  label: string;
  title: string;
  description?: string;
  confirmLabel?: string;
  onConfirm: (comment: string) => Promise<unknown>;
  comment?: "optional" | "required";
  variant?: ButtonProps["variant"];
  icon?: React.ReactNode;
  size?: ButtonProps["size"];
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await onConfirm(text.trim());
      setOpen(false);
      setText("");
    } catch {
      /* toast shown by the mutation */
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button variant={variant} size={size} onClick={() => setOpen(true)}>
        {icon}
        {label}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={title} description={description}>
          {children}
          {comment && (
            <Field label={comment === "required" ? "Reason (required)" : "Comment (optional)"}>
              <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000} />
            </Field>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Go back
            </Button>
            <Button variant={variant === "outline" || variant === "ghost" ? "default" : variant} loading={busy} disabled={comment === "required" && text.trim().length < 3} onClick={run}>
              {confirmLabel ?? label}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ApprovalTimeline({ items }: { items: Approval[] }) {
  if (!items.length) return <p className="px-4 py-6 text-sm text-muted-foreground md:px-5">No workflow activity yet.</p>;
  return (
    <ol className="relative grid gap-4 px-4 py-4 md:px-5">
      {items.map((a, i) => {
        const Icon = a.action === "APPROVED" || a.action === "CLOSED" ? CheckCircle2 : a.action === "REJECTED" || a.action === "CANCELLED" ? XCircle : CircleDot;
        const color = a.action === "APPROVED" ? "text-success" : a.action === "REJECTED" || a.action === "CANCELLED" ? "text-destructive" : "text-primary";
        return (
          <li key={i} className="flex gap-3">
            <Icon className={cn("mt-0.5 size-5 shrink-0", color)} />
            <div className="min-w-0">
              <p className="text-sm">
                <span className="font-medium">{humanize(a.action)}</span>
                <span className="text-muted-foreground"> · {humanize(a.stage)} · {a.actor_name}</span>
              </p>
              {a.comment && <p className="mt-0.5 text-sm text-muted-foreground">“{a.comment}”</p>}
              <p className="text-xs text-muted-foreground">{fmtDateTime(a.acted_at)}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function KpiCard({ label, value, format, hint }: { label: string; value: number | null; format: string; hint?: string | null }) {
  const shown = format === "money" ? fmtMoneyShort(value) : format === "pct" ? fmtPct(value) : fmtQty(value);
  return (
    <Card className="p-4">
      <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums" title={format === "money" ? fmtMoney(value) : undefined}>
        {shown}
      </p>
      {hint && <p className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

export function Attachments({ entityType, entityId, canUpload }: { entityType: string; entityId: string; canUpload: boolean }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const key = ["attachments", entityType, entityId];
  const { data } = useQuery({ queryKey: key, queryFn: () => get<Attachment[]>("/attachments", { entity_type: entityType, entity_id: entityId }) });
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("entity_type", entityType);
    fd.append("entity_id", entityId);
    fd.append("file", file);
    setBusy(true);
    try {
      await http.post("/attachments", fd);
      toast.success("File attached");
      qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const files = data?.data ?? [];
  return (
    <div className="grid gap-2 px-4 py-4 md:px-5">
      {files.map((f) => (
        <div key={f.id} className="flex items-center gap-3 rounded-md border px-3 py-2">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <button className="min-w-0 flex-1 truncate text-left text-sm hover:underline" onClick={() => download(`/attachments/${f.id}/download`, undefined, "open")}>
            {f.filename}
          </button>
          <span className="hidden text-xs text-muted-foreground sm:inline">{(f.size / 1024).toFixed(0)} KB · {f.uploaded_by_name}</span>
          {canUpload && (
            <Button
              variant="ghost"
              size="icon"
              aria-label="Remove"
              onClick={async () => {
                try {
                  await del(`/attachments/${f.id}`);
                  qc.invalidateQueries({ queryKey: key });
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              <Trash2 />
            </Button>
          )}
        </div>
      ))}
      {!files.length && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Paperclip className="size-4" /> No attachments
        </p>
      )}
      {canUpload && (
        <>
          <input
            ref={input}
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            capture="environment"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload(f);
              e.target.value = "";
            }}
          />
          <Button variant="outline" size="sm" className="justify-self-start" loading={busy} onClick={() => input.current?.click()}>
            <Upload /> Attach photo / PDF
          </Button>
        </>
      )}
    </div>
  );
}

export function QtyInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" inputMode="decimal" step="any" min="0" {...props} className={cn("flex h-10 w-full rounded-md border border-input bg-card px-3 text-right tabular-nums shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:h-9", props.className)} />;
}
