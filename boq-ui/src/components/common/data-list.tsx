import { ChevronLeft, ChevronRight, ChevronRight as Go } from "lucide-react";
import type * as React from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { EmptyState, LoadingRows } from "./page";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
  /** Shown in the mobile card body (label: value). Defaults to true except for the primary column. */
  mobile?: boolean;
}

/**
 * Responsive list: a real table from `md` up, stacked cards on phones.
 * The first column is the card title; `mobileMeta` (optional) renders the card's right-hand badge.
 */
export function DataList<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  loading,
  empty,
  mobileMeta,
  footer,
  dense,
}: {
  rows: T[] | undefined;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  loading?: boolean;
  empty?: React.ReactNode;
  mobileMeta?: (row: T) => React.ReactNode;
  footer?: React.ReactNode;
  dense?: boolean;
}) {
  if (loading) return <LoadingRows />;
  if (!rows?.length) return <>{empty ?? <EmptyState />}</>;
  const [primary, ...rest] = columns;
  const align = (a?: string) => (a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left");

  return (
    <>
      {/* phones */}
      <ul className="divide-y md:hidden">
        {rows.map((row) => (
          <li key={rowKey(row)}>
            <div
              role={onRowClick ? "button" : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={() => onRowClick?.(row)}
              onKeyDown={(e) => e.key === "Enter" && onRowClick?.(row)}
              className={cn("flex gap-3 px-4 py-3", onRowClick && "cursor-pointer active:bg-accent")}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 font-medium">{primary.cell(row)}</div>
                  {mobileMeta?.(row)}
                </div>
                <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  {rest
                    .filter((c) => c.mobile !== false)
                    .map((c) => (
                      <div key={c.key} className="min-w-0">
                        <dt className="text-muted-foreground">{c.header}</dt>
                        <dd className="truncate text-sm text-foreground">{c.cell(row)}</dd>
                      </div>
                    ))}
                </dl>
              </div>
              {onRowClick && <Go className="mt-1 size-4 shrink-0 text-muted-foreground" />}
            </div>
          </li>
        ))}
      </ul>
      {/* tablet / desktop */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead className="sticky top-0 border-b bg-muted/50 text-xs text-muted-foreground">
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={cn("whitespace-nowrap px-4 py-2 font-medium", align(c.align), c.className)}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={() => onRowClick?.(row)}
                className={cn("hover:bg-accent/40", onRowClick && "cursor-pointer")}
              >
                {columns.map((c) => (
                  <td key={c.key} className={cn("px-4", dense ? "py-2" : "py-3", align(c.align), c.className)}>
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footer}
    </>
  );
}

export function Pager({
  page,
  totalPages,
  total,
  onPage,
}: {
  page: number;
  totalPages?: number;
  total?: number;
  onPage: (p: number) => void;
}) {
  if (!totalPages || totalPages <= 1) return total ? <p className="border-t px-4 py-3 text-xs text-muted-foreground">{total} record(s)</p> : null;
  return (
    <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
      <span className="text-xs text-muted-foreground">
        Page {page} of {totalPages} · {total} records
      </span>
      <div className="flex gap-1">
        <Button variant="outline" size="icon" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        <Button variant="outline" size="icon" disabled={page >= totalPages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
