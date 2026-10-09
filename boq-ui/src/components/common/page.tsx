import { AlertTriangle, ArrowLeft, Inbox } from "lucide-react";
import type * as React from "react";
import { useNavigate } from "react-router";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { errorMessage } from "@/lib/api";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
  badge,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  back?: string | boolean;
  badge?: React.ReactNode;
}) {
  const nav = useNavigate();
  return (
    <div className="mb-4 flex flex-col gap-3 md:mb-6 md:flex-row md:items-end md:justify-between">
      <div className="flex min-w-0 items-start gap-2">
        {back && (
          <Button variant="ghost" size="icon" className="-ml-2 shrink-0" onClick={() => (typeof back === "string" ? nav(back) : nav(-1))} aria-label="Back">
            <ArrowLeft />
          </Button>
        )}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight md:text-[22px]">{title}</h1>
            {badge}
          </div>
          {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const TONES: Record<string, BadgeTone> = {
  DRAFT: "gray",
  SUBMITTED: "amber",
  RETURNED: "amber",
  PM_APPROVED: "violet",
  STORE_APPROVED: "blue",
  APPROVED: "green",
  POSTED: "green",
  ISSUED: "blue",
  PARTIALLY_ISSUED: "violet",
  RECEIVED: "green",
  PARTIALLY_RECEIVED: "amber",
  DISPATCHED: "blue",
  REJECTED: "red",
  CANCELLED: "red",
  SUPERSEDED: "gray",
  CLOSED: "gray",
  URGENT: "red",
  NORMAL: "gray",
  LOW: "gray",
  ACTIVE: "green",
  ON_HOLD: "amber",
  COMPLETED: "gray",
};

const LABELS: Record<string, string> = { ISSUED: "Issued / in transit", PARTIALLY_RECEIVED: "Received (loss)" };

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge tone={TONES[status] ?? "gray"} className={className}>
      {LABELS[status] ?? humanize(status)}
    </Badge>
  );
}

export function EmptyState({ title = "Nothing here yet", hint, action }: { title?: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      <Inbox className="size-10 text-muted-foreground/60" />
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-sm text-sm text-muted-foreground">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <Card className="flex flex-col items-center gap-3 border-destructive/30 p-8 text-center">
      <AlertTriangle className="size-8 text-destructive" />
      <p className="text-sm">{errorMessage(error)}</p>
      {retry && (
        <Button variant="outline" size="sm" onClick={retry}>
          Try again
        </Button>
      )}
    </Card>
  );
}

export function LoadingRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="grid gap-2 p-4">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10" />
      ))}
    </div>
  );
}

export function InfoGrid({ items, className }: { items: [string, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 gap-x-4 gap-y-3 text-sm md:grid-cols-4", className)}>
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{k}</dt>
          <dd className="mt-0.5 truncate font-medium">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Section({ title, actions, children, className }: { title: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <Card className={cn("overflow-hidden", className)}>
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3 md:px-5">
        <h2 className="font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </Card>
  );
}

/** Sticky bottom action bar on phones, inline on desktop. */
export function ActionBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="sticky bottom-0 z-20 -mx-4 mt-4 flex flex-wrap justify-end gap-2 border-t bg-background/95 px-4 py-3 pb-safe backdrop-blur md:static md:mx-0 md:border-0 md:bg-transparent md:px-0 md:backdrop-blur-none">
      {children}
    </div>
  );
}
