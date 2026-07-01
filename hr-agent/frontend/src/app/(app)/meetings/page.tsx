"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Video,
  Loader2,
  Plus,
  ArrowRight,
  Search,
  ChevronRight,
  ExternalLink,
  Sparkles,
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
import { ScoreBar } from "@/components/tier-badge";
import {
  meetings,
  type MeetingListItem,
  type MeetingRound,
  type MeetingTranscript,
  type TranscriptBlock,
} from "@/lib/api/agentic";
import { MarkdownLite } from "@/components/markdown-lite";
import { fmtRelative, fmtDate } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Helpers                                                                     */
/* -------------------------------------------------------------------------- */

function blockSpeaker(b: TranscriptBlock): string {
  if (typeof b.speaker === "string") return b.speaker || "Speaker";
  if (b.speaker && typeof b.speaker === "object") return b.speaker.name || "Speaker";
  return "Speaker";
}
function blockText(b: TranscriptBlock): string {
  return (b.text || b.words || b.transcript || "").trim();
}

/* -------------------------------------------------------------------------- */
/* TranscriptViewer                                                            */
/* -------------------------------------------------------------------------- */

function TranscriptViewer({ meetingSessionId }: { meetingSessionId: string }) {
  const { data, error, isLoading } = useSWR<MeetingTranscript>(
    `/agentic/meetings/${meetingSessionId}/transcript`,
    () => meetings.transcript(meetingSessionId),
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading transcript…
      </div>
    );
  }
  if (error) {
    return (
      <div className="py-3 text-xs text-destructive">
        Could not load transcript: {error.message}
      </div>
    );
  }
  if (!data) return null;

  const blocks = (data.transcript || []).filter((b) => blockText(b));
  const isSummaryOnly =
    blocks.length === 1 && blockSpeaker(blocks[0]).toLowerCase().includes("summary");

  return (
    <div className="space-y-4">
      {/* Scores + verdict */}
      <div className="flex flex-wrap items-center gap-2">
        {data.verdict ? (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-primary">
            {data.verdict.replaceAll("_", " ")}
          </span>
        ) : null}
        {(["technical", "communication", "confidence", "overall"] as const).map((k) =>
          data.scores[k] != null ? (
            <span
              key={k}
              className="rounded-md bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              {k}: <span className="font-semibold text-foreground">{data.scores[k]}</span>
            </span>
          ) : null,
        )}
      </div>

      {/* LLM report */}
      {data.llm_report ? (
        <div>
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Interview report
          </p>
          <div className="rounded-md bg-muted/40 p-3 text-sm leading-relaxed">
            <MarkdownLite source={data.llm_report} />
          </div>
        </div>
      ) : null}

      {/* Transcript */}
      <div>
        <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {isSummaryOnly ? "Summary" : "Transcript"}
        </p>
        {blocks.length === 0 ? (
          <p className="text-xs text-muted-foreground">No transcript captured for this meeting.</p>
        ) : (
          <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
            {blocks.map((b, i) => (
              <div key={i} className="text-sm leading-relaxed">
                <span className="font-semibold text-primary">{blockSpeaker(b)}: </span>
                <span className="text-foreground/90">{blockText(b)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Types & constants                                                           */
/* -------------------------------------------------------------------------- */

type TabKey = "upcoming" | "live" | "completed" | "all";

const LIVE_STATUSES = new Set(["joining", "in_call", "recording", "live"]);
const COMPLETED_STATUSES = new Set(["completed", "scored", "done", "analyzed"]);

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

export default function MeetingsPage() {
  const { data, error, isLoading, mutate } = useSWR<MeetingListItem[]>(
    "/agentic/meetings",
    () => meetings.list({ limit: 200 }),
    { refreshInterval: 30_000 },
  );

  const [tab, setTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [roundFilter, setRoundFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selected, setSelected] = useState<MeetingListItem | null>(null);
  const [showSchedule, setShowSchedule] = useState(false);
  const [showAiSchedule, setShowAiSchedule] = useState(false);

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
        if (tab === "completed" && !COMPLETED_STATUSES.has(r.bot_status)) return false;
        if (tab === "upcoming") {
          if (!r.scheduled_at) return false;
          const t = new Date(r.scheduled_at).getTime();
          if (t < now) return false;
          if (COMPLETED_STATUSES.has(r.bot_status)) return false;
        }
        if (roundFilter !== "all" && r.round !== roundFilter) return false;
        if (statusFilter !== "all" && r.bot_status !== statusFilter) return false;
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

  return (
    <>
      <Topbar title="Meetings" subtitle="Teams technical + CEO interviews" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-5">

        {/* KPI Strip — single horizontal card */}
        <div className="flex items-stretch bg-card rounded-xl shadow-card overflow-hidden">
          <KpiItem label="Total" value={stats.total} />
          <KpiItem label="Today ±24h" value={stats.today} accent="blue" />
          <KpiItem label="This week" value={stats.week} accent="purple" />
          <KpiItem
            label="Live"
            value={stats.live}
            accent={stats.live > 0 ? "live" : undefined}
          />
          <KpiItem label="Completed" value={stats.completed} accent="green" />
          <KpiItem
            label="Avg score"
            value={stats.avgScore != null ? stats.avgScore.toFixed(1) : "—"}
          />
          <div className="flex flex-col justify-center px-5 py-4 border-l border-border">
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground whitespace-nowrap mb-2">
              By Round
            </p>
            <div className="flex gap-4 font-mono text-[11px]">
              <span className="text-primary font-semibold">
                Tech {stats.byRound.technical}
              </span>
              <span className="text-muted-foreground">
                CEO {stats.byRound.ceo}
              </span>
            </div>
          </div>
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Segment control */}
          <div className="flex rounded-lg bg-muted p-1 gap-0.5">
            {(["all", "upcoming", "live", "completed"] as TabKey[]).map((t) => (
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
                  : t === "upcoming"
                  ? "Upcoming"
                  : t === "live"
                  ? `Live (${stats.live})`
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

          {/* Round filter */}
          <Select value={roundFilter} onValueChange={setRoundFilter}>
            <SelectTrigger className="w-36 h-8 text-xs">
              <SelectValue placeholder="Round" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All rounds</SelectItem>
              <SelectItem value="technical">Technical</SelectItem>
              <SelectItem value="ceo">CEO</SelectItem>
            </SelectContent>
          </Select>

          {/* Status filter */}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-40 h-8 text-xs">
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

          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => setShowAiSchedule(true)}
          >
            <Sparkles className="mr-1 h-3.5 w-3.5" /> AI schedule
          </Button>
          <Button size="sm" className="h-8" onClick={() => setShowSchedule(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Schedule meeting
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
          <div className="grid grid-cols-[2fr_100px_150px_140px_120px_120px_36px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground sticky top-0 backdrop-blur z-10">
            <span>Candidate · Role</span>
            <span>Round</span>
            <span>Bot status</span>
            <span>Score</span>
            <span>Scheduled</span>
            <span>Created</span>
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
              <Video className="h-10 w-10 text-primary" />
              <p className="text-base font-bold">No meetings match</p>
              <p className="max-w-md text-sm text-muted-foreground">
                {(data ?? []).length === 0
                  ? "Click Schedule meeting or AI schedule. Read.ai joins via the connected calendar — no on-demand bot dispatch."
                  : "Try clearing filters or switching tabs."}
              </p>
            </div>
          ) : null}

          {/* Rows */}
          {!isLoading && filtered.length > 0 ? (
            <div>
              {filtered.map((row) => {
                const isLive = LIVE_STATUSES.has(row.bot_status);
                return (
                  <div key={row.meeting_session_id} className="border-b border-border last:border-0">
                    <button
                      type="button"
                      onClick={() => setSelected(row)}
                      className={`grid w-full grid-cols-[2fr_100px_150px_140px_120px_120px_36px] items-center gap-3 px-4 py-3 text-left text-sm transition cursor-pointer hover:bg-muted/30 ${
                        isLive ? "bg-destructive/5" : ""
                      }`}
                    >
                      {/* Candidate + role */}
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-sm">
                          {isLive ? (
                            <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-destructive" />
                          ) : null}
                          {row.candidate_name ?? "Unnamed"}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {row.role_title ?? "—"}
                        </p>
                      </div>

                      {/* Round */}
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-center font-mono text-[10px] uppercase tracking-[0.15em] text-primary w-fit">
                        {row.round}
                      </span>

                      {/* Status */}
                      <StatusTag stage={`technical_meeting_${row.bot_status}`} />

                      {/* Score */}
                      <ScoreBar score={row.overall_score} />

                      {/* Scheduled */}
                      <span className="text-xs text-muted-foreground">
                        {row.scheduled_at ? fmtRelative(row.scheduled_at) : "—"}
                      </span>

                      {/* Created */}
                      <span className="text-xs text-muted-foreground">
                        {fmtRelative(row.created_at)}
                      </span>

                      {/* Trailing affordance */}
                      <div className="flex justify-end">
                        <ChevronRight className="h-4 w-4 text-muted-foreground/40 transition group-hover:text-muted-foreground" />
                      </div>
                    </button>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      {/* Row detail Sheet */}
      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent width="lg">
          {selected && (
            <MeetingDetail key={selected.meeting_session_id} row={selected} />
          )}
        </SheetContent>
      </Sheet>

      {/* Schedule meeting Dialog */}
      <Dialog open={showSchedule} onOpenChange={setShowSchedule}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Video className="h-5 w-5 text-primary" />
              Schedule meeting
            </DialogTitle>
          </DialogHeader>
          <ScheduleForm
            onCancel={() => setShowSchedule(false)}
            onScheduled={() => {
              setShowSchedule(false);
              mutate();
            }}
          />
        </DialogContent>
      </Dialog>

      {/* AI schedule Dialog */}
      <Dialog open={showAiSchedule} onOpenChange={setShowAiSchedule}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              AI auto-schedule
            </DialogTitle>
          </DialogHeader>
          <AIScheduleForm
            onCancel={() => setShowAiSchedule(false)}
            onScheduled={() => {
              setShowAiSchedule(false);
              mutate();
            }}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* KpiItem — single stat cell in the horizontal strip                         */
/* -------------------------------------------------------------------------- */

function KpiItem({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: "blue" | "green" | "purple" | "amber" | "live";
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
      : accent === "live"
      ? "text-destructive"
      : "text-foreground";

  return (
    <div className="flex flex-col justify-center px-5 py-4 border-r border-border">
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground whitespace-nowrap">
        {label}
      </p>
      <p className={`mt-0.5 text-2xl font-extrabold tabular-nums leading-none ${valClass}`}>
        {value}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* MeetingDetail — rendered inside the Sheet                                  */
/* -------------------------------------------------------------------------- */

function MeetingDetail({ row }: { row: MeetingListItem }) {
  const [showTranscript, setShowTranscript] = useState(false);
  const hasTranscript = !!row.transcript_url;
  const isLive = LIVE_STATUSES.has(row.bot_status);

  return (
    <>
      <SheetHeader>
        <SheetTitle className="flex items-center gap-2">
          {isLive ? (
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-destructive" />
          ) : null}
          {row.candidate_name ?? "Unnamed"}
        </SheetTitle>
        <SheetDescription>
          {row.role_title ?? "—"} · {row.round} round · {row.bot_status}
          {row.scheduled_at ? ` · ${fmtDate(row.scheduled_at)}` : ""}
        </SheetDescription>
      </SheetHeader>

      <SheetBody className="space-y-6">
        {/* Score + verdict row */}
        {(row.overall_score != null || row.verdict) ? (
          <div className="flex flex-wrap items-center gap-2">
            {row.verdict ? (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-primary">
                {row.verdict.replaceAll("_", " ")}
              </span>
            ) : null}
            {row.overall_score != null ? (
              <span className="font-mono text-sm font-bold tabular-nums">
                Score: {row.overall_score.toFixed(1)}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="border-t border-border" />

        {/* Identifiers */}
        <div className="space-y-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Identifiers
          </p>
          <div className="space-y-1.5 text-xs">
            <p className="break-all">
              <span className="text-muted-foreground">session:</span>{" "}
              <span className="font-mono">{row.meeting_session_id}</span>
            </p>
            <p className="break-all">
              <span className="text-muted-foreground">application:</span>{" "}
              <span className="font-mono">{row.application_id}</span>
            </p>
            <p>
              <span className="text-muted-foreground">round:</span> {row.round}
            </p>
          </div>
        </div>

        <div className="border-t border-border" />

        {/* Timeline */}
        <div className="space-y-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Timeline
          </p>
          <div className="space-y-1.5 text-xs">
            <p>
              <span className="text-muted-foreground">scheduled:</span>{" "}
              {fmtDate(row.scheduled_at)}
            </p>
            <p>
              <span className="text-muted-foreground">started:</span>{" "}
              {fmtDate(row.started_at)}
            </p>
            <p>
              <span className="text-muted-foreground">duration:</span>{" "}
              {row.duration_sec != null
                ? `${Math.round(row.duration_sec / 60)} min`
                : "—"}
            </p>
            <p>
              <span className="text-muted-foreground">created:</span>{" "}
              {fmtDate(row.created_at)}
            </p>
          </div>
        </div>

        <div className="border-t border-border" />

        {/* Artifacts */}
        <div className="space-y-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Artifacts
          </p>
          <div className="space-y-2">
            {hasTranscript ? (
              <>
                <button
                  type="button"
                  onClick={() => setShowTranscript((v) => !v)}
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  {showTranscript ? "Hide transcript & report" : "View transcript & report"}
                </button>
                <a
                  href={row.transcript_url!}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Raw JSON <ExternalLink className="h-3 w-3" />
                </a>
              </>
            ) : (
              <span className="text-xs text-muted-foreground">No transcript yet</span>
            )}
          </div>

          {showTranscript ? (
            <div className="mt-3 rounded-lg border border-border bg-muted/20 p-4">
              <TranscriptViewer meetingSessionId={row.meeting_session_id} />
            </div>
          ) : null}
        </div>
      </SheetBody>

      <SheetFooter className="flex justify-end gap-2">
        <Link
          href={`/candidates/${row.application_id}`}
          className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline"
        >
          Open candidate <ArrowRight className="h-4 w-4" />
        </Link>
      </SheetFooter>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* ScheduleForm — rendered inside Dialog                                      */
/* -------------------------------------------------------------------------- */

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
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Register the Teams meeting with the agent. Read.ai joins via its calendar
        integration on the organiser mailbox, records, transcribes, and posts the
        report to our webhook for scoring.
      </p>
      <div className="space-y-1.5">
        <Label>Application ID</Label>
        <Input
          value={applicationId}
          onChange={(e) => setApplicationId(e.target.value)}
          placeholder="UUID"
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Round</Label>
          <Select value={round} onValueChange={(v) => setRound(v as MeetingRound)}>
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
          disabled={busy || !applicationId.trim() || !joinUrl.trim() || !scheduledAt}
        >
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Register meeting
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* AIScheduleForm — rendered inside Dialog                                    */
/* -------------------------------------------------------------------------- */

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
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Agent picks slot, creates Teams meeting on the organiser calendar, emails
        candidate + panel. Read.ai auto-joins via calendar integration. No URL or
        time needed.
      </p>
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
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {err}
        </p>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button onClick={go} disabled={busy || !applicationId.trim()}>
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Auto-schedule
        </Button>
      </div>
    </div>
  );
}
