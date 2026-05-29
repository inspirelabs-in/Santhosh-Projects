"use client";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SecretInput } from "./SecretInput";
import type { ConfigFieldDef } from "@/lib/configClient";

interface Props {
  field: ConfigFieldDef;
  value: unknown;
  configured: boolean;
  dirty: boolean;
  disabled?: boolean;
  onChange: (v: unknown) => void;
}

export function ConfigField({ field, value, configured, dirty, disabled, onChange }: Props) {
  const labelEl = (
    <div className="flex items-baseline justify-between">
      <Label className="font-mono text-[12px]">
        {field.label}
        {dirty && (
          <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full bg-warning align-middle" />
        )}
        {!field.hot_reload && (
          <span className="ml-2 font-mono text-[10px] text-warning">requires restart</span>
        )}
      </Label>
      <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {field.key}
      </span>
    </div>
  );

  let input: React.ReactNode;
  switch (field.type) {
    case "secret":
      input = (
        <SecretInput
          value={(value as string) ?? ""}
          configured={configured && !dirty}
          onChange={onChange}
          disabled={disabled}
        />
      );
      break;
    case "bool":
      input = (
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
            disabled={disabled}
            className="h-4 w-4"
          />
          <span className="font-mono text-xs text-muted-foreground">
            {value ? "enabled" : "disabled"}
          </span>
        </label>
      );
      break;
    case "int":
    case "float":
      input = (
        <Input
          type="number"
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(null);
              return;
            }
            const n = field.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            onChange(Number.isNaN(n) ? null : n);
          }}
          step={field.type === "int" ? 1 : "any"}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          disabled={disabled}
          className="font-mono"
        />
      );
      break;
    case "select":
      input = (
        <Select
          value={(value as string) ?? ""}
          onValueChange={onChange}
          disabled={disabled}
        >
          <SelectTrigger className="font-mono">
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {(field.options ?? []).map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
      break;
    case "json":
    case "textarea":
      input = (
        <Textarea
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          rows={6}
          className="font-mono text-xs"
          placeholder={field.help.startsWith("[") ? field.help : ""}
        />
      );
      break;
    case "email":
    case "url":
    case "string":
    default:
      input = (
        <Input
          type={field.type === "email" ? "email" : field.type === "url" ? "url" : "text"}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="font-mono"
          autoComplete="off"
          spellCheck={false}
        />
      );
  }

  return (
    <div className="space-y-1.5">
      {labelEl}
      {input}
      {field.help && (
        <p className="font-mono text-[11px] text-muted-foreground">{field.help}</p>
      )}
    </div>
  );
}
