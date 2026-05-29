"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ChevronLeft, RefreshCw } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { PageHeader } from "@/components/layout/page-header";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { configApi, type AuditRow } from "@/lib/configClient";

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export default function AuditLogPage() {
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(100);
  const { data, mutate, isLoading } = useSWR<{ rows: AuditRow[] }>(
    `config:audit:${filter}:${limit}`,
    () => configApi.audit(filter || undefined, limit),
    { refreshInterval: 0 },
  );

  return (
    <>
      <Topbar title="Settings · Audit" subtitle="config_audit log" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6">
          <Link
            href="/settings"
            className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="h-3 w-3" /> back to settings
          </Link>
          <div className="mt-3">
            <PageHeader
              title={<span className="font-display text-[36px] font-normal">Audit log</span>}
              description={
                <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
                  every config write + test, with actor, IP, and diff
                </span>
              }
            />
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Input
              placeholder="filter by key (e.g. LLM_DAILY_BUDGET_USD)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="max-w-xs font-mono text-xs"
            />
            <select
              value={limit}
              onChange={(e) => setLimit(parseInt(e.target.value, 10))}
              className="h-9 rounded-md border border-input bg-background px-2 font-mono text-xs"
            >
              {[50, 100, 250, 500].map((n) => (
                <option key={n} value={n}>
                  last {n}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" onClick={() => mutate()} disabled={isLoading}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
              Refresh
            </Button>
          </div>

          <div className="mt-6 overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left">
              <thead className="bg-muted/40">
                <tr className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                  <th className="px-4 py-2">when</th>
                  <th className="px-4 py-2">action</th>
                  <th className="px-4 py-2">key</th>
                  <th className="px-4 py-2">old → new</th>
                  <th className="px-4 py-2">actor</th>
                  <th className="px-4 py-2">ip</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {(data?.rows ?? []).map((r) => (
                  <tr key={r.id} className="font-mono text-xs">
                    <td className="whitespace-nowrap px-4 py-2 text-muted-foreground">
                      {r.created_at ? new Date(r.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={
                          r.action === "delete"
                            ? "text-destructive"
                            : r.action === "test"
                              ? "text-muted-foreground"
                              : r.action === "create"
                                ? "text-success"
                                : "text-foreground"
                        }
                      >
                        {r.action}
                      </span>
                    </td>
                    <td className="px-4 py-2">{r.key}</td>
                    <td className="px-4 py-2 text-xs">
                      <span className="text-muted-foreground">{fmt(r.old_value)}</span>
                      <span className="mx-1 text-muted-foreground">→</span>
                      <span>{fmt(r.new_value)}</span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {r.actor} <span className="text-[10px]">({r.actor_role})</span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{r.ip ?? "—"}</td>
                  </tr>
                ))}
                {!isLoading && (data?.rows ?? []).length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center font-mono text-xs text-muted-foreground">
                      no rows
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
