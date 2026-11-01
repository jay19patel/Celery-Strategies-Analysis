import * as React from "react";

import { cn } from "@/lib/utils";

// A filled field, as in iOS forms: no outline until focus.
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-9 w-full min-w-0 rounded-[10px] bg-input px-3 py-1 text-sm outline-none transition-[background-color,box-shadow] placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:bg-card focus-visible:ring-2 focus-visible:ring-primary aria-invalid:ring-2 aria-invalid:ring-destructive",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
