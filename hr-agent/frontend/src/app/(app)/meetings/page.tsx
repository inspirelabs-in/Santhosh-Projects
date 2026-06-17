"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Video,
  Loader2,
  Plus,
  ArrowRight,
  ArrowLeft,
  Search,
  RefreshCw,
  Download,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Sparkles,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { ScoreBar } from "@/components/tier-badge";
import {
  meetings,
  type MeetingListItem,
  type MeetingRound,
} from "@/lib/api/agentic";
import { fmtRelative, fmtDate } from "@/lib/utils";

type TabKey = "upcoming" | "live" | "completed" | "all";

const LIVE_STATUSES = new Set(["joining", "in_call", "recording", "live"]);
const COMPLETED_STATUSES = new Set([
  "completed",
  "scored",
  "done",
  "analyzed",
]);

export default function MeetingsPage() {
  const [view, setView] = useState<"list" | "schedule" | "ai-schedule">(
    "list",
  );
  const { data, error, isLoading, mutate } = useSWR<MeetingListItem[]>(
    "/agentic/meetings",
    () => meetings.list({ limit: 200 }),
    { refreshInterval: 30_000 },
  );

  const [tab, setTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [roundFilter, setRoundFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const stats = useMemo(() => {
    const rows = data ?? [];
    const now = Date.now();
    const dayMs = 24 * 60 * 60 * 1000;
    let today = 0;
    let week = 0;
    let live = 0;
    let completed = 0;
    let scoreSum = 0;
    let scoreCount = 0;
    const byRound = { technical: 0, ceo: 0 };
    for (const r of rows) {
      if (LIVE_STATUSES.has(r.bot_status)) live++;
      if (COMPLETED_STATUSES.has(r.bot_status)) completed++;
      if (r.scheduled_at) {
        const t = new Date(r.scheduled_at).getTime();
        if (t >= now - dayMs && t <= now + dayMs) today++;
        if (t >= now - 7 * dayMs && t <= now + 7 * dayMs) week++;
      }
      if (r.overall_score != null) {
        scoreSum += r.overall_score;
        scoreCount++;
      }
      if (r.round in byRound) byRound[r.round]++;
    }
    return {
      total: rows.length,
      today,
      week,
      live,
      completed,
      avgScore: scoreCount ? scoreSum / scoreCount : null,
      byRound,
    };
  }, [data]);

  const statusOptions = useMemo(() => {
    const set = new Set<string>();
    for (const r of data ?? []) set.add(r.bot_status);
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const now = Date.now();
    return (data ?? [])
      .filter((r) => {
        if (tab === "live" && !LIVE_STATUSES.has(r.bot_status)) return false;
        if (tab === "completed" && !COMPLETED_STATUSES.has(r.bot_status))
          return false;
        if (tab === "upcoming") {
          if (!r.scheduled_at) return false;
          const t = new Date(r.scheduled_at).getTime();
          if (t < now) return false;
          if (COMPLETED_STATUSES.has(r.bot_status)) return false;
        }
        if (roundFilter !== "all" && r.round !== roundFilter) return false;
        if (statusFilter !== "all" && r.bot_status !== statusFilter)
          return false;
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
      })
      .sort((a, b) => {
        const at = a.scheduled_at ? new Date(a.scheduled_at).getTime() : 0;
        const bt = b.scheduled_at ? new Date(b.scheduled_at).getTime() : 0;
        if (tab === "upcoming") return at - bt;
        return bt - at;
      });
  }, [data, tab, search, roundFilter, statusFilter]);

  function exportCsv() {
    const rows = [
      [
        "meeting_session_id",
        "application_id",
        "candidate",
        "role",
        "round",
        "bot_status",
        "verdict",
        "overall_score",
        "scheduled_at",
        "started_at",
        "duration_sec",
        "transcript_url",
      ],
      ...filtered.map((r) => [
        r.meeting_session_id,
        r.application_id,
        r.candidate_name ?? "",
        r.role_title ?? "",
        r.round,
        r.bot_status,
        r.verdict ?? "",
        r.overall_score?.toString() ?? "",
        r.scheduled_at ?? "",
        r.started_at ?? "",
        r.duration_sec?.toString() ?? "",
        r.transcript_url ?? "",
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
    a.download = `meetings-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (view === "schedule") {
    return (
      <>
        <Topbar title="Meetings" subtitle="Teams technical + CEO interviews" />
        <div className="flex-1 overflow-auto px-8 py-6">
          <ScheduleForm
            onCancel={() => setView("list")}
            onScheduled={() => {
              setView("list");
              mutate();
            }}
          />
        </div>
      </>
    );
  }

  if (view === "ai-schedule") {
    return (
      <>
        <Topbar title="Meetings" subtitle="Teams technical + CEO interviews" />
        <div className="flex-1 overflow-auto px-8 py-6">
          <AIScheduleForm
            onCancel={() => setView("list")}
            onScheduled={() => {
              setView("list");
              mutate();
            }}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title="Meetings" subtitle="Teams technical + CEO interviews" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        <KpiStrip stats={stats} />

        <div className="flex flex-wrap items-center gap-2">
          <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
            <TabsList>
              <TabsTrigger value="all">All ({stats.total})</TabsTrigger>
              <TabsTrigger value="upcoming">Upcoming</TabsTrigger>
              <TabsTrigger value="live">Live ({stats.live})</TabsTrigger>
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

          <Select value={roundFilter} onValueChange={setRoundFilter}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="Round" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All rounds</SelectItem>
              <SelectItem value="technical">Technical</SelectItem>
              <SelectItem value="ceo">CEO</SelectItem>
            </SelectContent>
          </Select>

          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Bot status" />
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
          <Button
            variant="outline"
            size="sm"
            onClick={() => setView("ai-schedule")}
          >
            <Sparkles className="mr-1 h-4 w-4" /> AI schedule
          </Button>
          <Button size="sm" onClick={() => setView("schedule")}>
            <Plus className="mr-1 h-4 w-4" /> Schedule meeting
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
              <Video className="h-8 w-8 text-primary" />
              <p className="text-base font-bold">No meetings match</p>
              <p className="max-w-md text-sm text-muted-foreground">
                {(data ?? []).length === 0
                  ? "Click Schedule meeting or AI schedule. Read.ai joins via the connected calendar — no on-demand bot dispatch."
                  : "Try clearing filters."}
              </p>
            </CardContent>
          </Card>
        ) : null}

        {filtered.length > 0 ? (
          <Card className="overflow-hidden">
            <div className="grid grid-cols-[2fr_100px_140px_140px_120px_120px_60px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              <span>Candidate · role</span>
              <span>Round</span>
              <span>Bot status</span>
              <span>Score</span>
              <span>Scheduled</span>
              <span>Created</span>
              <span />
            </div>
            {filtered.map((row) => {
              const open = expandedId === row.meeting_session_id;
              const isLive = LIVE_STATUSES.has(row.bot_status);
              return (
                <div
                  key={row.meeting_session_id}
                  className="border-b border-border"
                >
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedId(open ? null : row.meeting_session_id)
                    }
                    className={`grid w-full grid-cols-[2fr_100px_140px_140px_120px_120px_60px] items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-muted/40 ${
                      isLive ? "bg-destructive/5" : ""
                    }`}
                  >
                    <div className="min-w-0">
                      <p className="truncate font-semibold">
                        {isLive ? (
                          <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-destructive" />
                        ) : null}
                        {row.candidate_name ?? "Unnamed"}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {row.role_title ?? "—"}
                      </p>
                    </div>
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-center font-mono text-[10px] uppercase tracking-[0.15em] text-primary">
                      {row.round}
                    </span>
                    <StatusTag stage={`technical_meeting_${row.bot_status}`} />
                    <ScoreBar score={row.overall_score} />
                    <span className="text-xs text-muted-foreground">
                      {row.scheduled_at ? fmtRelative(row.scheduled_at) : "—"}
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
                  {open ? <RowDetail row={row} /> : null}
                </div>
              );
            })}
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
    today: number;
    week: number;
    live: number;
    completed: number;
    avgScore: number | null;
    byRound: { technical: number; ceo: number };
  };
}) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
      <Kpi label="Total" value={stats.total} />
      <Kpi label="Today (±24h)" value={stats.today} />
      <Kpi label="This week (±7d)" value={stats.week} />
      <Kpi
        label="Live"
        value={stats.live}
        accent={stats.live > 0 ? "live" : undefined}
      />
      <Kpi label="Completed" value={stats.completed} />
      <Kpi
        label="Avg score"
        value={stats.avgScore != null ? stats.avgScore.toFixed(1) : "—"}
      />
    </div>
  );
}

function Kpi({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: "live";
}) {
  return (
    <Card className={accent === "live" ? "border-destructive/40" : ""}>
      <CardContent className="p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </p>
        <p
          className={`mt-1 text-2xl font-bold tabular-nums ${
            accent === "live" ? "text-destructive" : ""
          }`}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function RowDetail({ row }: { row: MeetingListItem }) {
  return (
    <div className="grid grid-cols-1 gap-4 border-t border-border bg-muted/20 px-4 py-3 text-xs md:grid-cols-3">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Identifiers
        </p>
        <p className="mt-1 break-all">
          <span className="text-muted-foreground">session:</span>{" "}
          {row.meeting_session_id}
        </p>
        <p className="mt-1 break-all">
          <span className="text-muted-foreground">application:</span>{" "}
          {row.application_id}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">round:</span> {row.round}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">verdict:</span>{" "}
          {row.verdict ?? "—"}
        </p>
      </div>
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Timeline
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">scheduled:</span>{" "}
          {fmtDate(row.scheduled_at)}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">started:</span>{" "}
          {fmtDate(row.started_at)}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">duration:</span>{" "}
          {row.duration_sec != null
            ? `${Math.round(row.duration_sec / 60)} min`
            : "—"}
        </p>
        <p className="mt-1">
          <span className="text-muted-foreground">created:</span>{" "}
          {fmtDate(row.created_at)}
        </p>
      </div>
      <div className="flex flex-col items-start gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Artifacts
        </p>
        {row.transcript_url ? (
          <a
            href={row.transcript_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            Transcript <ExternalLink className="h-3 w-3" />
          </a>
        ) : (
          <span className="text-muted-foreground">No transcript yet</span>
        )}
        <Link
          href={`/candidates/${row.application_id}`}
          className="mt-auto inline-flex items-center gap-1 font-semibold text-primary hover:underline"
        >
          Open candidate <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

function ScheduleForm({
  onCancel,
  onScheduled,
}: {
  onCancel: () => void;
  onScheduled: () => void;
}) {
  const [applicationId, setApplicationId] = useState("");
  const [round, setRound] = useState<MeetingRound>("technical");
  const [joinUrl, setJoinUrl] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function dispatch() {
    setBusy(true);
    setErr(null);
    try {
      await meetings.dispatch({
        application_id: applicationId.trim(),
        round,
        teams_join_url: joinUrl.trim(),
        scheduled_at: new Date(scheduledAt).toISOString(),
      });
      onScheduled();
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
          <Video className="h-5 w-5 text-primary" />
          Schedule meeting
        </CardTitle>
        <CardDescription>
          Register the Teams meeting with the agent. Read.ai joins via its
          calendar integration on the organiser mailbox, records, transcribes,
          and posts the report to our webhook for scoring.
        </CardDescription>
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Round</Label>
            <Select
              value={round}
              onValueChange={(v) => setRound(v as MeetingRound)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="technical">Technical</SelectItem>
                <SelectItem value="ceo">CEO</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Scheduled at</Label>
            <Input
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Teams join URL</Label>
          <Input
            value={joinUrl}
            onChange={(e) => setJoinUrl(e.target.value)}
            placeholder="https://teams.microsoft.com/l/meetup-join/…"
          />
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
              !joinUrl.trim() ||
              !scheduledAt
            }
          >
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Register meeting
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AIScheduleForm({
  onCancel,
  onScheduled,
}: {
  onCancel: () => void;
  onScheduled: () => void;
}) {
  const [applicationId, setApplicationId] = useState("");
  const [round, setRound] = useState<"technical" | "ceo" | "hr">("technical");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      await meetings.aiSchedule({
        application_id: applicationId.trim(),
        round,
      });
      onScheduled();
    } catch (e: any) {
      setErr(e?.message ?? "ai-schedule failed");
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
          <Sparkles className="h-5 w-5 text-primary" />
          AI auto-schedule
        </CardTitle>
        <CardDescription>
          Agent picks slot, creates Teams meeting on the organiser calendar,
          emails candidate + panel. Read.ai auto-joins via calendar integration.
          No URL or time needed.
        </CardDescription>
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
        <div className="space-y-1.5">
          <Label>Round</Label>
          <Select value={round} onValueChange={(v) => setRound(v as any)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="technical">Technical</SelectItem>
              <SelectItem value="ceo">CEO</SelectItem>
              <SelectItem value="hr">HR</SelectItem>
            </SelectContent>
          </Select>
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
          <Button onClick={go} disabled={busy || !applicationId.trim()}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Auto-schedule
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
