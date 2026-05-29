"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Loader2, RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ConfigField } from "./ConfigField";
import { TestConnectionButton } from "./TestConnectionButton";
import {
  configApi,
  type ConfigFieldDef,
  type ConfigSchema,
  type GroupValues,
} from "@/lib/configClient";
import { swrFetcher } from "@/lib/api";

interface Props {
  group: string;
  readOnly?: boolean;
  onlyKeys?: string[];
  onSaved?: () => void;
}

// Keys whose change requires explicit confirmation.
const RISKY_KEYS = new Set([
  "ENABLE_VOICE_SCREENING",
  "ENABLE_MEETING_ANALYSIS",
  "ENABLE_ASSESSMENT_ROUND",
  "AUTO_APPROVE_GREEN_TIER",
  "VOICE_AGENT_PASS_THRESHOLD",
  "DATA_RETENTION_DAYS_DEFAULT",
  "DATA_RETENTION_DAYS_TALENT_POOL",
  "LLM_DAILY_CALL_LIMIT",
  "LLM_DAILY_TOKEN_LIMIT",
  "LLM_PROVIDER",
]);

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export function ConfigForm({ group, readOnly: forcedReadOnly, onlyKeys, onSaved }: Props) {
  const { data: schema } = useSWR<ConfigSchema>("config:schema", () => configApi.schema());
  const { data: values, mutate } = useSWR<GroupValues>(
    `config:group:${group}`,
    () => configApi.group(group),
  );
  const { data: who } = useSWR<{ role: string }>("/dashboard/settings/whoami", swrFetcher);

  const isAdmin = who?.role === "admin";
  const readOnly = forcedReadOnly || !isAdmin;

  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const fields: ConfigFieldDef[] = useMemo(() => {
    if (!schema) return [];
    const all = schema.groups.find((g) => g.name === group)?.fields ?? [];
    if (!onlyKeys) return all;
    const set = new Set(onlyKeys);
    return all.filter((f) => set.has(f.key));
  }, [schema, group, onlyKeys]);

  useEffect(() => {
    setDraft({});
    setSavedMsg(null);
    setErrorMsg(null);
  }, [group]);

  const valueMap: Record<string, { value: unknown; configured: boolean }> = useMemo(() => {
    const out: Record<string, { value: unknown; configured: boolean }> = {};
    for (const f of values?.fields ?? []) {
      out[f.key] = { value: f.value, configured: f.configured };
    }
    return out;
  }, [values]);

  function buildCandidate(): Record<string, unknown> {
    const cand: Record<string, unknown> = {};
    for (const f of fields) {
      const cur = valueMap[f.key]?.value;
      cand[f.key] = f.key in draft ? draft[f.key] : cur;
    }
    return cand;
  }

  async function doSave() {
    setSaving(true);
    setErrorMsg(null);
    setSavedMsg(null);
    try {
      const updates: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(draft)) {
        if (v === "***set***") continue;
        updates[k] = v;
      }
      if (Object.keys(updates).length === 0) {
        setSavedMsg("No changes");
        return;
      }
      await configApi.patch(updates);
      setDraft({});
      await mutate();
      setSavedMsg(`Saved ${Object.keys(updates).length} setting(s)`);
      onSaved?.();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
      setConfirmOpen(false);
    }
  }

  function attemptSave() {
    const riskyChanges = Object.keys(draft).filter((k) => RISKY_KEYS.has(k));
    if (riskyChanges.length > 0) {
      setConfirmOpen(true);
    } else {
      void doSave();
    }
  }

  async function resetField(key: string) {
    if (!confirm(`Reset ${key} to env/default value? This drops the DB override.`)) return;
    try {
      await configApi.resetField(key);
      setDraft((d) => {
        const { [key]: _drop, ...rest } = d;
        return rest;
      });
      await mutate();
      setSavedMsg(`Reset ${key}`);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Reset failed");
    }
  }

  async function restartWorkers() {
    if (!confirm("Signal worker processes to restart? Active jobs may abort and resume.")) return;
    try {
      await configApi.restartWorkers();
      setSavedMsg("Workers signaled — restart in progress");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Restart failed");
    }
  }

  const subgroups = useMemo(() => {
    const groups = new Map<string, ConfigFieldDef[]>();
    for (const f of fields) {
      if (f.advanced && !showAdvanced) continue;
      const key = f.subgroup || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(f);
    }
    return Array.from(groups.entries());
  }, [fields, showAdvanced]);

  const dirtyCount = Object.keys(draft).length;
  const hasNonHotReloadDirty = Object.keys(draft).some((k) =>
    fields.find((f) => f.key === k && !f.hot_reload),
  );

  if (!schema || !values) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {readOnly && !forcedReadOnly && (
        <div className="rounded-md border border-warning/40 bg-warning/5 px-4 py-2 font-mono text-[11px] text-warning">
          read-only · admin role required to edit
        </div>
      )}

      {subgroups.map(([sub, sfields]) => (
        <div key={sub} className="space-y-4">
          {sub && (
            <div className="flex items-baseline gap-3">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                {sub}
              </h3>
              <span className="h-px flex-1 bg-border" />
            </div>
          )}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            {sfields.map((f) => {
              const v = valueMap[f.key];
              const draftHas = f.key in draft;
              const value = draftHas ? draft[f.key] : v?.value ?? null;
              return (
                <div
                  key={f.key}
                  className="rounded-lg border border-border bg-card p-4 space-y-2"
                >
                  <ConfigField
                    field={f}
                    value={value}
                    configured={Boolean(v?.configured)}
                    dirty={draftHas}
                    disabled={readOnly || saving}
                    onChange={(nv) => setDraft((d) => ({ ...d, [f.key]: nv }))}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    {f.test_integration && !readOnly && (
                      <TestConnectionButton
                        integration={f.test_integration}
                        buildCandidate={buildCandidate}
                        disabled={saving}
                      />
                    )}
                    {!readOnly && v?.configured && (
                      <button
                        type="button"
                        onClick={() => resetField(f.key)}
                        className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground hover:text-foreground"
                      >
                        <RotateCcw className="h-3 w-3" />
                        reset
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      <div className="flex flex-wrap items-center gap-3 pt-4">
        <Button onClick={attemptSave} disabled={readOnly || saving || dirtyCount === 0}>
          {saving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          Save {dirtyCount > 0 ? `(${dirtyCount})` : ""}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setDraft({})}
          disabled={dirtyCount === 0}
        >
          Discard
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setShowAdvanced((s) => !s)}
        >
          {showAdvanced ? "Hide advanced" : "Show advanced"}
        </Button>
        {hasNonHotReloadDirty && !readOnly && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={restartWorkers}
            className="border-warning/40 text-warning"
          >
            <AlertTriangle className="mr-1 h-3.5 w-3.5" />
            Restart workers (apply now)
          </Button>
        )}
        {savedMsg && (
          <span className="font-mono text-[11px] text-success">{savedMsg}</span>
        )}
        {errorMsg && (
          <span className="font-mono text-[11px] text-destructive">{errorMsg}</span>
        )}
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-warning" />
              Confirm risky changes
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <p className="font-mono text-xs text-muted-foreground">
              These settings affect candidate flows or spend caps. Review the diff
              before saving:
            </p>
            <div className="rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">
              {Object.entries(draft)
                .filter(([k]) => RISKY_KEYS.has(k))
                .map(([k, v]) => {
                  const old = valueMap[k]?.value;
                  return (
                    <div key={k} className="py-1">
                      <span className="text-muted-foreground">{k}:</span>{" "}
                      <span className="text-muted-foreground">{fmt(old)}</span>
                      <span className="mx-1 text-muted-foreground">→</span>
                      <span className="text-foreground">{fmt(v)}</span>
                    </div>
                  );
                })}
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={doSave} disabled={saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Confirm save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
