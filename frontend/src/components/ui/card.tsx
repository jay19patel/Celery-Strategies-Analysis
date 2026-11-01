import * as React from "react";

import { cn } from "@/lib/utils";

// An inset grouped section: a plain surface on the grouped background, no border and no shadow.
function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card" className={cn("min-w-0 rounded-2xl bg-card text-card-foreground", className)} {...props} />;
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("flex flex-wrap items-center justify-between gap-2 border-b border-separator px-4 py-3 sm:gap-3", className)}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-title" className={cn("flex items-center gap-2 text-[15px] font-semibold leading-tight tracking-[-0.01em]", className)} {...props} />;
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-description" className={cn("text-[13px] text-muted-foreground", className)} {...props} />;
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-content" className={cn("p-4", className)} {...props} />;
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-footer" className={cn("flex items-center border-t border-separator px-4 py-3", className)} {...props} />;
}

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
