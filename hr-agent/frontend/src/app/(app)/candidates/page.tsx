"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Search,
  RefreshCw,
  GitCompare,
  X,
  Check,
  Loader2,
  LayoutList,
  LayoutGrid,
} from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  StatusTag,
  STAGE_LABELS,
  STAGE_ORDER,
  type Stage,
} from "@/components/status-tag";
import { SkeletonLines } from "@/components/skeleton";
import { api, swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Pagination } from "@/components/pagination";
import { Avatar } from "@/components/ui/avatar";
import { KanbanView } from "@/components/candidates/kanban-view";
import { QuickViewPanel } from "@/components/candidates/quick-view-panel";
import { CandidateContextMenu } from "@/components/candidates/candidate-context-menu";
import { CsvExportButton } from "@/components/candidates/csv-export";
import { ExportMenu } from "@/components/export-menu";

interface Candidate {
  application_id: string;
  candidate_id: string;
  name: string | null;
  email: string | null;
  role_title: string | null;
  current_stage: Stage;
  current_stage_key: string;
  screening_score: number | null;
  fit_score: number | null;
  fit_tier: string | null;
  updated_at: string;
}

const FILTER_STAGES: (Stage | "all")[] = [
  "all",
  ...STAGE_ORDER,
  "needs_hr_review",
  "rejected",
  "hired",
];

const REJECTION_CATEGORIES = [
  { value: "experience_mismatch", label: "Experience mismatch" },
  { value: "skills_gap", label: "Skills gap" },
  { value: "location", label: "Location / relocation" },
  { value: "compensation", label: "Compensation band" },
  { value: "notice_period", label: "Notice period" },
  { value: "role_filled", label: "Role filled" },
  { value: "other", label: "Other" },
];

interface CandidatePage {
  items: Candidate[];
  total: number;
  limit: number;
  offset: number;
}

