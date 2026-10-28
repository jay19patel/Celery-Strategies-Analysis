"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSWRConfig } from "swr";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { toast } from "sonner";

import { FormContext, type FormState } from "@/components/settings/fields";
import { AiTab, AutoTab, BrokersTab, NotifyTab, PaperTab, RiskTab, SecurityTab, ZeroHeroTab } from "@/components/settings/sections";
import { PageHeader } from "@/components/shared/page";
import { LoadError, Loading, Notice } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useApi } from "@/lib/live";
import type { SettingsResponse } from "@/lib/types";

const SECRETS = ["delta_api_key", "delta_api_secret", "telegram_bot_token", "email_smtp_pass"];
// Derived values the backend adds to `values`; they are read-only and never sent back.
const DERIVED = ["active_brokers", "is_real_money", "data_env", "has_delta_credentials", "rest_url"];

const TABS = [
  { value: "brokers", label: "Brokers" },
  { value: "paper", label: "Paper" },
  { value: "auto", label: "Auto-trader" },
  { value: "zerohero", label: "Zero-Hero" },
  { value: "risk", label: "Risk & exits" },
  { value: "ai", label: "AI" },
  { value: "notifications", label: "Notifications" },
  { value: "security", label: "Security" },
];

function editable(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([k]) => !SECRETS.includes(k) && !DERIVED.includes(k)));
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export default function SettingsPage() {
  const { data, error, isLoading } = useApi<SettingsResponse>("/settings");
  const { mutate } = useSWRConfig();
  const [tab, setTab] = useState("brokers");
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [invalid, setInvalidSet] = useState<Set<string>>(new Set());
  const [generation, setGeneration] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [phrase, setPhrase] = useState("");

  useEffect(() => {
    const fromHash = () => {
      const hash = window.location.hash.slice(1);
      if (TABS.some((t) => t.value === hash)) setTab(hash);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  const stored = useMemo(() => (data ? editable(data.values) : null), [data]);
  const current = draft ?? stored;

  const reset = useCallback((response?: SettingsResponse) => {
    setDraft(response ? editable(response.values) : null);
    setSecrets({});
    setInvalidSet(new Set());
    setGeneration((g) => g + 1);
  }, []);

  const set = useCallback(
    (name: string, value: unknown) => setDraft((d) => ({ ...(d ?? stored ?? {}), [name]: value })),
    [stored],
  );
  const setSecret = useCallback((name: string, value: string) => setSecrets((s) => ({ ...s, [name]: value })), []);
  const setInvalid = useCallback((name: string, bad: boolean) => {
    setInvalidSet((prev) => {
      if (prev.has(name) === bad) return prev;
      const next = new Set(prev);
      if (bad) next.add(name);
      else next.delete(name);
      return next;
    });
  }, []);

  const changes = useMemo(() => {
    if (!current || !stored) return {};
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(current)) if (!same(v, stored[k])) out[k] = v;
    for (const [k, v] of Object.entries(secrets)) if (v.trim()) out[k] = v.trim();
    return out;
  }, [current, stored, secrets]);
  const dirty = Object.keys(changes).length > 0;

  const send = async (confirm = "") => {
    setSaving(true);
    try {
      const response = await api<SettingsResponse>("/settings", "PATCH", { changes, confirm });
      await mutate("/settings", response, { revalidate: false });
      reset(response);
      setConfirmOpen(false);
      setPhrase("");
      toast.success("Settings saved", { description: Object.keys(changes).join(", ") });
    } catch (e) {
      toast.error("Not saved", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    if (!data || !current) return;
    const goesLive = Boolean(current.delta_active) && current.delta_env === "live" && !data.values.is_real_money;
    if (goesLive) setConfirmOpen(true);
    else void send();
  };

  const form: FormState | null =
    current && stored && data ? { draft: current, stored: data.values, limits: data.limits, set, secrets, setSecret, setInvalid, generation } : null;

  return (
    <>
      <PageHeader title="Settings" description="Stored in the engine's database and applied immediately — no restart." />
      <LoadError error={error} />
      {isLoading && !data && <Loading rows={8} />}
      {form && data && (
        <FormContext.Provider value={form}>
          <Tabs
            value={tab}
            onValueChange={(v) => {
              setTab(v);
              window.history.replaceState(null, "", `#${v}`);
            }}
          >
            <div className="overflow-x-auto">
              <TabsList>
                {TABS.map((t) => (
                  <TabsTrigger key={t.value} value={t.value}>
                    {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            <Card>
              <CardContent className="p-5">
                <TabsContent value="brokers">
                  <BrokersTab onCleared={(r) => { void mutate("/settings", r, { revalidate: false }); reset(r); }} />
                </TabsContent>
                <TabsContent value="paper">
                  <PaperTab />
                </TabsContent>
                <TabsContent value="auto">
                  <AutoTab strategies={data.strategies} />
                </TabsContent>
                <TabsContent value="zerohero">
                  <ZeroHeroTab />
                </TabsContent>
                <TabsContent value="risk">
                  <RiskTab />
                </TabsContent>
                <TabsContent value="ai">
                  <AiTab />
                </TabsContent>
                <TabsContent value="notifications">
                  <NotifyTab />
                </TabsContent>
                <TabsContent value="security" id="security">
                  <SecurityTab />
                </TabsContent>
              </CardContent>
            </Card>
          </Tabs>
        </FormContext.Provider>
      )}

      {dirty && (
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 shadow-lg">
          <div className="min-w-0 text-[13px]">
            <span className="font-medium">Unsaved changes: </span>
            <span className="text-muted-foreground">{Object.keys(changes).join(", ")}</span>
            {invalid.size > 0 && <span className="ml-2 text-loss">Fix {[...invalid].join(", ")} first.</span>}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => reset()} disabled={saving}>
              <RotateCcw /> Discard
            </Button>
            <Button size="sm" onClick={save} disabled={saving || invalid.size > 0}>
              {saving ? <Loader2 className="animate-spin" /> : <Save />} Save changes
            </Button>
          </div>
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={(o) => !saving && setConfirmOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Switch to real-money trading?</DialogTitle>
            <DialogDescription>
              Delta will place orders on your live account. Auto-trading on Delta stays off until you switch it on yourself.
            </DialogDescription>
          </DialogHeader>
          <Notice tone="loss">
            Type <b className="num">{data?.live_confirm_phrase}</b> to confirm.
          </Notice>
          <Input value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder={data?.live_confirm_phrase} autoFocus />
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => send(phrase)} disabled={saving || phrase !== data?.live_confirm_phrase}>
              {saving && <Loader2 className="animate-spin" />} Go live and save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
