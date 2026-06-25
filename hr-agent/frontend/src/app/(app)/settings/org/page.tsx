"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import {
  ArrowLeft,
  Loader2,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { Topbar } from "@/components/layout/topbar";
import { api, swrFetcher } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { Org } from "@/lib/types";

interface PersonaValue {
  name: string;
  description?: string;
}

interface HiringPersona {
  company_name?: string;
  mission?: string;
  domain_context?: string;
  values: PersonaValue[];
  what_good_looks_like: string[];
  anti_patterns: string[];
  hiring_philosophy?: string;
  tone?: string;
  version?: number;
}

function PersonaValueRow({
  val,
  onChange,
  onRemove,
}: {
  val: PersonaValue;
  onChange: (v: PersonaValue) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="flex-1 space-y-1">
        <Input
          value={val.name}
          onChange={(e) => onChange({ ...val, name: e.target.value })}
          placeholder="Value name"
          className="h-8 text-xs"
        />
        <Input
          value={val.description ?? ""}
          onChange={(e) => onChange({ ...val, description: e.target.value || undefined })}
          placeholder="Description (optional)"
          className="h-8 text-xs"
        />
      </div>
      <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={onRemove}>
        <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
      </Button>
    </div>
  );
}

function StringListEditor({
  items,
  onChange,
  placeholder,
}: {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
}) {
  return (
    <div className="space-y-1.5">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={item}
            onChange={(e) => {
              const next = [...items];
              next[i] = e.target.value;
              onChange(next);
            }}
            placeholder={placeholder}
            className="h-8 text-xs"
          />
          <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            <X className="h-3.5 w-3.5 text-muted-foreground" />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => onChange([...items, ""])}>
        <Plus className="mr-1 h-3 w-3" /> Add
      </Button>
    </div>
  );
}

function FieldBlock({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label className="text-xs font-medium text-muted-foreground">
        {label}
        {hint && <span className="ml-1.5 font-mono text-[10px] text-muted-foreground/60">({hint})</span>}
      </Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

export default function OrgSettingsPage() {
  const { data: org, isLoading, mutate } = useSWR<Org>("/dashboard/settings/org", swrFetcher);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const [name, setName] = useState("");
  const [persona, setPersona] = useState<HiringPersona>({
    values: [],
    what_good_looks_like: [],
    anti_patterns: [],
  });

  useEffect(() => {
    if (!org) return;
    setName(org.name);
    setPersona({
      company_name: "",
      mission: "",
      domain_context: "",
      values: [],
      what_good_looks_like: [],
      anti_patterns: [],
      ...(org.hiring_persona as Partial<HiringPersona>),
    });
  }, [org]);

  const update = (patch: Partial<HiringPersona>) => {
    setPersona((p) => ({ ...p, ...patch }));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/dashboard/settings/org", {
        name,
        hiring_persona: persona,
      });
      setDirty(false);
      await mutate();
      setMsg({ type: "ok", text: "Org details saved" });
      setTimeout(() => setMsg(null), 3000);
    } catch (e: any) {
      setMsg({ type: "err", text: e?.message ?? "Save failed" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Topbar title="Organization Settings" subtitle="hiring persona · mission · culture" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-8 py-6 pb-24 space-y-6">
          {/* Back link */}
          <Link
            href="/settings"
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to settings
          </Link>

          {isLoading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
              ))}
            </div>
          ) : !org ? (
            <p className="text-sm text-muted-foreground italic">Could not load org data</p>
          ) : (
            <>
              {msg && (
                <div
                  className={cn(
                    "rounded-md border px-3 py-2 text-xs",
                    msg.type === "ok"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                      : "border-red-200 bg-red-50 text-red-800",
                  )}
                >
                  {msg.text}
                </div>
              )}

              {/* Company name */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">General</h3>
                <FieldBlock label="Company name">
                  <Input value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} className="h-9 text-sm" />
                </FieldBlock>
                <FieldBlock label="Slug" hint="read-only">
                  <Input value={org.slug} disabled className="h-9 text-sm text-muted-foreground" />
                </FieldBlock>
              </div>

              {/* Mission & context */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">Mission & Context</h3>
                <FieldBlock label="Mission">
                  <textarea
                    value={persona.mission ?? ""}
                    onChange={(e) => update({ mission: e.target.value })}
                    className="w-full rounded border border-border bg-background px-3 py-2 text-xs resize-none"
                    rows={3}
                    placeholder="What drives the company?"
                  />
                </FieldBlock>
                <FieldBlock label="Domain context" hint="industry, niche, space">
                  <textarea
                    value={persona.domain_context ?? ""}
                    onChange={(e) => update({ domain_context: e.target.value })}
                    className="w-full rounded border border-border bg-background px-3 py-2 text-xs resize-none"
                    rows={3}
                    placeholder="e.g. B2B SaaS in construction-tech"
                  />
                </FieldBlock>
              </div>

              {/* Values */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">Values</h3>
                <div className="space-y-2">
                  {persona.values.map((v, i) => (
                    <PersonaValueRow
                      key={i}
                      val={v}
                      onChange={(nv) => {
                        const next = [...persona.values];
                        next[i] = nv;
                        update({ values: next });
                      }}
                      onRemove={() => update({ values: persona.values.filter((_, j) => j !== i) })}
                    />
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => update({ values: [...persona.values, { name: "" }] })}
                  >
                    <Plus className="mr-1 h-3 w-3" /> Add value
                  </Button>
                </div>
              </div>

              {/* What good looks like */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">What Good Looks Like</h3>
                <StringListEditor
                  items={persona.what_good_looks_like}
                  onChange={(items) => update({ what_good_looks_like: items })}
                  placeholder="e.g. Ships features end-to-end"
                />
              </div>

              {/* Anti-patterns */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">Anti-patterns</h3>
                <StringListEditor
                  items={persona.anti_patterns}
                  onChange={(items) => update({ anti_patterns: items })}
                  placeholder="e.g. Cowboy coding without tests"
                />
              </div>

              {/* Philosophy & tone */}
              <div className="rounded-lg border bg-card p-5 space-y-4">
                <h3 className="font-display text-sm font-medium">Philosophy & Tone</h3>
                <FieldBlock label="Hiring philosophy">
                  <textarea
                    value={persona.hiring_philosophy ?? ""}
                    onChange={(e) => update({ hiring_philosophy: e.target.value })}
                    className="w-full rounded border border-border bg-background px-3 py-2 text-xs resize-none"
                    rows={3}
                    placeholder="What's the company's approach to hiring?"
                  />
                </FieldBlock>
                <FieldBlock label="Tone" hint="voice & personality">
                  <textarea
                    value={persona.tone ?? ""}
                    onChange={(e) => update({ tone: e.target.value })}
                    className="w-full rounded border border-border bg-background px-3 py-2 text-xs resize-none"
                    rows={2}
                    placeholder="e.g. direct, supportive, no-nonsense"
                  />
                </FieldBlock>
              </div>

              {/* Save */}
              <div className="flex items-center justify-end gap-3">
                <span className="text-xs text-muted-foreground">
                  {dirty ? "Unsaved changes" : "All saved"}
                </span>
                <Button onClick={save} disabled={saving || !dirty}>
                  {saving ? (
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="mr-1.5 h-4 w-4" />
                  )}
                  Save
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
