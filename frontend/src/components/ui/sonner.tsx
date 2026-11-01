"use client";

import { Toaster as Sonner, type ToasterProps } from "sonner";

function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="system"
      position="top-center"
      richColors
      closeButton
      offset={{ top: "calc(env(safe-area-inset-top) + 12px)" }}
      mobileOffset={{ top: "calc(env(safe-area-inset-top) + 8px)" }}
      toastOptions={{ classNames: { toast: "text-[13px] !rounded-2xl" } }}
      {...props}
    />
  );
}

export { Toaster };
