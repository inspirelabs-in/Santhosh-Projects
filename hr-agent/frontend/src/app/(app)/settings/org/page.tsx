"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { ArrowLeft, Check, Loader2, Plus, Save, Trash2, X } from "lucide-react";
import Link from "next/link";
import { Topbar } from "@/components/layout/topbar";
import { api, swrFetcher } from "@/lib/api";
import { Button } from "@/components/ui/button";
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

/* ------------------------------------------------------------------ */
/*  Inline field primitives — editable text that lights up on focus,   */
/*  no heavy borders / boxes.                                          */
/* ------------------------------------------------------------------ */

const inlineArea =
  "w-full resize-none rounded-md bg-transparent px-3 py-2.5 text-sm leading-relaxed transition hover:bg-muted/40 focus:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 placeholder:text-muted-foreground/40";

function Field({
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
      <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        {label}
        {hint && (
          <span className="ml-1.5 font-mono text-[10px] normal-case tracking-normal text-muted-foreground/50">
            {hint}
          </span>
        )}
      </span>
      <div className="mt-2">{children}</div>
    </div>
  );
}

/* Numbered, borderless value row — name + description on hairline dividers. */
function ValueRow({
  index,
  val,
  onChange,
  onRemove,
}: {
  index: number;
  val: PersonaValue;
  onChange: (v: PersonaValue) => void;
  onRemove: () => void;
}) {
  return (
    <div className="group flex items-start gap-4 border-b border-border/40 py-3 last:border-0">
      <span className="mt-1 w-6 shrink-0 font-mono text-xs tabular-nums text-muted-foreground/50">
        {String(index + 1).padStart(2, "0")}
      </span>
      <div className="min-w-0 flex-1">
        <input
          value={val.name}
          onChange={(e) => onChange({ ...val, name: e.target.value })}
          placeholder="Value name"
          className="w-full bg-transparent text-sm font-medium focus:outline-none placeholder:font-normal placeholder:text-muted-foreground/40"
        />
        <input
          value={val.description ?? ""}
          onChange={(e) => onChange({ ...val, description: e.target.value || undefined })}
          placeholder="Short description (optional)"
          className="mt-0.5 w-full bg-transparent text-xs text-muted-foreground focus:outline-none placeholder:text-muted-foreground/40"
        />
      </div>
      <button
        type="button"
        onClick={onRemove}
        className="mt-1 shrink-0 opacity-0 transition group-hover:opacity-100"
        aria-label="Remove value"
      >
        <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
      </button>
    </div>
  );
}

