"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { AiBrief, AiStatus } from "@/lib/types";

export function AiBriefCard({ underlying }: { underlying: string }) {
  const { mutate } = useSWRConfig();
  const { data: status } = useApi<AiStatus>("/ai/status");
  const { data: briefs } = useApi<AiBrief[]>(`/ai/brief?underlying=${underlying}`);
  const [busy, setBusy] = useState(false);
  const brief = briefs?.[0];

  const refresh = async () => {
    setBusy(true);
    try {
      await api("/ai/brief", "POST", { underlying });
      await mutate(`/ai/brief?underlying=${underlying}`);
    } catch (e) {
      toast.error("Brief failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Sparkles className="size-4 text-slate-400" /> AI market brief
        </CardTitle>
        <Button variant="ghost" size="xs" onClick={refresh} disabled={busy || !!status?.ready}>
          {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />} Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-[13px] leading-relaxed">
        {status?.ready && (
          <p className="text-muted-foreground">
            {status.ready}. <Link href="/settings" className="font-medium text-primary hover:underline">Settings → AI</Link>
          </p>
        )}
        {brief?.error && <p className="text-loss">Last attempt failed: {brief.error.slice(0, 240)}</p>}
        {brief?.content && (
          <>
            <p className="font-semibold text-slate-900">{brief.content.headline}</p>
            <p>{brief.content.volatility}</p>
            <p>{brief.content.positioning}</p>
            <p className="text-slate-600">{brief.content.summary}</p>
            {brief.content.watch.length > 0 && (
              <ul className="space-y-0.5 text-[12px] text-muted-foreground">
                {brief.content.watch.map((w) => (
                  <li key={w}>→ {w}</li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <Badge variant="outline">written by {brief.model}</Badge>
              <Badge>{brief.content.confidence} confidence</Badge>
              <span className="text-[11px] text-muted-foreground">{ago(brief.ts)}</span>
            </div>
            <p className="text-[11px] text-muted-foreground">Prose from a language model over the measured numbers: the weakest source on this page. Check it against the figures.</p>
          </>
        )}
        {!brief && !status?.ready && <p className="text-muted-foreground">No brief yet. Refresh to write one.</p>}
      </CardContent>
    </Card>
  );
}
