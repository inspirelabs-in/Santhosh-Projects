"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { Topbar } from "@/components/layout/topbar";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { swrFetcher } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { AuditItem } from "@/lib/types";
import { Pagination } from "@/components/pagination";
import { ExportMenu } from "@/components/export-menu";

interface AuditPageResp {
  items: AuditItem[];
  total: number;
  limit: number;
  offset: number;
}

export default function AuditPage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const q = debouncedQuery.trim();
  const url = new URLSearchParams();
  if (q) url.set("search", q);
  url.set("limit", String(limit));
  url.set("offset", String(offset));

  const { data: page, isLoading, isValidating, mutate } = useSWR<AuditPageResp>(
    `/dashboard/audit?${url.toString()}`,
    swrFetcher,
    { keepPreviousData: true },
  );
  const data = page?.items;
  const total = page?.total ?? 0;
  const [refreshing, setRefreshing] = useState(false);
  const refreshingNow = refreshing || isValidating;
  async function handleRefresh() {
    setRefreshing(true);
    try { await mutate(); } finally { setRefreshing(false); }
  }

  return (
    <>
      <Topbar title="Audit log" subtitle="Every action is recorded, append-only, forever" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        <PageHeader
          title="Activity trail"
          description="The database blocks updates and deletes on this table -- what's written is permanent. Search by action, actor, or candidate."
          actions={
            <>
              <ExportMenu
                options={[
                  {
                    label: "Audit log (CSV, last 30 days)",
                    path: "/export/audit?since_days=30",
                    filename: "audit-export.csv",
                    icon: "text",
                  },
                  {
                    label: "Audit log (CSV, last 90 days)",
                    path: "/export/audit?since_days=90",
                    filename: "audit-export.csv",
                    icon: "text",
                  },
                ]}
              />
              <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshingNow}>
                <RefreshCw className={cn("mr-1 h-3.5 w-3.5", refreshingNow && "animate-spin")} />
                {refreshingNow ? "Refreshing" : "Refresh"}
              </Button>
            </>
          }
        />

        <div className="mb-4 flex items-center gap-2">
          <div className="relative w-96">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Search action or actor…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                clearTimeout((window as any).__auditSearchT);
                (window as any).__auditSearchT = setTimeout(() => {
                  setDebouncedQuery(e.target.value);
                  setOffset(0);
                }, 300);
              }}
            />
          </div>
        </div>

        <Card>
          {isLoading ? (
            <div className="p-6 text-sm text-muted-foreground">Loading…</div>
          ) : !data || data.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">Nothing matches.</div>
          ) : (
            <div className="divide-y">
              {data.map((a) => (
                <div key={a.id} className="grid grid-cols-[160px,1fr,200px] items-start gap-4 p-4 text-sm">
                  <div className="tabular-nums text-xs text-muted-foreground">{fmtDate(a.created_at)}</div>
                  <div>
                    <div className="flex items-center gap-2">
                      <Badge variant={a.actor === "agent" || a.actor === "system" ? "secondary" : "default"}>
                        {a.actor}
                      </Badge>
                      <span className="font-medium">{a.action.replace(/_/g, " ")}</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {a.candidate_id ? <>candidate <code>{a.candidate_id.slice(0, 8)}</code> · </> : null}
                      {a.application_id ? <>application <code>{a.application_id.slice(0, 8)}</code></> : null}
                    </div>
                    {a.details ? (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                          View payload
                        </summary>
                        <pre className="mt-2 overflow-auto rounded-md bg-muted/50 p-3 text-[11px] leading-relaxed">
                          {JSON.stringify(a.details, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                  <div className="text-right text-[11px] text-muted-foreground space-y-0.5">
                    {a.model_version ? <div>model <span className="font-mono">{a.model_version}</span></div> : null}
                    {a.prompt_version ? <div>prompt <span className="font-mono">{a.prompt_version}</span></div> : null}
                    {a.langfuse_trace_id ? (
                      <div>trace <span className="font-mono">{a.langfuse_trace_id.slice(0, 8)}</span></div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
          <Pagination
            total={total}
            limit={limit}
            offset={offset}
            onChange={setOffset}
            onLimitChange={setLimit}
          />
        </Card>
      </div>
    </>
  );
}
