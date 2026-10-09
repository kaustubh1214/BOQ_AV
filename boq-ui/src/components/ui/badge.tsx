import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4", {
  variants: {
    tone: {
      gray: "border-transparent bg-muted text-muted-foreground",
      blue: "border-transparent bg-sky-500/15 text-sky-700 dark:text-sky-300",
      amber: "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300",
      green: "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
      red: "border-transparent bg-rose-500/15 text-rose-700 dark:text-rose-300",
      violet: "border-transparent bg-violet-500/15 text-violet-700 dark:text-violet-300",
      outline: "text-foreground",
    },
  },
  defaultVariants: { tone: "gray" },
});

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
