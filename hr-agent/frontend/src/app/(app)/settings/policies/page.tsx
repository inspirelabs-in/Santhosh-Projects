"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Check,
  Pencil,
  RefreshCw,
  Search,
  Settings2,
  X,
} from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Topbar } from "@/components/layout/topbar";
import { cn, fmtRelative } from "@/lib/utils";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PolicyRule {
  id: string;
  key: string;
  role_id: string | null;
  value: unknown;
  value_type: string;
  description: string | null;
  updated_by: string | null;
  version: number;
  is_active: boolean;
  created_at: string;
}

interface PolicyListResponse {
  rules: PolicyRule[];
  total: number;
}

// ---------------------------------------------------------------------------
// Inline edit row
// ---------------------------------------------------------------------------

function PolicyRow({
  rule,
  onSaved,
}: {
  rule: PolicyRule;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(rule.value));
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      let parsed: unknown = draft;
      if (rule.value_type === "float" || rule.value_type === "int") {
        parsed = Number(draft);
        if (Number.isNaN(parsed as number)) {
          alert("Invalid number");
          setSaving(false);
          return;
        }
      } else if (rule.value_type === "bool") {
        parsed = draft.toLowerCase() === "true";
      }

      await api.patch(`/admin/policies/${rule.id}`, { value: parsed });
      setEditing(false);
      onSaved();
    } catch (err) {
      console.error("save failed", err);
    } finally {
      setSaving(false);
    }
  }

  const scopeLabel = rule.role_id ? (
    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
      Role override
    </span>
  ) : (
    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      Global
    </span>
  );

  return (
    <div className="grid grid-cols-[1fr_80px_180px_120px_60px_80px] items-center gap-3 border-b border-border px-4 py-3 text-sm">
      <div className="min-w-0">
        <p className="truncate font-medium">{rule.key}</p>
        {rule.description && (
          <p className="truncate text-xs text-muted-foreground">{rule.description}</p>
        )}
      </div>

      {scopeLabel}

      <div className="flex items-center gap-1.5">
        {editing ? (
          <>
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="h-7 w-28 text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
              autoFocus
            />
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-success"
              onClick={save}
              disabled={saving}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground"
              onClick={() => setEditing(false)}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </>
        ) : (
          <>
            <span className="font-mono text-sm font-semibold tabular-nums">
              {String(rule.value)}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
              onClick={() => {
                setDraft(String(rule.value));
                setEditing(true);
              }}
            >
              <Pencil className="h-3 w-3" />
            </Button>
          </>
        )}
      </div>

      <span className="font-mono text-[10px] uppercase text-muted-foreground">
        {rule.value_type}
      </span>

      <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
        v{rule.version}
      </span>

      <span className="text-[10px] text-muted-foreground">
        {rule.updated_by ?? "—"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const CATEGORIES = [
  { prefix: "fit_", label: "Fit scoring" },
  { prefix: "ctc_", label: "CTC" },
  { prefix: "screening_", label: "Screening" },
  { prefix: "voice_", label: "Voice screen" },
  { prefix: "stall_", label: "Stall detection" },
  { prefix: "contradiction_", label: "Contradictions" },
  { prefix: "confidence_", label: "Confidence gates" },
  { prefix: "autonomy.", label: "Autonomy" },
  { prefix: "scoring_", label: "Scoring weights" },
  { prefix: "classification_", label: "Classification" },
];

export default function PoliciesPage() {
  const [search, setSearch] = useState("");
  const { data, isLoading, mutate } = useSWR<PolicyListResponse>(
    "/admin/policies",
    () => api.get<PolicyListResponse>("/admin/policies"),
    { refreshInterval: 30_000 },
  );

  const rules = data?.rules ?? [];

  const filtered = useMemo(() => {
    if (!search.trim()) return rules;
    const q = search.toLowerCase();
    return rules.filter(
      (r) =>
        r.key.toLowerCase().includes(q) ||
        (r.description ?? "").toLowerCase().includes(q),
    );
  }, [rules, search]);

  const grouped = useMemo(() => {
    const groups: Record<string, PolicyRule[]> = {};
    for (const rule of filtered) {
      const cat = CATEGORIES.find((c) => rule.key.startsWith(c.prefix));
      const key = cat?.label ?? "Other";
      (groups[key] ??= []).push(rule);
    }
    return groups;
  }, [filtered]);

  return (
    <>
      <Topbar title="Policy rules" subtitle="Pipeline decision thresholds" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        {/* Toolbar */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search rules..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8"
            />
          </div>
          <Button variant="outline" size="sm" onClick={() => mutate()}>
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-14 rounded-lg skeleton" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <Settings2 className="h-8 w-8 text-primary" />
              <p className="text-base font-bold">No policy rules found</p>
            </CardContent>
          </Card>
        ) : (
          Object.entries(grouped).map(([category, categoryRules]) => (
            <Card key={category} className="overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-4 py-2">
                <Settings2 className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  {category}
                </span>
                <span className="ml-auto font-mono text-[10px] tabular-nums text-muted-foreground">
                  {categoryRules.length} rules
                </span>
              </div>
              <div className="grid grid-cols-[1fr_80px_180px_120px_60px_80px] items-center gap-3 border-b border-border bg-muted/20 px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                <span>Key</span>
                <span>Scope</span>
                <span>Value</span>
                <span>Type</span>
                <span>Ver.</span>
                <span>Updated by</span>
              </div>
              {categoryRules.map((rule) => (
                <PolicyRow key={rule.id} rule={rule} onSaved={() => mutate()} />
              ))}
            </Card>
          ))
        )}
      </div>
    </>
  );
}
