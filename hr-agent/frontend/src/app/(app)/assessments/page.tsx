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
  Download,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  RefreshCw,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusTag } from "@/components/status-tag";
import {
  assessments,
  type AssessmentListItem,
  type AssessmentKind,
} from "@/lib/api/agentic";
import { fmtRelative, fmtDate } from "@/lib/utils";

const BAND_CLASS = {
  green: "bg-success/15 text-success",
  amber: "bg-warning/15 text-foreground",
  red: "bg-destructive/15 text-destructive",
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
  const [expandedId, setExpandedId] = useState<string | null>(null);
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

  function exportCsv() {
    const rows = [
      [
        "assessment_id",
        "application_id",
        "candidate",
        "role",
        "kind",
        "provider",
        "status",
        "fit_band",
        "percentile",
        "normalized_score",
        "invite_sent_at",
        "completed_at",
        "created_at",
      ],
      ...filtered.map((r) => [
        r.assessment_id,
        r.application_id,
        r.candidate_name ?? "",
        r.role_title ?? "",
        r.kind ?? "",
        r.provider,
        r.status,
        r.fit_band ?? "",
        r.percentile?.toString() ?? "",
        r.normalized_score?.toString() ?? "",
        r.invite_sent_at ?? "",
        r.completed_at ?? "",
        r.created_at,
      ]),
    ];
    const csv = rows
      .map((row) =>
        row
          .map((c) => {
            const s = String(c ?? "");
            return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
          })
          .join(","),
      )
      .join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `assessments-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (showDispatch) {
    return (
      <>
        <Topbar
          title="Assessments"
          subtitle="Take-home Assignments"
        />
        <div className="flex-1 overflow-auto px-8 py-6">
          <DispatchForm
            onCancel={() => setShowDispatch(false)}
            onDispatched={() => {
              setShowDispatch(false);
              mutate();
            }}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        title="Assessments"
        subtitle="Take-home Assignments"
      />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        <KpiStrip stats={stats} />

        <div className="flex flex-wrap items-center gap-2">
          <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
            <TabsList>
              <TabsTrigger value="all">All ({stats.total})</TabsTrigger>
              <TabsTrigger value="active">Active ({stats.active})</TabsTrigger>
              <TabsTrigger value="completed">
                Completed ({stats.completed})
              </TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="relative ml-auto w-72">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search candidate, role, app id"
              className="pl-8"
            />
          </div>

          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-36">
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

          <Select value={kindFilter} onValueChange={setKindFilter}>
            <SelectTrigger className="w-36">
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

          <Select value={bandFilter} onValueChange={setBandFilter}>
            <SelectTrigger className="w-32">
              <SelectValue placeholder="Band" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All bands</SelectItem>
              <SelectItem value="green">Green</SelectItem>
              <SelectItem value="amber">Amber</SelectItem>
              <SelectItem value="red">Red</SelectItem>
            </SelectContent>
          </Select>

          <Button variant="outline" size="sm" onClick={() => mutate()}>
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={exportCsv}
            disabled={filtered.length === 0}
          >
            <Download className="mr-1 h-4 w-4" /> CSV
          </Button>
          <Button size="sm" onClick={() => setShowDispatch(true)}>
            <Plus className="mr-1 h-4 w-4" /> Dispatch
          </Button>
        </div>

        {error ? (
          <Card className="border-destructive/30 bg-destructive/5">
            <CardContent className="p-3 text-sm text-destructive">
              {error.message}
            </CardContent>
          </Card>
        ) : null}

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-16 rounded-lg skeleton" />
            ))}
          </div>
        ) : null}

        {!isLoading && filtered.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <ClipboardCheck className="h-8 w-8 text-primary" />
              <p className="text-base font-bold">No assessments match</p>
              <p className="max-w-md text-sm text-muted-foreground">
                {(data ?? []).length === 0
                  ? "Send a PI invite from a candidate page or use Dispatch above."
                  : "Try clearing filters."}
              </p>
            </CardContent>
          </Card>
        ) : null}

        {filtered.length > 0 ? (
          <Card className="overflow-hidden">
            <div className="grid grid-cols-[2fr_120px_140px_110px_120px_120px_60px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              <span>Candidate · role</span>
              <span>Kind</span>
              <span>Status</span>
              <span>Band</span>
              <span>Percentile</span>
              <span>Created</span>
              <span />
            </div>
            <div>
              {filtered.map((row) => {
                const open = expandedId === row.assessment_id;
                return (
                  <div
                    key={row.assessment_id}
                    className="border-b border-border"
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedId(open ? null : row.assessment_id)
                      }
                      className="grid w-full grid-cols-[2fr_120px_140px_110px_120px_120px_60px] items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-muted/40"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-semibold">
                          {row.candidate_name ?? "Unnamed"}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {row.role_title ?? "—"}
                        </p>
                      </div>
                      <span className="font-mono text-xs uppercase tracking-[0.1em] text-muted-foreground">
                        {row.kind ?? row.provider}
                      </span>
                      <StatusTag stage={`assessment_${row.status}`} />
                      {row.fit_band ? (
                        <span
                          className={`rounded-full px-2 py-0.5 text-center font-mono text-[10px] uppercase tracking-[0.15em] ${BAND_CLASS[row.fit_band]}`}
                        >
                          {row.fit_band}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                      <span className="font-mono text-sm font-semibold tabular-nums">
                        {row.percentile != null
                          ? `${Math.round(row.percentile)}%`
                          : row.normalized_score != null
                            ? row.normalized_score.toFixed(1)
                            : "—"}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {fmtRelative(row.created_at)}
                      </span>
                      {open ? (
                        <ChevronUp className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-muted-foreground" />
                      )}
                    </button>
                    {open ? (
                      <RowDetail row={row} />
                    ) : null}
                  </div>
                );
              })}
            </div>
          </Card>
        ) : null}
      </div>
    </>
  );
}

function KpiStrip({
  stats,
}: {
  stats: {
    total: number;
    active: number;
    completed: number;
    avgPct: number | null;
    bands: Record<Band, number>;
  };
}) {
  const bandTotal =
    stats.bands.green + stats.bands.amber + stats.bands.red || 1;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      <Kpi label="Total" value={stats.total} />
      <Kpi label="Active" value={stats.active} />
      <Kpi label="Completed" value={stats.completed} />
      <Kpi
        label="Avg percentile"
        value={stats.avgPct != null ? `${stats.avgPct}%` : "—"}
      />
      <Card>
        <CardContent className="p-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Band mix
          </p>
          <div className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="bg-success"
              style={{ width: `${(stats.bands.green / bandTotal) * 100}%` }}
            />
            <div
              className="bg-warning"
              style={{ width: `${(stats.bands.amber / bandTotal) * 100}%` }}
            />
            <div
              className="bg-destructive"
              style={{ width: `${(stats.bands.red / bandTotal) * 100}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground">
            <span>G {stats.bands.green}</span>
            <span>A {stats.bands.amber}</span>
            <span>R {stats.bands.red}</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </p>
        <p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
}

function RowDetail({ row }: { row: AssessmentListItem }) {
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-border bg-muted/20 px-4 py-3 text-xs md:grid-cols-3">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Identifiers
        </p>
        <p className="mt-1 break-all">
          <span className="text-muted-foreground">assessment:</span>{" "}
          {row.assessment_id}
        </p>
        <p className="mt-1 break-all">
          <span className="text-muted-foreground">application:</span>{" "}
          {row.application_id}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">provider:</span> {row.provider}
        </p>
      </div>
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Timeline
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">created:</span>{" "}
          {fmtDate(row.created_at)}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">invite sent:</span>{" "}
          {fmtDate(row.invite_sent_at)}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">completed:</span>{" "}
          {fmtDate(row.completed_at)}
        </p>
      </div>
      <div className="flex flex-col items-start gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Scores
        </p>
        <p>
          <span className="text-muted-foreground">percentile:</span>{" "}
          <span className="font-mono font-semibold">
            {row.percentile != null ? `${Math.round(row.percentile)}%` : "—"}
          </span>
        </p>
        <p>
          <span className="text-muted-foreground">normalized:</span>{" "}
          <span className="font-mono font-semibold">
            {row.normalized_score != null
              ? row.normalized_score.toFixed(2)
              : "—"}
          </span>
        </p>
        <Link
          href={`/candidates/${row.application_id}`}
          className="mt-auto inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
        >
          Open candidate <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

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
    <Card className="max-w-2xl">
      <CardHeader>
        <button
          type="button"
          onClick={onCancel}
          className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3 w-3" /> Back to list
        </button>
        <CardTitle className="flex items-center gap-2 text-xl">
          <ClipboardCheck className="h-5 w-5 text-primary" />
          Dispatch PI assessment
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label>Application ID</Label>
          <Input
            value={applicationId}
            onChange={(e) => setApplicationId(e.target.value)}
            placeholder="UUID"
          />
        </div>
        <div className="flex flex-wrap gap-3">
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={behavioral}
              onChange={(e) => setBehavioral(e.target.checked)}
            />
            Behavioral
          </label>
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cognitive}
              onChange={(e) => setCognitive(e.target.checked)}
            />
            Cognitive
          </label>
        </div>
        {err ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            onClick={dispatch}
            disabled={
              busy ||
              !applicationId.trim() ||
              (!behavioral && !cognitive)
            }
          >
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Send invite
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
