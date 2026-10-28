"use client";

// One component per Settings tab. Fields are bound to the draft through `useForm`; actions that
// talk to the backend directly (connection tests, clearing keys, changing the PIN) live here too.

import { useState } from "react";
import { useSWRConfig } from "swr";
import { AlertTriangle, Check, Loader2, PlugZap, Send, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { FieldGrid, NumberField, SecretField, Section, SelectField, SwitchField, TextField, useForm } from "@/components/settings/fields";
import { Confirm } from "@/components/shared/confirm";
import { Notice } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useApi } from "@/lib/live";
import type { AiStatus, SettingsResponse, StrategySpec } from "@/lib/types";
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
          <SelectField
            name="market_data"
            label="Market data while Delta is inactive"
            hint="Live public data needs no keys."
            options={[
              { value: "live", label: "Live exchange" },
              { value: "demo", label: "Testnet" },
            ]}
          />
          <div className="space-y-1.5">
            <Label>Underlyings</Label>
            <Toggles name="underlyings" options={[{ value: "BTC", label: "BTC" }, { value: "ETH", label: "ETH" }]} />
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

export function PaperTab() {
  return (
    <Section title="Paper account" description="The starting balance applies to a fresh paper account (reset it on the Performance page).">
      <FieldGrid>
        <NumberField name="paper_starting_balance" label="Starting balance (USD)" />
        <NumberField name="paper_slippage_pct" label="Slippage when a side is empty (%)" hint="Applied to the mark only when the leg has no bid or ask." />
      </FieldGrid>
    </Section>
  );
}

// -- auto-trader --------------------------------------------------------------------------

const VIEW_TONE: Record<StrategySpec["view"], "default" | "profit" | "loss" | "primary"> = {
  neutral: "default",
  bullish: "profit",
  bearish: "loss",
  volatile: "primary",
};

export function AutoTab({ strategies }: { strategies: StrategySpec[] }) {
  const { draft, set } = useForm();
  const enabled = (draft.auto_strategies as string[] | undefined) ?? [];
  return (
    <div className="space-y-6">
      <Section title="Auto-trading" description="One decision per underlying every interval; each one is recorded on the Auto Trader page, skips included.">
        <FieldGrid>
          <SwitchField name="auto_paper" label="Auto-trade on paper" />
          <SwitchField name="auto_delta" label="Auto-trade on Delta" hint="Switched off at every engine start and on any Delta account change." />
          <NumberField name="auto_interval_minutes" label="Decision interval (minutes)" integer />
        </FieldGrid>
      </Section>
      <Section title="Strategies" description="Only the checked structures are considered. Defined-risk ones have a known worst case before entry.">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {strategies.map((s) => {
            const on = enabled.includes(s.key);
            return (
              <label
                key={s.key}
                className={cn("flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors", on ? "border-primary bg-accent/40" : "hover:bg-muted/50")}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-[var(--primary)]"
                  checked={on}
                  onChange={() => set("auto_strategies", on ? enabled.filter((k) => k !== s.key) : [...enabled, s.key])}
                />
                <span className="min-w-0 space-y-1">
                  <span className="flex flex-wrap items-center gap-1.5 text-[13px] font-semibold">
                    {s.label}
                    <Badge variant={VIEW_TONE[s.view]}>{s.view}</Badge>
                    <Badge variant="outline">{s.premium}</Badge>
                    {s.risk === "undefined" ? <Badge variant="warn">unlimited risk</Badge> : <Badge variant="default">defined risk</Badge>}
                  </span>
                  <span className="block text-xs leading-snug text-muted-foreground">{s.summary}</span>
                </span>
              </label>
            );
          })}
        </div>
      </Section>
      <Section title="Expiry and strikes">
        <FieldGrid>
          <NumberField name="expiry_min_hours" label="Nearest expiry at least (hours)" />
          <NumberField name="expiry_max_hours" label="…and at most (hours)" />
          <NumberField name="short_delta" label="Sold strike |delta|" step={0.01} />
          <NumberField name="wing_width_pct" label="Wing distance (% of spot)" />
        </FieldGrid>
      </Section>
      <Section title="Entry filters" description="A candidate must pass every one of these to be traded automatically.">
        <FieldGrid>
          <NumberField name="min_score" label="Minimum regime score" integer />
          <NumberField name="min_pop_pct" label="Minimum probability of profit (%)" />
          <NumberField name="min_credit_width_pct" label="Minimum credit / width (%)" />
          <NumberField name="max_spread_pct" label="Maximum leg bid/ask spread (%)" />
        </FieldGrid>
      </Section>
      <Section
        title="Pacing"
        description="Towards about one entry a day. While nothing has opened on an underlying today the regime score above eases towards this floor as the day runs out, and the first entry puts it back. 0 switches it off. The filters above never ease: a thin premium is refused at any hour."
      >
        <FieldGrid>
          <NumberField name="min_score_floor" label="Regime score floor (0 = off)" integer />
        </FieldGrid>
      </Section>
    </div>
  );
}

