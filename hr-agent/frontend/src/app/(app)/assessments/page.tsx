"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ClipboardCheck,
  ArrowRight,
  Search,
  Plus,
  Loader2,
  ChevronRight,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetBody,
  SheetFooter,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { StatusTag } from "@/components/status-tag";
import {
  assessments,
  type AssessmentListItem,
  type AssessmentKind,
} from "@/lib/api/agentic";
import { fmtRelative, fmtDate } from "@/lib/utils";

const BAND_CLASS = {
  green: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  amber: "bg-amber-50 text-amber-700 border border-amber-200",
  red: "bg-red-50 text-red-600 border border-red-200",
} as const;

type Band = "green" | "amber" | "red";
type TabKey = "all" | "active" | "completed";

const ACTIVE_STATUSES = new Set([
  "queued",
  "invite_sent",
  "in_progress",
  "pending",
  "started",
]);
const COMPLETED_STATUSES = new Set(["completed", "scored", "done"]);

export default function AssessmentsPage() {
  const { data, error, isLoading, mutate } = useSWR<AssessmentListItem[]>(
    "/agentic/assessments",
    () => assessments.list({ limit: 200 }),
    { refreshInterval: 30_000 },
  );

  const [tab, setTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [bandFilter, setBandFilter] = useState<string>("all");
  const [selected, setSelected] = useState<AssessmentListItem | null>(null);
  const [showDispatch, setShowDispatch] = useState(false);

  const stats = useMemo(() => {
    const rows = data ?? [];
    const completed = rows.filter((r) => COMPLETED_STATUSES.has(r.status));
    const active = rows.filter((r) => ACTIVE_STATUSES.has(r.status));
    const bands: Record<Band, number> = { green: 0, amber: 0, red: 0 };
    let pctSum = 0;
    let pctCount = 0;
    for (const r of completed) {
      if (r.fit_band) bands[r.fit_band]++;
      if (r.percentile != null) {
        pctSum += r.percentile;
        pctCount++;
      }
    }
    return {
      total: rows.length,
      active: active.length,
      completed: completed.length,
      avgPct: pctCount ? Math.round(pctSum / pctCount) : null,
      bands,
    };
  }, [data]);

  const statusOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of data ?? []) set.add(r.status);
    return Array.from(set).sort();
  }, [data]);

  const kindOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of data ?? []) if (r.kind) set.add(r.kind);
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data ?? []).filter((r) => {
      if (tab === "active" && !ACTIVE_STATUSES.has(r.status)) return false;
      if (tab === "completed" && !COMPLETED_STATUSES.has(r.status)) return false;
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (kindFilter !== "all" && r.kind !== kindFilter) return false;
      if (bandFilter !== "all" && r.fit_band !== bandFilter) return false;
      if (q) {
        const hay =
          (r.candidate_name ?? "") +
          " " +
          (r.role_title ?? "") +
          " " +
          (r.application_id ?? "");
        if (!hay.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [data, tab, search, statusFilter, kindFilter, bandFilter]);

  return (
    <>
      <Topbar title="Assessments" subtitle="Take-home Assignments" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-5">

        {/* KPI cards */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Total" value={stats.total} />
          <StatCard label="Active" value={stats.active} accent="blue" />
          <StatCard label="Completed" value={stats.completed} accent="green" />
          <StatCard
            label="Avg Score"
            value={stats.avgPct != null ? `${stats.avgPct}%` : "—"}
            accent="purple"
          />
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Segment control tabs */}
          <div className="flex rounded-lg bg-muted p-1 gap-0.5">
            {(["all", "active", "completed"] as TabKey[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  tab === t
                    ? "bg-card shadow-sm text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {t === "all"
                  ? `All (${stats.total})`
                  : t === "active"
                  ? `Active (${stats.active})`
                  : `Completed (${stats.completed})`}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="relative ml-auto w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search candidate, role, app id"
              className="pl-8 h-8 text-xs"
            />
          </div>

          {/* Status select */}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-36 h-8 text-xs">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All status</SelectItem>
              {statusOptions.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Kind select */}
          <Select value={kindFilter} onValueChange={setKindFilter}>
            <SelectTrigger className="w-32 h-8 text-xs">
              <SelectValue placeholder="Kind" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All kinds</SelectItem>
              {kindOptions.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Band select */}
          <Select value={bandFilter} onValueChange={setBandFilter}>
            <SelectTrigger className="w-28 h-8 text-xs">
              <SelectValue placeholder="Band" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All bands</SelectItem>
              <SelectItem value="green">Green</SelectItem>
              <SelectItem value="amber">Amber</SelectItem>
              <SelectItem value="red">Red</SelectItem>
            </SelectContent>
          </Select>

          <Button size="sm" className="h-8" onClick={() => setShowDispatch(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Dispatch
          </Button>
        </div>

        {error ? (
          <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error.message}
          </div>
        ) : null}

        {/* Main table */}
        <div className="bg-card rounded-xl shadow-card overflow-hidden">
          {/* Table header */}
          <div className="grid grid-cols-[2fr_1.5fr_120px_150px_90px_110px_110px_32px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground sticky top-0 backdrop-blur z-10">
            <span>Candidate · Role</span>
            <span>Role</span>
            <span>Kind</span>
            <span>Status</span>
            <span>Band</span>
            <span>Score</span>
            <span>Sent / Created</span>
            <span />
          </div>

          {/* Loading skeletons */}
          {isLoading ? (
            <div className="space-y-px">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-14 skeleton mx-0 rounded-none" />
              ))}
            </div>
          ) : null}

          {/* Empty state */}
          {!isLoading && filtered.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-20 text-center">
              <ClipboardCheck className="h-10 w-10 text-primary" />
              <p className="text-base font-bold">No assessments match</p>
              <p className="max-w-md text-sm text-muted-foreground">
                {(data ?? []).length === 0
                  ? "Send a PI invite from a candidate page or use Dispatch above."
                  : "Try clearing filters or switching tabs."}
              </p>
            </div>
          ) : null}

          {/* Rows */}
          {!isLoading && filtered.length > 0 ? (
            <div>
              {filtered.map((row) => (
                <div
                  key={row.assessment_id}
                  onClick={() => setSelected(row)}
                  className="grid w-full grid-cols-[2fr_1.5fr_120px_150px_90px_110px_110px_32px] items-center gap-3 border-b border-border last:border-0 px-4 py-3 text-left text-sm transition hover:bg-muted/30 cursor-pointer group"
                >
                  {/* Candidate */}
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-sm">
                      {row.candidate_name ?? "Unnamed"}
                    </p>
                  </div>

                  {/* Role */}
                  <p className="truncate text-xs text-muted-foreground">
                    {row.role_title ?? "—"}
                  </p>

                  {/* Kind */}
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground truncate">
                    {row.kind ?? row.provider}
                  </span>

                  {/* Status */}
                  <div>
                    <StatusTag stage={`assessment_${row.status}`} />
                  </div>

                  {/* Band */}
                  {row.fit_band ? (
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-center font-mono text-[10px] uppercase tracking-[0.15em] w-fit ${BAND_CLASS[row.fit_band]}`}
                    >
                      {row.fit_band}
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}

                  {/* Score */}
                  <span className="font-mono text-sm font-bold tabular-nums">
                    {row.percentile != null
                      ? `${Math.round(row.percentile)}%`
                      : row.normalized_score != null
                        ? row.normalized_score.toFixed(1)
                        : "—"}
                  </span>

                  {/* Time */}
                  <span className="text-xs text-muted-foreground">
                    {fmtRelative(row.created_at)}
                  </span>

                  {/* Trailing affordance */}
                  <div className="flex justify-end">
                    <ChevronRight className="h-4 w-4 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {/* Row detail — Sheet slide-over */}
      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent width="md">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>{selected.candidate_name ?? "Unnamed"}</SheetTitle>
                <SheetDescription>
                  {selected.role_title ?? "—"}
                  {" · "}
                  <StatusTag stage={`assessment_${selected.status}`} />
                </SheetDescription>
              </SheetHeader>

              <SheetBody className="space-y-5">
                {/* Scores — prominent */}
                <div>
                  <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Scores
                  </p>
                  <div className="flex items-end gap-6">
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-0.5">
                        Percentile
                      </p>
                      <p className="font-mono text-4xl font-extrabold tabular-nums leading-none text-foreground">
                        {selected.percentile != null
                          ? `${Math.round(selected.percentile)}%`
                          : "—"}
                      </p>
                    </div>
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-0.5">
                        Normalized
                      </p>
                      <p className="font-mono text-4xl font-extrabold tabular-nums leading-none text-foreground">
                        {selected.normalized_score != null
                          ? selected.normalized_score.toFixed(2)
                          : "—"}
                      </p>
                    </div>
                    {selected.fit_band ? (
                      <div className="ml-auto">
                        <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
                          Band
                        </p>
                        <span
                          className={`inline-block rounded-full px-3 py-1 font-mono text-xs font-semibold uppercase tracking-[0.15em] ${BAND_CLASS[selected.fit_band]}`}
                        >
                          {selected.fit_band}
                        </span>
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="border-t border-border" />

                {/* Identifiers */}
                <div>
                  <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Identifiers
                  </p>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Assessment</span>
                      <span className="font-mono break-all text-right">{selected.assessment_id}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Application</span>
                      <span className="font-mono break-all text-right">{selected.application_id}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Provider</span>
                      <span className="font-mono">{selected.provider}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Kind</span>
                      <span className="font-mono uppercase">{selected.kind ?? "—"}</span>
                    </div>
                  </div>
                </div>

                <div className="border-t border-border" />

                {/* Timeline */}
                <div>
                  <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Timeline
                  </p>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Created</span>
                      <span className="tabular-nums">{fmtDate(selected.created_at)}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Invite sent</span>
                      <span className="tabular-nums">{fmtDate(selected.invite_sent_at)}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                      <span className="text-muted-foreground shrink-0">Completed</span>
                      <span className="tabular-nums">{fmtDate(selected.completed_at)}</span>
                    </div>
                  </div>
                </div>
              </SheetBody>

              <SheetFooter className="flex justify-end">
                <Link
                  href={`/candidates/${selected.application_id}`}
                  className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
                >
                  Open candidate <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>

      {/* Dispatch Dialog */}
      <Dialog open={showDispatch} onOpenChange={setShowDispatch}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ClipboardCheck className="h-5 w-5 text-primary" />
              Dispatch PI Assessment
            </DialogTitle>
          </DialogHeader>
          <DispatchForm
            onCancel={() => setShowDispatch(false)}
            onDispatched={() => {
              setShowDispatch(false);
              mutate();
            }}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* StatCard — standalone KPI card                                              */
/* -------------------------------------------------------------------------- */
function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: "blue" | "green" | "purple" | "amber";
}) {
  const valClass =
    accent === "blue"
      ? "text-blue-600"
      : accent === "green"
      ? "text-emerald-600"
      : accent === "purple"
      ? "text-violet-600"
      : accent === "amber"
      ? "text-amber-600"
      : "text-foreground";

  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4 shadow-card">
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 text-3xl font-extrabold tabular-nums leading-none ${valClass}`}>
        {value}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* DispatchForm — rendered inside the Dialog                                   */
/* -------------------------------------------------------------------------- */
function DispatchForm({
  onCancel,
  onDispatched,
}: {
  onCancel: () => void;
  onDispatched: () => void;
}) {
  const [applicationId, setApplicationId] = useState("");
  const [behavioral, setBehavioral] = useState(true);
  const [cognitive, setCognitive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function dispatch() {
    setBusy(true);
    setErr(null);
    try {
      const kinds: AssessmentKind[] = [];
      if (behavioral) kinds.push("behavioral");
      if (cognitive) kinds.push("cognitive");
      await assessments.dispatch({
        application_id: applicationId.trim(),
        kinds: kinds.length ? kinds : undefined,
      });
      onDispatched();
    } catch (e: any) {
      setErr(e?.message ?? "dispatch failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label>Application ID</Label>
        <Input
          value={applicationId}
          onChange={(e) => setApplicationId(e.target.value)}
          placeholder="UUID"
        />
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={behavioral}
            onChange={(e) => setBehavioral(e.target.checked)}
            className="rounded"
          />
          Behavioral
        </label>
        <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={cognitive}
            onChange={(e) => setCognitive(e.target.checked)}
            className="rounded"
          />
          Cognitive
        </label>
      </div>
      {err ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {err}
        </p>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          onClick={dispatch}
          disabled={busy || !applicationId.trim() || (!behavioral && !cognitive)}
        >
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Send invite
        </Button>
      </div>
    </div>
  );
}
