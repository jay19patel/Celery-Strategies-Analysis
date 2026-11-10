"use client";

// A strategy's numbers, drawn from its own Pydantic schema (GET /strategies). A new strategy file
// brings its form with it: nothing here names a strategy or a field.

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { ParamSchema } from "@/lib/types";

type Value = number | boolean | string;

/** "short_delta" -> "Short delta": the schema's title when it has one. */
function label(name: string, title?: string): string {
  return title ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function ParamsForm({
  schema,
  values,
  onChange,
  disabled,
  idPrefix,
}: {
  schema: ParamSchema;
  values: Record<string, Value>;
  onChange: (name: string, value: Value) => void;
  disabled?: boolean;
  idPrefix: string;
}) {
  const fields = Object.entries(schema.properties ?? {});
  if (!fields.length) return <p className="text-[12.5px] text-muted-foreground">This strategy has no settings of its own.</p>;
  return (
    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
      {fields.map(([name, prop]) => {
        const id = `${idPrefix}-${name}`;
        if (prop.type === "boolean") {
          return (
            <div key={name} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
              <Label htmlFor={id}>{label(name, prop.title)}</Label>
              <Switch id={id} checked={Boolean(values[name])} disabled={disabled} onCheckedChange={(v) => onChange(name, v)} />
            </div>
          );
        }
        return (
          <NumberParam
            key={`${name}:${String(values[name])}`}
            id={id}
            label={label(name, prop.title)}
            hint={prop.description}
            value={Number(values[name] ?? prop.default)}
            min={prop.minimum}
            max={prop.maximum}
            integer={prop.type === "integer"}
            disabled={disabled}
            onCommit={(v) => onChange(name, v)}
          />
        );
      })}
    </div>
  );
}

function NumberParam({
  id, label: text, hint, value, min, max, integer, disabled, onCommit,
}: {
  id: string; label: string; hint?: string; value: number; min?: number; max?: number; integer: boolean; disabled?: boolean;
  onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const n = Number(draft);
  const problem =
    draft.trim() === "" || Number.isNaN(n) ? "a number is required"
      : integer && !Number.isInteger(n) ? "a whole number is required"
        : min !== undefined && n < min ? `at least ${min}`
          : max !== undefined && n > max ? `at most ${max}` : "";
  const commit = () => {
    if (!problem && n !== value) onCommit(n);
  };
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{text}</Label>
      <Input
        id={id}
        inputMode="decimal"
        className="num"
        value={draft}
        disabled={disabled}
        aria-invalid={!!problem}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      <p className="text-[11.5px] leading-snug text-muted-foreground">
        {problem ? <span className="text-loss">{problem}</span> : <span className="num">{min ?? "…"} – {max ?? "…"}</span>}
        {hint && <span> · {hint}</span>}
      </p>
    </div>
  );
}
