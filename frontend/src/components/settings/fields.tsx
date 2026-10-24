"use client";

// Form fields bound to the Settings draft. Each one reads and writes the draft through context, so a
// tab is a list of field names and labels, and the page alone decides what is saved.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

export interface FormState {
  draft: Record<string, unknown>;
  stored: Record<string, unknown>;
  limits: Record<string, { minimum?: number; maximum?: number }>;
  set: (name: string, value: unknown) => void;
  secrets: Record<string, string>;
  setSecret: (name: string, value: string) => void;
  setInvalid: (name: string, invalid: boolean) => void;
  generation: number; // bumps on reset, so locally edited inputs start over
}

export const FormContext = createContext<FormState | null>(null);

export function useForm(): FormState {
  const form = useContext(FormContext);
  if (!form) throw new Error("settings field used outside the settings form");
  return form;
}

function Field({ name, label, hint, children, className }: { name: string; label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={name}>{label}</Label>
      {children}
      {hint && <p className="text-[11.5px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function FieldGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid gap-x-5 gap-y-4 sm:grid-cols-2 xl:grid-cols-3", className)}>{children}</div>;
}

export function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-[13px] font-semibold">{title}</div>
        {description && <div className="text-xs text-muted-foreground">{description}</div>}
      </div>
      {children}
    </div>
  );
}

interface NumberProps {
  name: string;
  label: ReactNode;
  hint?: ReactNode;
  integer?: boolean;
  step?: number;
}

export function NumberField(props: NumberProps) {
  // A reset (save or discard) remounts the box, so it shows the stored value again.
  return <NumberInput key={useForm().generation} {...props} />;
}

function NumberInput({ name, label, hint, integer = false, step }: NumberProps) {
  const { draft, set, limits, setInvalid } = useForm();
  const value = draft[name] as number | undefined;
  const [text, setText] = useState(value === undefined ? "" : String(value));
  const { minimum, maximum } = limits[name] ?? {};

  const parsed = Number(text);
  let problem = "";
  if (text.trim() === "" || Number.isNaN(parsed)) problem = "a number is required";
  else if (integer && !Number.isInteger(parsed)) problem = "a whole number is required";
  else if (minimum !== undefined && parsed < minimum) problem = `at least ${minimum}`;
  else if (maximum !== undefined && parsed > maximum) problem = `at most ${maximum}`;

  useEffect(() => {
    setInvalid(name, !!problem);
    return () => setInvalid(name, false);
  }, [name, problem, setInvalid]);

  const range = minimum !== undefined || maximum !== undefined ? `${minimum ?? "…"} – ${maximum ?? "…"}` : "";
  return (
    <Field
      name={name}
      label={label}
      hint={
        <>
          {problem ? <span className="text-loss">{problem}</span> : range && <span className="num">{range}</span>}
          {hint && <span>{problem || range ? " · " : ""}{hint}</span>}
        </>
      }
    >
      <Input
        id={name}
        inputMode="decimal"
        className="num"
        value={text}
        aria-invalid={!!problem}
        step={step}
        onChange={(e) => {
          setText(e.target.value);
          const n = Number(e.target.value);
          if (e.target.value.trim() !== "" && !Number.isNaN(n)) set(name, n);
        }}
      />
    </Field>
  );
}

export function TextField({ name, label, hint, placeholder }: { name: string; label: ReactNode; hint?: ReactNode; placeholder?: string }) {
  const { draft, set } = useForm();
  return (
    <Field name={name} label={label} hint={hint}>
      <Input id={name} value={String(draft[name] ?? "")} placeholder={placeholder} onChange={(e) => set(name, e.target.value)} />
    </Field>
  );
}

/** A secret is never shown: the box starts empty, the stored state is the placeholder, and blank keeps it. */
export function SecretField({ name, label, hint }: { name: string; label: ReactNode; hint?: ReactNode }) {
  const { stored, secrets, setSecret } = useForm();
  const current = String(stored[name] ?? "");
  return (
    <Field name={name} label={label} hint={hint ?? (current ? "Stored. Leave blank to keep it." : "Not set.")}>
      <Input
        id={name}
        type="password"
        autoComplete="new-password"
        value={secrets[name] ?? ""}
        placeholder={current === "set" ? "••••••••" : current || "not set"}
        onChange={(e) => setSecret(name, e.target.value)}
      />
    </Field>
  );
}

export function SwitchField({ name, label, hint, disabled }: { name: string; label: ReactNode; hint?: ReactNode; disabled?: boolean }) {
  const { draft, set } = useForm();
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border px-3 py-2.5">
      <div className="min-w-0">
        <Label htmlFor={name}>{label}</Label>
        {hint && <p className="mt-1 text-[11.5px] leading-snug text-muted-foreground">{hint}</p>}
      </div>
      <Switch id={name} checked={!!draft[name]} disabled={disabled} onCheckedChange={(v) => set(name, v)} />
    </div>
  );
}

export function SelectField({ name, label, hint, options }: { name: string; label: ReactNode; hint?: ReactNode; options: { value: string; label: string }[] }) {
  const { draft, set } = useForm();
  return (
    <Field name={name} label={label} hint={hint}>
      <Select value={String(draft[name] ?? "")} onValueChange={(v) => set(name, v)}>
        <SelectTrigger id={name} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}
