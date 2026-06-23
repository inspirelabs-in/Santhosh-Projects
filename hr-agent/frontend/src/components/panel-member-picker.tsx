/* PanelMemberPicker — commented out per instructions
"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { Check, Plus, X } from "lucide-react";
import Link from "next/link";

import { Input } from "@/components/ui/input";
import { panels, type PanelMember, type PanelRoleType } from "@/lib/api/panels";

export function PanelMemberPicker({
  roleType,
  value,
  onChange,
  placeholder = "Pick panel members or paste an email",
}: {
  roleType: PanelRoleType;
  value: string[];
  onChange: (emails: string[]) => void;
  placeholder?: string;
}) {
  const { data, isLoading } = useSWR<PanelMember[]>(
    `/dashboard/panels?role_type=${roleType}`,
    () => panels.list({ role_type: roleType }),
    { refreshInterval: 60_000 },
  );
  const [text, setText] = useState("");

  const directory = data ?? [];
  const directoryByEmail = useMemo(() => {
    const m = new Map<string, PanelMember>();
    for (const d of directory) m.set(d.email.toLowerCase(), d);
    return m;
  }, [directory]);

  function add(email: string) {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !trimmed.includes("@")) return;
    if (value.map((v) => v.toLowerCase()).includes(trimmed)) return;
    onChange([...value, trimmed]);
  }

  function remove(email: string) {
    onChange(value.filter((v) => v.toLowerCase() !== email.toLowerCase()));
  }

  function commitFree() {
    const parts = text
      .split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    for (const p of parts) add(p);
    setText("");
  }

  const valueSet = new Set(value.map((v) => v.toLowerCase()));
  const suggestions = directory.filter(
    (d) => !valueSet.has(d.email.toLowerCase()),
  );

  return (
    <div className="space-y-2">
      {value.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {value.map((email) => {
            const member = directoryByEmail.get(email.toLowerCase());
            return (
              <span
                key={email}
                className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px]"
              >
                {member ? (
                  <span className="font-semibold">{member.name}</span>
                ) : null}
                <span className="font-mono text-muted-foreground">{email}</span>
                <button
                  type="button"
                  onClick={() => remove(email)}
                  className="rounded-full p-0.5 hover:bg-destructive/10 hover:text-destructive"
                  aria-label={`Remove ${email}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            );
          })}
        </div>
      ) : null}

      <div className="flex gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commitFree();
            }
          }}
          placeholder={placeholder}
          className="flex-1"
        />
        <button
          type="button"
          onClick={commitFree}
          disabled={!text.includes("@")}
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      {isLoading ? (
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          loading directory...
        </p>
      ) : null}

      {!isLoading && directory.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No saved {roleType} panel members yet.{" "}
          <Link
            href="/settings/panels"
            className="underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground"
          >
            Add some →
          </Link>
        </p>
      ) : null}

      {!isLoading && suggestions.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => add(m.email)}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2 py-0.5 text-[11px] hover:border-primary/50 hover:bg-muted"
              title={m.email}
            >
              <Plus className="h-3 w-3 text-muted-foreground" />
              <span className="font-semibold">{m.name}</span>
              <span className="font-mono text-muted-foreground">
                {m.email.split("@")[0]}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
*/
