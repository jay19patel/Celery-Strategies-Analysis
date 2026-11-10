"use client";

// One component per Settings tab. Fields are bound to the draft through `useForm`; actions that
// talk to the backend directly (connection tests, clearing keys, changing the PIN) live here too.

import { useState } from "react";
import { useSWRConfig } from "swr";
import { AlertTriangle, Check, Loader2, PlugZap, Send, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import Link from "next/link";

import { Advanced, FieldGrid, NumberField, SecretField, Section, SelectField, SwitchField, TextField, useForm } from "@/components/settings/fields";
import { Confirm } from "@/components/shared/confirm";
import { Notice } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useApi } from "@/lib/live";
import type { AiStatus, SettingsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function Toggles({ name, options }: { name: string; options: { value: string; label: string }[] }) {
  const { draft, set } = useForm();
  const current = (draft[name] as string[] | undefined) ?? [];
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const on = current.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => set(name, on ? current.filter((v) => v !== o.value) : [...current, o.value])}
            className={cn(
              "rounded-md border px-3 py-1.5 text-[13px] font-medium transition-colors",
              on ? "border-primary bg-accent text-accent-foreground" : "bg-card text-slate-600 hover:bg-muted",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// -- brokers ------------------------------------------------------------------------------

export function BrokersTab({ onCleared }: { onCleared: (r: SettingsResponse) => void }) {
  const { draft, secrets } = useForm();
  const [testing, setTesting] = useState(false);

  const test = async () => {
    setTesting(true);
    try {
      const r = await api<{ ok: boolean; env: string; assets?: string[]; error?: string }>("/settings/delta/test", "POST", {
        env: draft.delta_env,
        api_key: secrets.delta_api_key ?? "",
        api_secret: secrets.delta_api_secret ?? "",
      });
      if (r.ok) toast.success(`Delta ${r.env}: keys accepted`, { description: r.assets?.length ? `Assets: ${r.assets.join(", ")}` : "No funded assets" });
      else toast.error(`Delta ${r.env}: connection failed`, { description: r.error });
    } catch (e) {
      toast.error(message(e));
    } finally {
      setTesting(false);
    }
  };

  const clear = async () => {
    try {
      onCleared(await api<SettingsResponse>("/settings/delta/credentials", "DELETE"));
      toast.success("Delta credentials cleared");
    } catch (e) {
      toast.error(message(e));
    }
  };

  return (
    <div className="space-y-6">
      <Section title="Brokers" description="Paper and Delta are independent: either or both can be active. Keep at least one on.">
        <FieldGrid>
          <SwitchField name="paper_active" label="Paper broker" hint="Simulated fills at the live bid/ask, with the exchange's fees." />
          <SwitchField name="delta_active" label="Delta Exchange" hint="Orders go to your Delta account. Auto-trading on it stays off until you switch it on." />
        </FieldGrid>
      </Section>
      <Section title="Delta account">
        {draft.delta_env === "live" && (
          <Notice tone="loss" className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              <b>Live account: real money.</b> Saving this with Delta active asks for the confirmation phrase. Any change to the account or keys switches Delta
              auto-trading off.
            </span>
          </Notice>
        )}
        <FieldGrid>
          <SelectField
            name="delta_env"
            label="Account"
            options={[
              { value: "demo", label: "Demo (testnet)" },
              { value: "live", label: "Live (real money)" },
            ]}
          />
          <div className="space-y-1.5">
            <Label>Underlyings</Label>
            <Toggles name="underlyings" options={[{ value: "BTC", label: "BTC" }, { value: "ETH", label: "ETH" }, { value: "XAUT", label: "XAUT (gold)" }]} />
            <p className="text-[11.5px] text-muted-foreground">Which option chains are streamed and traded.</p>
          </div>
          <SecretField name="delta_api_key" label="API key" />
          <SecretField name="delta_api_secret" label="API secret" />
        </FieldGrid>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={test} disabled={testing}>
            {testing ? <Loader2 className="animate-spin" /> : <PlugZap />} Test connection
          </Button>
          <Confirm
            title="Clear the Delta API key and secret?"
            description="Delta stops being able to trade or read the account until new keys are saved. Delta auto-trading is switched off."
            confirmLabel="Clear credentials"
            destructive
            onConfirm={clear}
            trigger={(open) => (
              <Button variant="destructive-outline" size="sm" onClick={open}>
                <Trash2 /> Clear credentials
              </Button>
            )}
          />
        </div>
      </Section>
    </div>
  );
}

// -- paper --------------------------------------------------------------------------------

type TestRow = { underlying: string; kind: string; label?: string; status?: string; error?: string; refused?: string };

export function PaperTab() {
  const testAll = async () => {
    const rows = await api<TestRow[]>("/trading/test-all", "POST");
    for (const r of rows) {
      const title = `${r.underlying} ${r.kind}`;
      if (r.status === "open") toast.success(`${title}: opened`, { description: r.label });
      else toast.error(`${title}: not opened`, { description: r.refused || r.error || r.status });
    }
  };
  return (
    <div className="space-y-6">
      <Section title="Paper account" description="Fills at the live bid and ask with the exchange's fees. The starting balance applies to a fresh account (reset it on the Performance page).">
        <FieldGrid>
          <NumberField name="paper_starting_balance" label="Starting balance (USD)" />
          <NumberField name="paper_leverage" label="Perpetual leverage (x)" hint="Paper only: Delta positions use the leverage set on your Delta account." />
        </FieldGrid>
      </Section>
      <Section
        title="Test all positions"
        description="On paper only: for each underlying, one iron condor and one perpetual, at the smallest size and through the normal gates. A slot already taken is reported, not forced."
      >
        <Confirm
          title="Open test positions on paper?"
          description="Up to six paper positions open now and are managed by the guard like any other. Close them from Positions."
          confirmLabel="Open them"
          onConfirm={() => testAll().catch((e) => toast.error(message(e)))}
          trigger={(open) => (
            <Button variant="outline" size="sm" onClick={open}>
              <PlugZap /> Open test positions
            </Button>
          )}
        />
      </Section>
    </div>
  );
}

// -- automation ---------------------------------------------------------------------------

export function AutomationTab() {
  return (
    <div className="space-y-6">
      <Section
        title="Automatic trading"
        description="The master switch per broker. Which strategies run, on which underlyings, and with what numbers is set on the Strategies page."
      >
        <FieldGrid>
          <SwitchField name="auto_paper" label="Automatic entries on paper" />
          <SwitchField
            name="auto_delta"
            label="Automatic entries on Delta"
            hint="Off at every engine start and after any Delta account change. Only strategies switched to live trade there."
          />
        </FieldGrid>
        <Link href="/strategies" className="text-[13px] font-medium text-primary hover:underline">
          Choose strategies
        </Link>
      </Section>
      <Advanced title="Option entries and exits">
        <FieldGrid>
          <NumberField name="max_spread_pct" label="Widest leg bid/ask spread (% of mark)" hint="A leg quoted wider is not traded." />
          <NumberField
            name="exit_before_expiry_minutes"
            label="Close before expiry (minutes)"
            integer
            hint="Debits and hand-built unlimited credits. Spreads and condors are held to settlement."
          />
          <EventTimes />
        </FieldGrid>
      </Advanced>
    </div>
  );
}

function EventTimes() {
  const { draft, set } = useForm();
  const list = (draft.event_times as string[] | undefined) ?? [];
  const [text, setText] = useState(list.join(", "));
  return (
    <div className="space-y-1.5 sm:col-span-2">
      <Label htmlFor="event_times">Event times (UTC)</Label>
      <Input
        id="event_times"
        className="num"
        value={text}
        placeholder="2026-12-09 19:00, 2027-01-13 13:30"
        onChange={(e) => {
          setText(e.target.value);
          set("event_times", e.target.value.split(",").map((t) => t.trim()).filter(Boolean));
        }}
      />
      <p className="text-[11.5px] text-muted-foreground">
        Scheduled releases (FOMC, CPI, …) in UTC as YYYY-MM-DD HH:MM, comma-separated. No credit structure is opened whose life holds one.
      </p>
    </div>
  );
}

// -- risk -------------------------------------------------------------------------------

export function RiskTab() {
  return (
    <div className="space-y-6">
      <Section
        title="Risk"
        description="Each broker on its own. A strategy proven to lose gets no size, one proven to win up to twice this; a drawdown halves or quarters it."
      >
        <FieldGrid>
          <NumberField name="risk_per_trade_pct" label="Risk per automatic entry (% of equity)" hint="Its worst case: the stop, or the wings." />
          <NumberField name="daily_loss_limit_pct" label="Daily loss limit (% of equity)" hint="Closes that broker's positions for the day. 0 is off." />
          <NumberField name="max_drawdown_pct" label="Stop everything at a drawdown of (%)" hint="From the equity peak. Every entry stops until you resume it. 0 is off." />
        </FieldGrid>
      </Section>
      <Advanced title="Limits and the trading day">
        <FieldGrid>
          <NumberField name="max_lots" label="Most lots in one order" integer hint="A typing-error guard, not the sizing rule." />
          <SelectField
            name="day_timezone"
            label="Trading day starts in"
            options={[
              { value: "Asia/Kolkata", label: "Asia/Kolkata (IST)" },
              { value: "UTC", label: "UTC" },
            ]}
          />
        </FieldGrid>
      </Advanced>
    </div>
  );
}

// -- AI -----------------------------------------------------------------------------------

const EXAMPLE_MODELS = [
  "mistral/mistral-small-latest",
  "openai/gpt-4o-mini",
  "anthropic/claude-haiku-4-5",
  "gemini/gemini-2.0-flash",
  "groq/llama-3.3-70b-versatile",
  "ollama/llama3.1",
];

function FallbackModels() {
  const { draft, set } = useForm();
  const list = (draft.ai_fallback_models as string[] | undefined) ?? [];
  const [text, setText] = useState(list.join(", "));
  return (
    <div className="space-y-1.5">
      <Label htmlFor="ai_fallback_models">Fallback models</Label>
      <Input
        id="ai_fallback_models"
        className="num"
        value={text}
        placeholder="openai/gpt-4o-mini, groq/llama-3.3-70b-versatile"
        onChange={(e) => {
          setText(e.target.value);
          set("ai_fallback_models", e.target.value.split(",").map((m) => m.trim()).filter(Boolean));
        }}
      />
      <p className="text-[11.5px] text-muted-foreground">Comma-separated, tried in order when the primary fails. Up to 5.</p>
    </div>
  );
}

export function AiTab() {
  const { generation } = useForm();
  const { data: status, mutate } = useApi<AiStatus>("/ai/status");
  const [testing, setTesting] = useState(false);

  const test = async () => {
    setTesting(true);
    try {
      const r = await api<{ ok: boolean; error?: string; model?: string; ms?: number }>("/ai/test", "POST");
      if (r.ok) toast.success("Model answered", { description: `${r.model} in ${r.ms} ms` });
      else toast.error("No model answered", { description: r.error });
      await mutate();
    } catch (e) {
      toast.error(message(e));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <Section title="Language model" description="Any LiteLLM model id. Switching model is a setting: nothing restarts.">
        <FieldGrid>
          <SwitchField name="ai_enabled" label="Use AI" hint="The market brief. Trades are decided by the rules alone." />
          <TextField name="ai_model" label="Primary model" placeholder="mistral/mistral-small-latest" />
          <FallbackModels key={generation} />
        </FieldGrid>
        <Notice>
          Examples: {EXAMPLE_MODELS.map((m, i) => <span key={m}><code className="num text-[12px]">{m}</code>{i < EXAMPLE_MODELS.length - 1 ? ", " : ""}</span>)}.
          Provider keys (<code className="num text-[12px]">MISTRAL_API_KEY</code>, <code className="num text-[12px]">OPENAI_API_KEY</code>,{" "}
          <code className="num text-[12px]">ANTHROPIC_API_KEY</code>, …) live in the backend <code className="num text-[12px]">.env</code> — never here, never in the database.
        </Notice>
      </Section>
      <Section title="Model readiness" description="Saved settings, checked against the backend's environment.">
        <div className="space-y-2">
          {status?.models.map((m) => (
            <div key={m.model} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-[13px]">
              <span className="num font-medium">{m.model}</span>
              {m.key_present ? (
                <span className="flex items-center gap-1 text-profit"><Check className="size-4" /> key present</span>
              ) : (
                <span className="flex items-center gap-1 text-loss"><X className="size-4" /> missing {m.missing.join(", ") || "key"}</span>
              )}
              {m.last_error && <span className="w-full break-words text-xs text-loss">{m.last_error}</span>}
            </div>
          ))}
          {status?.ready && <p className="text-xs text-muted-foreground">Not ready: {status.ready}</p>}
          <Button variant="outline" size="sm" onClick={test} disabled={testing}>
            {testing ? <Loader2 className="animate-spin" /> : <PlugZap />} Test models
          </Button>
        </div>
      </Section>
    </div>
  );
}

// -- notifications ------------------------------------------------------------------------

export function NotifyTab() {
  const [sending, setSending] = useState(false);
  const send = async () => {
    setSending(true);
    try {
      const r = await api<{ results: Record<string, string>; note: string }>("/settings/notify/test", "POST");
      const entries = Object.entries(r.results);
      if (!entries.length) toast.warning("Nothing sent", { description: r.note || "Save an enabled channel first." });
      for (const [channel, result] of entries) {
        if (result === "ok") toast.success(`${channel}: sent`);
        else toast.error(`${channel}: failed`, { description: result });
      }
    } catch (e) {
      toast.error(message(e));
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="space-y-6">
      <Section title="Telegram">
        <FieldGrid>
          <SwitchField name="notify_telegram" label="Send to Telegram" />
          <SecretField name="telegram_bot_token" label="Bot token" />
          <TextField name="telegram_chat_id" label="Chat id" />
        </FieldGrid>
      </Section>
      <Section title="What is sent" description="Every fill, failure, loss halt, stop-all and exchange mismatch, always. Plus the daily report if it is on.">
        <FieldGrid>
          <SwitchField name="daily_report" label="Daily report" />
          <NumberField name="daily_report_hour" label="Daily report hour" integer hint="In the trading-day timezone." />
        </FieldGrid>
        <Button variant="outline" size="sm" onClick={send} disabled={sending}>
          {sending ? <Loader2 className="animate-spin" /> : <Send />} Send test
        </Button>
        <p className="text-[11.5px] text-muted-foreground">The test uses the saved settings: save first.</p>
      </Section>
      <Advanced title="Email">
        <FieldGrid>
          <SwitchField name="notify_email" label="Send email" />
          <TextField name="email_to" label="To" placeholder="you@example.com, desk@example.com" />
          <TextField name="email_smtp_host" label="SMTP host" placeholder="smtp.gmail.com" />
          <NumberField name="email_smtp_port" label="SMTP port" integer hint="465 uses SSL, anything else STARTTLS." />
          <TextField name="email_smtp_user" label="SMTP user" />
          <SecretField name="email_smtp_pass" label="SMTP password" />
        </FieldGrid>
      </Advanced>
    </div>
  );
}

// -- security -----------------------------------------------------------------------------

export function SecurityTab() {
  const { mutate } = useSWRConfig();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const problem =
    next && !/^\d{6}$/.test(next) ? "the new PIN must be exactly 6 digits" : again && next !== again ? "the two new PINs differ" : "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/auth/pin", "POST", { current, new: next });
      toast.success("PIN changed");
      setCurrent("");
      setNext("");
      setAgain("");
      await mutate("/auth/session");
    } catch (err) {
      toast.error(message(err));
    } finally {
      setBusy(false);
    }
  };

  const [resetPin, setResetPin] = useState("");
  const resetData = async () => {
    try {
      const r = await api<{ deleted: Record<string, number> }>("/system/reset-data", "POST", { pin: resetPin });
      const rows = Object.values(r.deleted).reduce((a, n) => a + n, 0);
      toast.success("All data deleted", { description: `${rows} rows removed; settings kept, paper account back to its starting balance` });
      setResetPin("");
      await mutate(() => true);
    } catch (err) {
      toast.error("Nothing deleted", { description: message(err) });
      throw err;
    }
  };

  const digits = (v: string) => v.replace(/\D/g, "").slice(0, 6);
  return (
    <>
    <Section title="Dashboard PIN" description="Stored as an Argon2 hash. Every page, API route and the live stream need it.">
      <form onSubmit={submit} className="grid max-w-3xl gap-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="pin-current">Current PIN</Label>
          <Input id="pin-current" type="password" inputMode="numeric" autoComplete="current-password" className="num" value={current} onChange={(e) => setCurrent(digits(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pin-new">New PIN (6 digits)</Label>
          <Input id="pin-new" type="password" inputMode="numeric" autoComplete="new-password" className="num" value={next} onChange={(e) => setNext(digits(e.target.value))} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pin-again">New PIN again</Label>
          <Input id="pin-again" type="password" inputMode="numeric" autoComplete="new-password" className="num" value={again} onChange={(e) => setAgain(digits(e.target.value))} />
        </div>
        <div className="flex items-center gap-3 sm:col-span-3">
          <Button type="submit" size="sm" disabled={busy || !current || !next || next !== again || !!problem}>
            {busy && <Loader2 className="animate-spin" />} Change PIN
          </Button>
          {problem && <span className="text-xs text-loss">{problem}</span>}
        </div>
      </form>
    </Section>

    <Section
      title="Reset all data"
      description="Deletes every trade, order, auto decision, signal, AI brief, prediction and event log, and puts the paper account back to its starting balance. Settings, API keys, the PIN and stored market prices are kept. Refused while any position is open."
    >
      <Confirm
        title="Delete all trading data?"
        description="This cannot be undone. Every trade, order, decision, signal and log is deleted; your configuration stays. Enter your login PIN to confirm."
        confirmLabel="Delete everything"
        destructive
        onConfirm={resetData}
        trigger={(open) => (
          <Button variant="destructive-outline" size="sm" onClick={() => { setResetPin(""); open(); }}>
            <Trash2 /> Reset all data
          </Button>
        )}
      >
        <div className="space-y-1.5">
          <Label htmlFor="reset-pin">Login PIN</Label>
          <Input
            id="reset-pin" type="password" inputMode="numeric" autoComplete="current-password" className="num"
            value={resetPin} onChange={(e) => setResetPin(digits(e.target.value))}
          />
        </div>
      </Confirm>
    </Section>
    </>
  );
}
