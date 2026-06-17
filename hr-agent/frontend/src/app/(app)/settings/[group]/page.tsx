"use client";

import { use, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ChevronLeft, Save, RotateCcw, Loader2, Eye, EyeOff } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { PageHeader } from "@/components/layout/page-header";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  configApi,
  type ConfigSchema,
  type ConfigFieldDef,
  type GroupValues,
} from "@/lib/configClient";

export default function ConfigGroupPage({
  params,
}: {
  params: Promise<{ group: string }>;
}) {
  const { group } = use(params);
  const groupName = decodeURIComponent(group);

  const { data: schema } = useSWR<ConfigSchema>("config:schema", () =>
    configApi.schema(),
  );
  const {
    data: values,
    mutate,
    isLoading,
  } = useSWR<GroupValues>(`config:group:${groupName}`, () =>
    configApi.group(groupName),
  );

  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [revealSecrets, setRevealSecrets] = useState<Set<string>>(new Set());

  const groupDef = schema?.groups.find((g) => g.name === groupName);
  const fields = groupDef?.fields ?? [];

  function currentValue(field: ConfigFieldDef): unknown {
    if (field.key in edits) return edits[field.key];
    const v = values?.fields.find((f) => f.key === field.key);
    return v?.value ?? field.default ?? "";
  }

  function setField(key: string, value: unknown) {
    setEdits((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    if (Object.keys(edits).length === 0) return;
    setSaving(true);
    try {
      await configApi.patch(edits);
      setEdits({});
      mutate();
    } finally {
      setSaving(false);
    }
  }

  const dirty = Object.keys(edits).length > 0;

  return (
    <>
      <Topbar
        title={`Settings · ${groupName}`}
        subtitle="runtime configuration"
      />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl px-8 py-6 pb-24">
          <Link
            href="/settings"
            className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="h-3 w-3" /> back to settings
          </Link>
          <div className="mt-3">
            <PageHeader
              title={
                <span className="font-display text-[36px] font-normal">
                  {groupName}
                </span>
              }
              description={
                <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
                  {fields.length} setting{fields.length !== 1 ? "s" : ""}
                </span>
              }
            />
          </div>

          {isLoading ? (
            <div className="mt-8 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              {fields.map((field) => {
                const val = currentValue(field);
                const isSecret = field.is_secret;
                const revealed = revealSecrets.has(field.key);

                return (
                  <div
                    key={field.key}
                    className="rounded-lg border border-border bg-card p-4"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <Label className="text-sm font-medium">
                          {field.label}
                        </Label>
                        {field.help && (
                          <p className="mt-0.5 text-[11px] text-muted-foreground">
                            {field.help}
                          </p>
                        )}
                        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/60">
                          {field.key}
                          {field.hot_reload && " · hot reload"}
                        </p>
                      </div>
                      {field.advanced && (
                        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] uppercase text-muted-foreground">
                          advanced
                        </span>
                      )}
                    </div>

                    <div className="mt-3">
                      {field.type === "bool" ? (
                        <button
                          type="button"
                          onClick={() => setField(field.key, !val)}
                          className={cn(
                            "relative h-6 w-11 rounded-full transition-colors",
                            val
                              ? "bg-primary"
                              : "bg-muted-foreground/20",
                          )}
                        >
                          <span
                            className={cn(
                              "absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                              !!val && "translate-x-5",
                            )}
                          />
                        </button>
                      ) : field.type === "select" && field.options ? (
                        <select
                          value={String(val ?? "")}
                          onChange={(e) => setField(field.key, e.target.value)}
                          className="h-9 w-full max-w-sm rounded-md border border-input bg-background px-3 font-mono text-xs"
                        >
                          <option value="">— not set —</option>
                          {field.options.map((o) => (
                            <option key={o} value={o}>
                              {o}
                            </option>
                          ))}
                        </select>
                      ) : field.type === "textarea" || field.type === "json" ? (
                        <textarea
                          value={String(val ?? "")}
                          onChange={(e) => setField(field.key, e.target.value)}
                          rows={4}
                          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
                        />
                      ) : field.type === "int" || field.type === "float" ? (
                        <Input
                          type="number"
                          value={val === null || val === undefined ? "" : String(val)}
                          onChange={(e) => {
                            const v = e.target.value;
                            setField(
                              field.key,
                              v === ""
                                ? null
                                : field.type === "int"
                                  ? parseInt(v, 10)
                                  : parseFloat(v),
                            );
                          }}
                          min={field.min ?? undefined}
                          max={field.max ?? undefined}
                          className="max-w-xs font-mono text-xs"
                        />
                      ) : (
                        <div className="flex items-center gap-2">
                          <Input
                            type={
                              isSecret && !revealed ? "password" : "text"
                            }
                            autoComplete={isSecret ? "off" : undefined}
                            value={String(val ?? "")}
                            onChange={(e) =>
                              setField(field.key, e.target.value)
                            }
                            className="max-w-md font-mono text-xs"
                          />
                          {isSecret && (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() =>
                                setRevealSecrets((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(field.key))
                                    next.delete(field.key);
                                  else next.add(field.key);
                                  return next;
                                })
                              }
                            >
                              {revealed ? (
                                <EyeOff className="h-3.5 w-3.5" />
                              ) : (
                                <Eye className="h-3.5 w-3.5" />
                              )}
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {fields.length === 0 && (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  No settings in this group.
                </p>
              )}
            </div>
          )}

          {dirty && (
            <div className="sticky bottom-0 mt-6 flex items-center gap-3 rounded-lg border border-primary/30 bg-card px-4 py-3 shadow-lg">
              <span className="text-xs text-muted-foreground">
                {Object.keys(edits).length} unsaved change
                {Object.keys(edits).length !== 1 ? "s" : ""}
              </span>
              <div className="flex-1" />
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEdits({})}
              >
                <RotateCcw className="mr-1 h-3.5 w-3.5" />
                Discard
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Save className="mr-1 h-3.5 w-3.5" />
                )}
                Save
              </Button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
