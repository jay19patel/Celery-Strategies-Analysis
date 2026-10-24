import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 [&>svg]:size-3 shrink-0",
  {
    variants: {
      variant: {
        default: "border-transparent bg-slate-100 text-slate-700",
        primary: "border-transparent bg-accent text-accent-foreground",
        profit: "border-transparent bg-profit-soft text-profit",
        loss: "border-transparent bg-loss-soft text-loss",
        warn: "border-transparent bg-warn-soft text-warn",
        solid: "border-transparent bg-destructive text-white",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
