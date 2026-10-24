"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CandlestickChart, Loader2, LockKeyhole } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/";
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", "POST", { pin });
      router.replace(next.startsWith("/") ? next : "/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
      setPin("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border bg-card p-7 shadow-sm">
      <div className="flex items-center gap-3">
        <div className="grid size-10 place-items-center rounded-xl bg-primary text-white">
          <CandlestickChart className="size-5" />
        </div>
        <div>
          <div className="text-base font-semibold">TradeBuddy</div>
          <div className="text-xs text-muted-foreground">Options desk · Delta Exchange India</div>
        </div>
      </div>
      <div className="space-y-2">
        <label htmlFor="pin" className="flex items-center gap-1.5 text-[13px] font-medium text-slate-700">
          <LockKeyhole className="size-3.5" /> Dashboard PIN
        </label>
        <Input
          id="pin"
          type="password"
          inputMode="numeric"
          autoComplete="current-password"
          autoFocus
          maxLength={12}
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
          className="num h-11 text-center text-lg tracking-[0.5em]"
          placeholder="••••••"
        />
        {error && <p className="text-[13px] text-loss">{error}</p>}
      </div>
      <Button type="submit" className="h-10 w-full" disabled={busy || pin.length < 4}>
        {busy && <Loader2 className="animate-spin" />} Unlock
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-background p-4">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
