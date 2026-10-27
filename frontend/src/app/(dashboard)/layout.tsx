"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Loader2, RefreshCw, WifiOff } from "lucide-react";

import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";
import { Button } from "@/components/ui/button";
import { LiveProvider, useApi } from "@/lib/live";

interface Session {
  authenticated: boolean;
  using_default_pin: boolean | null;
}

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: session, error, isValidating, mutate } = useApi<Session>("/auth/session", { revalidateOnFocus: true });

  useEffect(() => {
    if (session && !session.authenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [session, pathname, router]);

  // Only a cold failure takes the dashboard away. Once a session has loaded, a later request
  // failing is usually the backend reloading or a laptop waking — SWR is already retrying, so that
  // shows as a banner over the page you were on rather than throwing your work away.
  if (error && !session) {
    return (
      <div className="grid min-h-screen place-items-center p-6 text-center text-sm text-muted-foreground">
        <div>
          <div className="font-medium text-slate-800">The backend is not reachable.</div>
          <div className="mt-1">Start it with <code className="num rounded bg-slate-100 px-1.5 py-0.5">./start.sh</code>, then retry.</div>
          <Button variant="outline" size="sm" className="mt-4" onClick={() => mutate()} disabled={isValidating}>
            {isValidating ? <Loader2 className="animate-spin" /> : <RefreshCw />} Retry
          </Button>
        </div>
      </div>
    );
  }
  if (!session?.authenticated) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <LiveProvider>
      <Sidebar />
      <div className="min-w-0 lg:pl-60">
        <Header />
        {error && (
          <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-warn-soft px-4 py-2 text-[12.5px] text-amber-900 lg:px-6">
            <WifiOff className="size-3.5" />
            Lost contact with the backend — showing the last data received. Retrying automatically.
            <Button variant="ghost" size="xs" className="ml-auto" onClick={() => mutate()} disabled={isValidating}>
              {isValidating ? <Loader2 className="animate-spin" /> : <RefreshCw />} Retry now
            </Button>
          </div>
        )}
        {session.using_default_pin && (
          <div className="border-b border-amber-200 bg-warn-soft px-4 py-2 text-[12.5px] text-amber-900 lg:px-6">
            The dashboard PIN is still the published default. <Link href="/settings#security" className="font-semibold underline">Change it</Link> before this is reachable from anywhere but this machine.
          </div>
        )}
        <main className="mx-auto max-w-[1600px] space-y-5 p-4 lg:p-6">{children}</main>
      </div>
    </LiveProvider>
  );
}
