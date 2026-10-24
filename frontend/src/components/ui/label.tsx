"use client";

import * as React from "react";
import { Label as LabelPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function Label({ className, ...props }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn("flex select-none items-center gap-2 text-[13px] font-medium leading-none text-slate-700", className)}
      {...props}
    />
  );
}

export { Label };
