import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4 [&>svg]:size-3 shrink-0",
  {
    variants: {
      variant: {
        default: "bg-secondary text-muted-foreground",
        primary: "bg-accent text-accent-foreground",
        profit: "bg-profit-soft text-profit",
        loss: "bg-loss-soft text-loss",
        warn: "bg-warn-soft text-warn",
        solid: "bg-destructive text-white",
        outline: "text-muted-foreground ring-1 ring-inset ring-separator",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
