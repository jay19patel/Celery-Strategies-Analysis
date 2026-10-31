"use client";

import * as React from "react";
import { Dialog as SheetPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

const Sheet = SheetPrimitive.Root;
const SheetTitle = SheetPrimitive.Title;

const SIDES = {
  left: "inset-y-0 left-0 h-full w-72 sm:w-80 data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left",
  bottom:
    "inset-x-0 bottom-0 max-h-[85dvh] rounded-t-2xl pb-safe data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
};

function SheetContent({ className, children, side = "left", ...props }: React.ComponentProps<typeof SheetPrimitive.Content> & { side?: keyof typeof SIDES }) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay className="fixed inset-0 z-50 bg-slate-900/30 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
      <SheetPrimitive.Content
        className={cn(
          "fixed z-50 flex flex-col bg-sidebar shadow-2xl duration-300 ease-out data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:duration-200",
          SIDES[side],
          className,
        )}
        {...props}
      >
        {children}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export { Sheet, SheetContent, SheetTitle };
