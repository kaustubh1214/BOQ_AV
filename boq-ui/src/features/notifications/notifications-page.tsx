import { CheckCheck } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { Pager } from "@/components/common/data-list";
import { EmptyState, LoadingRows, PageHeader } from "@/components/common/page";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { fmtDateTime } from "@/lib/format";
import { post, useAction, useList } from "@/lib/hooks";
import type { Notification } from "@/lib/types";
import { cn } from "@/lib/utils";

const LINKS: Record<string, string> = { boq: "/boqs", grn: "/grns", rfq: "/rfqs", issue_note: "/issue-notes", return: "/returns", adjustment: "/adjustments" };

export function NotificationsPage() {
  const nav = useNavigate();
  const [page, setPage] = useState(1);
  const [unread, setUnread] = useState(false);
  const { data, isLoading } = useList<Notification>("notifications", "/notifications", { page, limit: 25, unread_only: unread });
  const read = useAction((id: string) => post(`/notifications/${id}/read`), { invalidate: ["notifications"] });
  const readAll = useAction(() => post("/notifications/read-all"), { success: "All caught up", invalidate: ["notifications"] });
  return (
    <>
      <PageHeader
        title="Notifications"
        actions={
          <>
            <Button variant={unread ? "default" : "outline"} onClick={() => (setUnread(!unread), setPage(1))}>Unread only</Button>
            <Button variant="outline" onClick={() => readAll.mutate(undefined)}><CheckCheck /> Mark all read</Button>
          </>
        }
      />
      <Card className="overflow-hidden">
        {isLoading ? <LoadingRows /> : !data?.data.length ? <EmptyState title="No notifications" /> : (
          <ul className="divide-y">
            {data.data.map((n) => (
              <li key={n.id}>
                <button
                  className={cn("flex w-full gap-3 px-4 py-3 text-left hover:bg-accent/50", !n.read_at && "bg-primary/5")}
                  onClick={() => {
                    if (!n.read_at) read.mutate(n.id);
                    if (n.entity_type && n.entity_id && LINKS[n.entity_type]) nav(`${LINKS[n.entity_type]}/${n.entity_id}`);
                  }}
                >
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-primary")} />
                  <div className="min-w-0 flex-1">
                    <p className={cn("text-sm", !n.read_at && "font-semibold")}>{n.title}</p>
                    {n.body && <p className="text-sm text-muted-foreground">{n.body}</p>}
                    <p className="mt-0.5 text-xs text-muted-foreground">{fmtDateTime(n.created_at)}</p>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={setPage} />
      </Card>
    </>
  );
}