// -- risk and exits -----------------------------------------------------------------------

export function RiskTab() {
  return (
    <div className="space-y-6">
      <Section title="Sizing and limits" description="Applied to every entry, manual or automatic, on each broker separately.">
        <FieldGrid>
          <NumberField name="risk_per_trade_pct" label="Worst case per structure (% of equity)" hint="Auto-trader lot sizing." />
          <NumberField name="max_lots" label="Maximum lots per structure" integer />
          <NumberField name="max_open_structures" label="Maximum open structures" integer />
          <NumberField name="max_per_underlying" label="Maximum per underlying" integer />
          <NumberField name="daily_loss_limit_pct" label="Daily loss limit (% of equity)" hint="0 switches it off." />
          <SelectField
            name="day_timezone"
            label="Trading day starts in"
            options={[
              { value: "Asia/Kolkata", label: "Asia/Kolkata (IST)" },
              { value: "UTC", label: "UTC" },
            ]}
          />
        </FieldGrid>
      </Section>
      <Section title="Exits" description="The guard checks every open structure every few seconds, reading what closing would actually book.">
        <FieldGrid>
          <NumberField name="credit_take_profit_pct" label="Credit: take profit (% of max profit)" />
          <NumberField name="credit_stop_loss_pct" label="Credit: stop loss (% of credit)" />
          <NumberField name="debit_take_profit_pct" label="Debit: take profit (% of debit)" />
          <NumberField name="debit_stop_loss_pct" label="Debit: stop loss (% of debit)" />
          <NumberField name="exit_before_expiry_minutes" label="Close before expiry (minutes)" integer hint="0 holds to settlement." />
        </FieldGrid>
      </Section>
    </div>
  );
}

// -- zero hero ----------------------------------------------------------------------------

