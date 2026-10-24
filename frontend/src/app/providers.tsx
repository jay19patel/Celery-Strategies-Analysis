"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { fetcher } from "@/lib/api";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, shouldRetryOnError: false }}>
      <TooltipProvider>
        {children}
        <Toaster />
      </TooltipProvider>
    </SWRConfig>
  );
}