/* One side of the signals ledger — ✓ good or ✗ avoid. */
function SignalColumn({
  tone,
  title,
  items,
  onChange,
  placeholder,
}: {
  tone: "good" | "bad";
  title: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
}) {
  const Marker = tone === "good" ? Check : X;
  const color = tone === "good" ? "text-emerald-600" : "text-red-500";
  const dot = tone === "good" ? "bg-emerald-500" : "bg-red-400";
  return (
    <div>
      <p className={cn("mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em]", color)}>
        <Marker className="h-3.5 w-3.5" /> {title}
      </p>
      <div>
        {items.map((item, i) => (
          <div
            key={i}
            className="group flex items-center gap-2.5 border-b border-border/40 py-1.5 last:border-0"
          >
            <span className={cn("h-1 w-1 shrink-0 rounded-full", dot)} />
            <input
              value={item}
              onChange={(e) => {
                const next = [...items];
                next[i] = e.target.value;
                onChange(next);
              }}
              placeholder={placeholder}
              className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/40"
            />
            <button
              type="button"
              onClick={() => onChange(items.filter((_, j) => j !== i))}
              className="shrink-0 opacity-0 transition group-hover:opacity-100"
              aria-label="Remove"
            >
              <X className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange([...items, ""])}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          <Plus className="h-3 w-3" /> Add
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sections                                                           */
/* ------------------------------------------------------------------ */

const SECTIONS: Array<{ id: string; label: string }> = [
  { id: "general", label: "General" },
  { id: "mission", label: "Mission & Context" },
  { id: "values", label: "Values" },
  { id: "signals", label: "Hiring signals" },
  { id: "philosophy", label: "Philosophy & Tone" },
];

const SECTION_BLURB: Record<string, string> = {
  general: "Basic identity of your organization.",
  mission: "What drives the company and the space it operates in.",
  values: "Core values the AI evaluates candidates against.",
  signals: "The traits that make a great hire — and the red flags to screen out.",
  philosophy: "How the AI talks to candidates and approaches hiring.",
};

export default function OrgSettingsPage() {
  const { data: org, isLoading, mutate } = useSWR<Org>("/dashboard/settings/org", swrFetcher);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [active, setActive] = useState<string>("general");

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
      await api.put("/dashboard/settings/org", { name, hiring_persona: persona });
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

  const initials = useMemo(
    () =>
      name
        ? name
            .split(/\s+/)
            .slice(0, 2)
            .map((w) => w[0])
            .join("")
            .toUpperCase()
        : "ORG",
    [name],
  );

  return (
    <>
      <Topbar title="Organization Settings" subtitle="hiring persona · mission · culture" />

      {/* Horizontal tab bar — below Topbar */}
      <div className="border-b border-border bg-background">
        <div className="mx-auto max-w-5xl px-8">
          <div className="flex items-center gap-1.5 py-2.5">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setActive(s.id)}
                className={cn(
                  "rounded-lg border px-3.5 py-1.5 text-sm font-medium transition-colors",
                  active === s.id
                    ? "border-primary bg-primary/5 text-primary"
                    : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/50 hover:text-foreground",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto pb-24">
        <div className="mx-auto max-w-5xl px-8 py-8">
          {/* Back link */}
          <Link
            href="/settings"
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to settings
          </Link>

          {/* Identity card */}
          <div className="mt-6 mb-8 flex items-center gap-4 rounded-xl border border-border bg-card px-5 py-4 shadow-sm">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-muted">
              <span className="font-mono text-sm font-bold text-foreground">{initials}</span>
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold leading-tight text-foreground">
                {name || "Organization profile"}
              </h1>
              {org?.slug && (
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                  /{org.slug}
                  <span className="ml-2 text-[10px] text-muted-foreground/50">read-only identifier</span>
                </p>
              )}
            </div>
          </div>

          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
              ))}
            </div>
          ) : !org ? (
            <p className="text-sm italic text-muted-foreground">Could not load org data</p>
          ) : (
            <div className="min-h-[420px] min-w-0">
              {/* Section heading */}
              <div className="mb-7">
                <h2 className="text-base font-semibold tracking-tight">
                  {SECTIONS.find((s) => s.id === active)?.label}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">{SECTION_BLURB[active]}</p>
              </div>

                {/* General */}
                {active === "general" && (
                  <div className="max-w-lg space-y-6">
                    <Field label="Company name">
                      <input
                        value={name}
                        onChange={(e) => {
                          setName(e.target.value);
                          setDirty(true);
                        }}
                        className={inlineArea}
                        placeholder="Acme Inc."
                      />
                    </Field>
                    <Field label="Slug" hint="read-only">
                      <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-2.5">
                        <p className="font-mono text-sm text-muted-foreground">{org.slug}</p>
                        <p className="mt-1 font-mono text-[10px] text-muted-foreground/50">
                          Used in API paths and email routing. Format: lowercase-hyphenated. Cannot be changed after setup.
                        </p>
                      </div>
                    </Field>
                  </div>
                )}

                {/* Mission & Context */}
                {active === "mission" && (
                  <div className="max-w-2xl space-y-6">
                    <Field label="Mission">
                      <textarea
                        value={persona.mission ?? ""}
                        onChange={(e) => update({ mission: e.target.value })}
                        className={cn(inlineArea, "min-h-[96px]")}
                        placeholder="What drives the company?"
                      />
                    </Field>
                    <Field label="Domain context" hint="industry, niche, space">
                      <textarea
                        value={persona.domain_context ?? ""}
                        onChange={(e) => update({ domain_context: e.target.value })}
                        className={cn(inlineArea, "min-h-[96px]")}
                        placeholder="e.g. B2B SaaS in construction-tech"
                      />
                    </Field>
                  </div>
                )}

                {/* Values */}
                {active === "values" && (
                  <div className="max-w-2xl">
                    <div>
                      {persona.values.map((v, i) => (
                        <ValueRow
                          key={i}
                          index={i}
                          val={v}
                          onChange={(nv) => {
                            const next = [...persona.values];
                            next[i] = nv;
                            update({ values: next });
                          }}
                          onRemove={() => update({ values: persona.values.filter((_, j) => j !== i) })}
                        />
                      ))}
                    </div>
                    {persona.values.length === 0 && (
                      <p className="py-2 text-sm italic text-muted-foreground/70">No values yet.</p>
                    )}
                    <button
                      type="button"
                      onClick={() => update({ values: [...persona.values, { name: "" }] })}
                      className="mt-4 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                    >
                      <Plus className="h-3 w-3" /> Add value
                    </button>
                  </div>
                )}

                {/* Hiring signals — two-column ledger */}
                {active === "signals" && (
                  <div className="grid max-w-3xl gap-x-12 gap-y-8 sm:grid-cols-2">
                    <SignalColumn
                      tone="good"
                      title="What good looks like"
                      items={persona.what_good_looks_like}
                      onChange={(items) => update({ what_good_looks_like: items })}
                      placeholder="e.g. Ships features end-to-end"
                    />
                    <SignalColumn
                      tone="bad"
                      title="Anti-patterns"
                      items={persona.anti_patterns}
                      onChange={(items) => update({ anti_patterns: items })}
                      placeholder="e.g. Cowboy coding without tests"
                    />
                  </div>
                )}

                {/* Philosophy & Tone */}
                {active === "philosophy" && (
                  <div className="max-w-2xl space-y-6">
                    <Field label="Hiring philosophy">
                      <textarea
                        value={persona.hiring_philosophy ?? ""}
                        onChange={(e) => update({ hiring_philosophy: e.target.value })}
                        className={cn(inlineArea, "min-h-[96px]")}
                        placeholder="What's the company's approach to hiring?"
                      />
                    </Field>
                    <Field label="Tone" hint="voice & personality">
                      <textarea
                        value={persona.tone ?? ""}
                        onChange={(e) => update({ tone: e.target.value })}
                        className={cn(inlineArea, "min-h-[96px]")}
                        placeholder="e.g. direct, supportive, no-nonsense"
                      />
                    </Field>
                  </div>
                )}
            </div>
          )}
        </div>
      </div>

      {/* Sticky footer save bar */}
      {org && !isLoading && (
        <div className="sticky bottom-0 z-10 flex items-center justify-between border-t border-border bg-background/95 px-8 py-4 backdrop-blur-sm">
          {msg ? (
            <span className={cn("text-xs font-medium", msg.type === "ok" ? "text-emerald-600" : "text-red-600")}>
              {msg.text}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">
              {dirty ? "You have unsaved changes" : "All saved"}
            </span>
          )}
          <Button
            onClick={save}
            disabled={saving || !dirty}
            className="bg-primary text-white hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Save className="mr-1.5 h-4 w-4" />}
            Save changes
          </Button>
        </div>
      )}
    </>
  );
}