export function ZeroHeroTab() {
  const { draft } = useForm();
  const shadow = draft.zh_shadow !== false;
  const budget = Number(draft.zh_daily_budget_pct ?? 20);
  const attempts = Number(draft.zh_max_attempts_day ?? 3);
  return (
    <div className="space-y-6">
      <Notice tone={shadow ? "info" : "warn"}>
        {shadow ? (
          <>
            <strong>Shadow mode is on.</strong> Every scan is recorded with the contract it chose and the size it would have
            bought, and no order is sent. Leave it on until the Zero-Hero page shows enough closed scans to tell you what the
            hit rate actually is — the sizing arithmetic below is only as good as that number.
          </>
        ) : (
          <>
            <strong>Shadow mode is off: this will send real orders</strong> on any broker whose auto-trading is on. A long OTM
            buy in the last hours before expiry loses its whole premium most of the time. That is the design, not a fault.
          </>
        )}
      </Notice>
      <Section
        title="Zero-hero"
        description="A long OTM option bought on an expiry-day momentum break. It is the opposite trade to everything on the Auto Options page and is gated separately, because a premium seller's filters would refuse it every time — correctly, and uselessly."
      >
        <FieldGrid>
          <SwitchField name="zh_enabled" label="Run the expiry-day scan" />
          <SwitchField name="zh_shadow" label="Shadow mode (scan and record, never trade)" />
          <NumberField name="zh_window_start_hours" label="Window opens (hours before expiry)" />
          <NumberField name="zh_window_end_hours" label="Window closes (hours before expiry)" hint="Must leave room before the time stop." />
          <NumberField name="zh_scan_seconds" label="Scan every (seconds)" integer hint="A break is a one-bar event; a slow scan finds the same entry after it has run." />
        </FieldGrid>
      </Section>
      <Section
        title="Sizing"
        description={`The budget is for the whole DAY and is split across the attempts the day has left — not per trade. At ${budget}% over ${attempts} attempts that is about ${(budget / attempts).toFixed(1)}% of equity each.`}
      >
        <FieldGrid>
          <NumberField name="zh_daily_budget_pct" label="Daily budget (% of equity)" />
          <NumberField name="zh_max_attempts_day" label="Maximum attempts per day" integer />
          <NumberField name="zh_max_lots" label="Maximum lots per attempt" integer hint="A backstop, not the sizing rule: one lot of an 8-dollar BTC option is 0.008 dollars of premium." />
          <NumberField name="zh_capacity_share_pct" label="Share of a strike's traded size (%)" hint="What actually limits size. A proxy from OI and volume — Delta's ticker carries no book depth." />
          <NumberField name="zh_min_oi" label="Minimum open interest (underlying)" />
        </FieldGrid>
      </Section>
      <Section title="Entry filters" description="A contract must pass every one of these, and the market must pass the scan's own gates on top.">
        <FieldGrid>
          <NumberField name="zh_min_multiple" label="Minimum payoff at the target (x)" hint="Repriced with the clock advanced, not just the spot." />
          <NumberField name="zh_min_ev" label="Minimum expected value (per unit paid)" />
          <NumberField name="zh_min_distance_pct" label="Minimum distance OTM (%)" />
          <NumberField name="zh_max_distance_pct" label="Maximum distance OTM (%)" />
          <NumberField name="zh_max_spread_pct" label="Maximum bid/ask spread (%)" hint="An 8/10 quote is a 20% loss on contact." />
          <NumberField name="zh_displacement_atr" label="Minimum displacement (x mean range)" />
          <NumberField name="zh_max_pain_buffer_pct" label="Past max pain by (% of spot)" hint="A trade running into the pin is refused outright." />
        </FieldGrid>
      </Section>
      <Section title="Exits" description="Whichever time stop comes first applies — this one or the global 'close before expiry'. A broken premise closes the position before either.">
        <FieldGrid>
          <NumberField name="zh_take_profit_pct" label="Take profit (% of the debit)" hint="300% means the contract at 4x." />
          <NumberField name="zh_stop_loss_pct" label="Stop loss (% of the debit)" />
          <NumberField name="zh_time_stop_minutes" label="Time stop (minutes before expiry)" integer />
        </FieldGrid>
      </Section>
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
          <SwitchField name="ai_enabled" label="Use AI" hint="The market brief and, if enabled below, the trade review." />
          <TextField name="ai_model" label="Primary model" placeholder="mistral/mistral-small-latest" />
          <FallbackModels key={generation} />
          <SwitchField name="ai_review_trades" label="AI reviews auto trades" hint="The model may approve one of the rules' candidates or skip; it cannot invent one." />
          <SwitchField name="ai_required" label="AI review required" hint="On: no answer from any model means no trade. Off: the rules decide." />
          <NumberField name="ai_brief_minutes" label="Market brief every (minutes)" integer />
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
      <Section title="Email">
        <FieldGrid>
          <SwitchField name="notify_email" label="Send email" />
          <TextField name="email_to" label="To" placeholder="you@example.com, desk@example.com" />
          <TextField name="email_smtp_host" label="SMTP host" placeholder="smtp.gmail.com" />
          <NumberField name="email_smtp_port" label="SMTP port" integer hint="465 uses SSL, anything else STARTTLS." />
          <TextField name="email_smtp_user" label="SMTP user" />
          <SecretField name="email_smtp_pass" label="SMTP password" />
        </FieldGrid>
      </Section>
      <Section title="What to send">
        <FieldGrid>
          <SwitchField name="notify_trades" label="Trades" hint="Opened, closed, failed, needs attention." />
          <SwitchField name="notify_decisions" label="Every auto decision" hint="Skips included: can be noisy." />
          <SwitchField name="notify_risk" label="Risk halts" />
          <SwitchField name="daily_report" label="Daily report" />
          <NumberField name="daily_report_hour" label="Daily report hour" integer hint="In the trading-day timezone." />
        </FieldGrid>
        <Button variant="outline" size="sm" onClick={send} disabled={sending}>
          {sending ? <Loader2 className="animate-spin" /> : <Send />} Send test
        </Button>
        <p className="text-[11.5px] text-muted-foreground">The test uses the saved settings: save first.</p>
      </Section>
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

  const digits = (v: string) => v.replace(/\D/g, "").slice(0, 6);
  return (
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
  );
}
