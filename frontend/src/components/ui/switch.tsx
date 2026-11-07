"use client";

import * as React from "react";
import { Switch as SwitchPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

// The iOS switch, in its green, at a size that suits a pointer as well as a thumb.
function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full p-0.5 outline-none transition-colors duration-200",
        "data-[state=checked]:bg-[#34c759] data-[state=unchecked]:bg-slate-200 dark:data-[state=checked]:bg-[#30d158] focus-visible:ring-[3px] focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block size-[18px] rounded-full bg-white ring-1 ring-black/10 transition-transform duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