export default function CandidatesPage() {
  const sp = useSearchParams();
  const router = useRouter();
  const initial = (sp.get("stage") as Stage | "all" | null) ?? "all";
  const [stage, setStage] = useState<Stage | "all">(initial);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [viewMode, setViewMode] = useState<"list" | "kanban">("list");
  const [quickViewId, setQuickViewId] = useState<string | null>(null);

  // Reset pagination when filters change.
  const filterKey = `${stage}|${q.trim()}`;
  useMemo(() => {
    setOffset(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  const query = new URLSearchParams();
  if (stage !== "all") query.set("stage", stage);
  if (q.trim()) query.set("q", q.trim());
  query.set("limit", String(limit));
  query.set("offset", String(offset));

  const { data: page, isLoading, isValidating, mutate } = useSWR<CandidatePage>(
    `/dashboard/v1/candidates?${query.toString()}`,
    swrFetcher,
    { refreshInterval: 15000, keepPreviousData: true },
  );
  const data = page?.items;
  const total = page?.total ?? 0;
  const [refreshing, setRefreshing] = useState(false);
  const refreshingNow = refreshing || isValidating;
  async function handleRefresh() {
    setRefreshing(true);
    try { await mutate(); } finally { setRefreshing(false); }
  }

  const visibleIds = useMemo(
    () => (data ?? []).map((c) => c.application_id),
    [data],
  );
  const allSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visibleIds));
    }
  }

  function clearSelection() {
    setSelected(new Set());
  }

  async function bulkAdvance() {
    setBulkBusy("advance");
    setBulkError(null);
    try {
      await api.post("/dashboard/v1/candidates-bulk/stage", {
        application_ids: Array.from(selected),
        stage: "assignment_sent",
        note: "bulk advance from candidates list",
      });
      clearSelection();
      mutate();
    } catch (e: any) {
      setBulkError(e?.message ?? "advance failed");
    } finally {
      setBulkBusy(null);
    }
  }

  async function bulkReject(category: string) {
    setBulkBusy("reject");
    setBulkError(null);
    try {
      await api.post("/dashboard/v1/candidates-bulk/reject", {
        application_ids: Array.from(selected),
        category,
        send_email: true,
      });
      clearSelection();
      setRejectOpen(false);
      mutate();
    } catch (e: any) {
      setBulkError(e?.message ?? "reject failed");
    } finally {
      setBulkBusy(null);
    }
  }

  function goCompare() {
    const ids = Array.from(selected).slice(0, 4).join(",");
    router.push(`/candidates/compare?ids=${ids}`);
  }

  const selCount = selected.size;

  return (
    <>
      <Topbar title="Candidates" subtitle="Everyone, everywhere in the pipeline" />
      <div className="flex flex-1 overflow-hidden">
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6 pb-24">
          <div className="mb-4 flex items-center justify-between gap-4">
            <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
              {total} total
            </span>
            <div className="flex items-center gap-2">
              <div className="flex rounded-md border border-border">
                <button
                  type="button"
                  onClick={() => setViewMode("list")}
                  className={cn("flex items-center gap-1 px-2.5 py-1.5 text-xs transition", viewMode === "list" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}
                >
                  <LayoutList className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("kanban")}
                  className={cn("flex items-center gap-1 px-2.5 py-1.5 text-xs transition", viewMode === "kanban" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}
                >
                  <LayoutGrid className="h-3.5 w-3.5" />
                </button>
              </div>
              <CsvExportButton data={data ?? []} />
              <ExportMenu
                options={[
                  {
                    label: "All candidates (CSV)",
                    path: `/export/candidates?format=csv&since_days=90${stage !== "all" ? `&status=${stage}` : ""}`,
                    filename: "candidates-export.csv",
                    icon: "spreadsheet",
                  },
                  {
                    label: "Pipeline summary (CSV)",
                    path: "/export/pipeline-summary",
                    filename: "pipeline-summary.csv",
                    icon: "spreadsheet",
                  },
                ]}
              />
              <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshingNow}>
                <RefreshCw className={cn("mr-1 h-3.5 w-3.5", refreshingNow && "animate-spin")} />
                {refreshingNow ? "Refreshing" : "Refresh"}
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search by name or email"
                className="pl-9"
              />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-1.5">
            {FILTER_STAGES.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStage(s)}
                className={cn(
                  "rounded-full border px-3 py-1 font-mono text-[11px] lowercase transition",
                  stage === s
                    ? "border-foreground bg-foreground text-background"
                    : "border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground",
                )}
              >
                {s === "all" ? "all" : STAGE_LABELS[s as Stage] ?? s}
              </button>
            ))}
          </div>

          {selCount > 0 && (
            <div className="sticky top-0 z-20 mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-foreground bg-foreground px-4 py-3 text-background shadow-lg">
              <span className="font-mono text-[11px] uppercase tracking-[0.15em]">
                {selCount} selected
              </span>
              <div className="ml-auto flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="border-background/40 bg-transparent text-background hover:bg-background/10"
                  onClick={goCompare}
                  disabled={selCount < 2 || selCount > 4}
                  title={selCount < 2 ? "pick 2-4" : selCount > 4 ? "max 4" : "compare"}
                >
                  <GitCompare className="mr-1 h-3.5 w-3.5" /> Compare ({selCount})
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-background/40 bg-transparent text-background hover:bg-background/10"
                  onClick={bulkAdvance}
                  disabled={bulkBusy === "advance"}
                >
                  {bulkBusy === "advance" ? (
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Check className="mr-1 h-3.5 w-3.5" />
                  )}
                  Advance to assignment
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-destructive/50 bg-transparent text-destructive-foreground hover:bg-destructive/20"
                  onClick={() => setRejectOpen(true)}
                  disabled={bulkBusy === "reject"}
                >
                  <X className="mr-1 h-3.5 w-3.5" /> Reject all
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-background hover:bg-background/10"
                  onClick={clearSelection}
                >
                  Clear
                </Button>
              </div>
              {bulkError && (
                <span className="w-full font-mono text-[11px] text-destructive-foreground">
                  {bulkError}
                </span>
              )}
            </div>
          )}

          {viewMode === "kanban" && data ? (
            <div className="mt-6">
              <KanbanView data={data} onQuickView={(id) => setQuickViewId(id)} />
            </div>
          ) : (
          <div className="mt-6 rounded-lg border border-border bg-card">
            {isLoading ? (
              <div className="divide-y divide-border">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="p-4">
                    <SkeletonLines lines={2} />
                  </div>
                ))}
              </div>
            ) : !data || data.length === 0 ? (
              <div className="p-16 text-center">
                <p className="font-display text-xl italic text-muted-foreground">
                  No candidates match this filter.
                </p>
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                  try broadening the stage or clearing the search
                </p>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3 border-b border-border px-4 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 cursor-pointer accent-foreground"
                    aria-label="select all"
                  />
                  <span>
                    {allSelected ? "all visible selected" : "select visible"}
                  </span>
                </div>
                <ul className="divide-y divide-border">
                  {data.map((c) => {
                    const isSel = selected.has(c.application_id);
                    const isRecentlyProcessed = Date.now() - new Date(c.updated_at).getTime() < 60_000;
                    return (
                      <CandidateContextMenu
                        key={c.application_id}
                        applicationId={c.application_id}
                        candidateName={c.name ?? "Unnamed"}
                        email={c.email}
                        onQuickView={() => setQuickViewId(c.application_id)}
                      >
                      <li
                          className={cn(
                            "grid grid-cols-[auto_1fr_auto] items-center gap-4 p-4 transition hover:bg-background/70 md:grid-cols-[auto_1fr_180px_80px_140px_auto]",
                            isSel && "bg-background/60",
                            isRecentlyProcessed && "agent-processing",
                          )}
                      >
                        <input
                          type="checkbox"
                          checked={isSel}
                          onChange={() => toggle(c.application_id)}
                          onClick={(e) => e.stopPropagation()}
                          className="h-3.5 w-3.5 cursor-pointer accent-foreground"
                          aria-label="select candidate"
                        />
                        <Link
                          href={`/candidates/${c.application_id}`}
                          className="min-w-0 flex items-center gap-3"
                        >
                          <Avatar name={c.name ?? c.email} size="sm" />
                          <div>
                          <div className="flex items-baseline gap-2">
                            <span className="truncate font-display text-lg">
                              {c.name ?? c.email ?? "Unnamed"}
                            </span>
                          </div>
                          <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                            {c.email ?? "no email"}
                          </div>
                          </div>
                        </Link>
                        <Link
                          href={`/candidates/${c.application_id}`}
                          className="hidden text-sm text-muted-foreground md:block"
                        >
                          {c.role_title ?? "—"}
                        </Link>
                        <Link
                          href={`/candidates/${c.application_id}`}
                          className="hidden font-mono text-[11px] text-muted-foreground md:block"
                        >
                          {new Date(c.updated_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" })}
                        </Link>
                        <Link href={`/candidates/${c.application_id}`} className="hidden items-center gap-1 md:flex">
                          {c.fit_score != null && (
                            <span className={cn(
                              "font-mono text-xs font-semibold",
                              (c.fit_tier === "green") ? "text-emerald-600" : (c.fit_tier === "red" ? "text-red-500" : "text-amber-600"),
                            )}>
                              {c.fit_score}
                            </span>
                          )}
                        </Link>
                        <Link href={`/candidates/${c.application_id}`} className="flex items-center gap-2">
                          <StatusTag stage={c.current_stage} />
                          {isRecentlyProcessed && (
                            <span className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                              <span className="relative flex h-1.5 w-1.5">
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
                              </span>
                              Agent
                            </span>
                          )}
                        </Link>
                      </li>
                      </CandidateContextMenu>
                    );
                  })}
                </ul>
                <Pagination
                  total={total}
                  limit={limit}
                  offset={offset}
                  onChange={setOffset}
                  onLimitChange={setLimit}
                />
              </>
            )}
          </div>
          )}
        </div>
      </div>
      {quickViewId && (
        <QuickViewPanel
          applicationId={quickViewId}
          onClose={() => setQuickViewId(null)}
        />
      )}
      </div>

      {rejectOpen && (
        <BulkRejectDialog
          count={selCount}
          busy={bulkBusy === "reject"}
          onCancel={() => setRejectOpen(false)}
          onConfirm={bulkReject}
        />
      )}
    </>
  );
}

function BulkRejectDialog({
  count,
  busy,
  onCancel,
  onConfirm,
}: {
  count: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (category: string) => void;
}) {
  const [category, setCategory] = useState(REJECTION_CATEGORIES[0].value);

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-foreground/20 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="font-display text-2xl">Reject {count} candidates</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          One reason applies to all. Email drafted per candidate, sent on confirm.
        </p>

        <div className="mt-4 space-y-2">
          <label className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            reason
          </label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {REJECTION_CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => onConfirm(category)}
            disabled={busy}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {busy ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <X className="mr-1 h-3.5 w-3.5" />
            )}
            Confirm rejection
          </Button>
        </div>
      </div>
    </div>
  );
}
