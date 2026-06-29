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
  Users,
} from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  StatusTag,
  STAGE_KEY_ORDER,
  humanizeStageKey,
  type Stage,
} from "@/components/status-tag";
import { SkeletonLines } from "@/components/skeleton";
import { api, swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Pagination } from "@/components/pagination";
import { Avatar } from "@/components/ui/avatar";
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

// V2: filter options are role-defined stage_keys, not the frozen legacy enum.
const FILTER_STAGES: string[] = ["all", ...STAGE_KEY_ORDER];

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

function relativeDate(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });
}

export default function CandidatesPage() {
  const sp = useSearchParams();
  const router = useRouter();
  const initial = sp.get("stage") ?? "all";
  const [stage, setStage] = useState<string>(initial);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [quickViewId, setQuickViewId] = useState<string | null>(null);

  // Reset pagination when filters change.
  const filterKey = `${stage}|${q.trim()}`;
  useMemo(() => {
    setOffset(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  // NOTE: stage filtering is applied client-side against the V2 ``current_stage_key``.
  // The legacy backend ``stage`` param only matches the frozen ``current_stage`` enum,
  // so we intentionally do NOT forward V2 stage_keys to the server.
  const query = new URLSearchParams();
  if (q.trim()) query.set("q", q.trim());
  query.set("limit", String(limit));
  query.set("offset", String(offset));

  const { data: page, isLoading, isValidating, mutate } = useSWR<CandidatePage>(
    `/dashboard/v1/candidates?${query.toString()}`,
    swrFetcher,
    { refreshInterval: 15000, keepPreviousData: true },
  );
  const data = useMemo(() => {
    const items = page?.items;
    if (!items) return items;
    if (stage === "all") return items;
    // Compare against the V2 stage cursor, falling back to the legacy enum.
    return items.filter((c) => (c.current_stage_key || c.current_stage) === stage);
  }, [page?.items, stage]);
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

            {/* ── Toolbar ── */}
            <div className="mb-5 flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-sm font-medium text-muted-foreground">
                  <Users className="h-3.5 w-3.5" />
                  {total} candidates
                </span>
              </div>
              <div className="flex items-center gap-2">
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
                  <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", refreshingNow && "animate-spin")} />
                  {refreshingNow ? "Refreshing" : "Refresh"}
                </Button>
              </div>
            </div>

            {/* ── Search bar ── */}
            <div className="relative mb-4">
              <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search by name or email…"
                className="h-10 pl-10 text-sm"
              />
            </div>

            {/* ── Stage filter chips ── */}
            <div className="mb-6 flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
              {FILTER_STAGES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStage(s)}
                  className={cn(
                    "flex-none rounded-full border px-4 py-1.5 text-sm transition-colors",
                    stage === s
                      ? "border-primary bg-primary text-white"
                      : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
                  )}
                >
                  {s === "all" ? "All stages" : humanizeStageKey(s)}
                </button>
              ))}
            </div>

            {/* ── Candidates table ── */}
            <div className="rounded-xl border border-border bg-card shadow-card">
              {isLoading ? (
                <div className="divide-y divide-border">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="px-4 py-4">
                      <SkeletonLines lines={2} />
                    </div>
                  ))}
                </div>
              ) : !data || data.length === 0 ? (
                /* ── Empty state ── */
                <div className="flex flex-col items-center justify-center py-20 text-center">
                  <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-muted">
                    <Users className="h-6 w-6 text-muted-foreground" />
                  </div>
                  <p className="text-base font-semibold text-foreground">No candidates found</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Try broadening the stage filter or clearing your search.
                  </p>
                </div>
              ) : (
                <>
                  {/* Table header */}
                  <div className="grid grid-cols-[40px_1fr_200px_160px_80px_100px] items-center gap-4 border-b border-border bg-muted/50 px-4 py-2.5">
                    <div className="flex items-center">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={toggleAll}
                        className="h-3.5 w-3.5 cursor-pointer accent-primary"
                        aria-label="select all"
                      />
                    </div>
                    <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      Candidate
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      Role
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      Stage
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      Fit
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                      Updated
                    </span>
                  </div>

                  {/* Table rows */}
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
                              "grid grid-cols-[40px_1fr_200px_160px_80px_100px] items-center gap-4 px-4 py-3 transition-colors hover:bg-muted/40",
                              isSel && "bg-primary/5",
                              isRecentlyProcessed && "agent-processing",
                            )}
                          >
                            {/* Checkbox */}
                            <div className="flex items-center">
                              <input
                                type="checkbox"
                                checked={isSel}
                                onChange={() => toggle(c.application_id)}
                                onClick={(e) => e.stopPropagation()}
                                className="h-3.5 w-3.5 cursor-pointer accent-primary"
                                aria-label="select candidate"
                              />
                            </div>

                            {/* Candidate name + email */}
                            <Link
                              href={`/candidates/${c.application_id}`}
                              className="flex min-w-0 items-center gap-3"
                            >
                              <Avatar name={c.name ?? c.email} size="sm" />
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="truncate text-base font-semibold text-foreground">
                                    {c.name ?? c.email ?? "Unnamed"}
                                  </span>
                                  {isRecentlyProcessed && (
                                    <span className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                                      <span className="relative flex h-1.5 w-1.5">
                                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
                                      </span>
                                      Agent
                                    </span>
                                  )}
                                </div>
                                <div className="truncate text-xs text-muted-foreground">
                                  {c.email ?? "no email"}
                                </div>
                              </div>
                            </Link>

                            {/* Role */}
                            <Link
                              href={`/candidates/${c.application_id}`}
                              className="truncate text-sm text-muted-foreground"
                            >
                              {c.role_title ?? "—"}
                            </Link>

                            {/* Stage */}
                            <Link
                              href={`/candidates/${c.application_id}`}
                              className="flex items-center"
                            >
                              <StatusTag stageKey={c.current_stage_key || c.current_stage} />
                            </Link>

                            {/* Fit score */}
                            <Link
                              href={`/candidates/${c.application_id}`}
                              className="flex items-center"
                            >
                              {c.fit_score != null ? (
                                <span
                                  className={cn(
                                    "font-mono text-sm font-bold",
                                    c.fit_tier === "green"
                                      ? "text-emerald-600"
                                      : c.fit_tier === "red"
                                        ? "text-red-500"
                                        : "text-amber-600",
                                  )}
                                >
                                  {c.fit_score}
                                </span>
                              ) : (
                                <span className="text-xs text-muted-foreground">—</span>
                              )}
                            </Link>

                            {/* Updated date */}
                            <Link
                              href={`/candidates/${c.application_id}`}
                              className="text-xs text-muted-foreground"
                            >
                              {relativeDate(c.updated_at)}
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
          </div>
        </div>

        {quickViewId && (
          <QuickViewPanel
            applicationId={quickViewId}
            onClose={() => setQuickViewId(null)}
          />
        )}
      </div>

      {/* ── Floating bulk action bar ── */}
      {selCount > 0 && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
          <div className="flex items-center gap-4 rounded-full bg-foreground px-6 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.25)]">
            <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-background">
              {selCount} selected
            </span>
            <div className="h-4 w-px bg-background/20" />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                className="rounded-full border border-background/30 text-background hover:bg-background/15"
                onClick={goCompare}
                disabled={selCount < 2 || selCount > 4}
                title={selCount < 2 ? "Pick 2–4 candidates" : selCount > 4 ? "Max 4 candidates" : "Compare"}
              >
                <GitCompare className="mr-1.5 h-3.5 w-3.5" />
                Compare
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="rounded-full border border-background/30 text-background hover:bg-background/15"
                onClick={bulkAdvance}
                disabled={bulkBusy === "advance"}
              >
                {bulkBusy === "advance" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="mr-1.5 h-3.5 w-3.5" />
                )}
                Advance
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="rounded-full border border-destructive/50 text-red-400 hover:bg-destructive/20"
                onClick={() => setRejectOpen(true)}
                disabled={bulkBusy === "reject"}
              >
                <X className="mr-1.5 h-3.5 w-3.5" />
                Reject all
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="rounded-full text-background/60 hover:bg-background/10 hover:text-background"
                onClick={clearSelection}
              >
                Clear
              </Button>
            </div>
            {bulkError && (
              <span className="ml-2 font-mono text-[11px] text-red-400">
                {bulkError}
              </span>
            )}
          </div>
        </div>
      )}

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
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-card"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-foreground">
          Reject {count} candidate{count !== 1 ? "s" : ""}
        </h3>
        <p className="mt-1.5 text-sm text-muted-foreground">
          One reason applies to all. An email will be drafted per candidate and sent on confirm.
        </p>

        <div className="mt-5 space-y-2">
          <label className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            Rejection reason
          </label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
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
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <X className="mr-1.5 h-3.5 w-3.5" />
            )}
            Confirm rejection
          </Button>
        </div>
      </div>
    </div>
  );
}
