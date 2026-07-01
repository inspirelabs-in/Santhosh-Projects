"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  PhoneCall,
  ExternalLink,
  RotateCw,
  CalendarClock,
  CheckCircle2,
  PhoneOff,
  Loader2,
  PhoneIncoming,
  PhoneOutgoing,
  Voicemail,
  Tag,
  FileText,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Volume2,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusTag } from "@/components/status-tag";
import { ScoreBar } from "@/components/tier-badge";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetBody,
  SheetFooter,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  voiceCalls,
  type VoiceCallListItem,
  type CallKind,
  CALL_KIND_LABELS,
} from "@/lib/api/agentic";
import { fmtRelative } from "@/lib/utils";

type StatusFilter = "all" | "live" | "callback" | "completed" | "failed" | "voicemail";

const STATUS_FILTER_LABELS: Record<StatusFilter, string> = {
  all: "All",
  live: "Live / dialing",
  callback: "Callback queued",
  completed: "Completed",
  failed: "Failed / no answer",
  voicemail: "Voicemail",
};

const LIVE_STATUSES = new Set(["pending", "dialing", "in_progress"]);
const FAIL_STATUSES = new Set(["failed", "no_answer", "declined"]);

const KIND_ICONS: Record<CallKind, typeof PhoneCall> = {
  screening: PhoneOutgoing,
  confirmation: CheckCircle2,
  meeting_schedule: CalendarClock,
  status_update: Tag,
  joining_details: CheckCircle2,
  general_query: PhoneIncoming,
};

function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export default function VoiceCallsPage() {
  const { data, error, isLoading, mutate } = useSWR<VoiceCallListItem[]>(
    "/agentic/voice-calls",
    () => voiceCalls.list({ limit: 200 }),
    { refreshInterval: 15_000 },
  );
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [kindFilter, setKindFilter] = useState<CallKind | "all">("all");
  const [redialing, setRedialing] = useState<string | null>(null);
  const [selected, setSelected] = useState<VoiceCallListItem | null>(null);

  const stats = useMemo(() => {
    const all = data ?? [];
    return {
      total: all.length,
      live: all.filter((r) => LIVE_STATUSES.has(r.status)).length,
      callback: all.filter((r) => r.status === "callback_requested").length,
      completed: all.filter((r) => r.status === "completed").length,
      failed: all.filter((r) => FAIL_STATUSES.has(r.status)).length,
      voicemail: all.filter((r) => r.status === "voicemail").length,
      passed: all.filter((r) => r.verdict === "clear_pass").length,
      inbound: all.filter((r) => r.call_kind === "general_query").length,
    };
  }, [data]);

  const filtered = useMemo(() => {
    let all = data ?? [];
    if (kindFilter !== "all") {
      all = all.filter((r) => r.call_kind === kindFilter);
    }
    if (statusFilter === "all") return all;
    if (statusFilter === "live") return all.filter((r) => LIVE_STATUSES.has(r.status));
    if (statusFilter === "callback") return all.filter((r) => r.status === "callback_requested");
    if (statusFilter === "completed") return all.filter((r) => r.status === "completed");
    if (statusFilter === "failed") return all.filter((r) => FAIL_STATUSES.has(r.status));
    if (statusFilter === "voicemail") return all.filter((r) => r.status === "voicemail");
    return all;
  }, [data, statusFilter, kindFilter]);

  const kindCounts = useMemo(() => {
    const all = data ?? [];
    const counts: Record<string, number> = {};
    for (const r of all) {
      const k = r.call_kind || "screening";
      counts[k] = (counts[k] || 0) + 1;
    }
    return counts;
  }, [data]);

  async function redial(applicationId: string, callKind: CallKind = "screening") {
    setRedialing(applicationId);
    try {
      if (callKind === "screening") {
        await voiceCalls.dispatch({ application_id: applicationId });
      } else {
        await voiceCalls.dispatchCall({ application_id: applicationId, call_kind: callKind });
      }
      await mutate();
    } catch (e: any) {
      alert(`Re-dial failed: ${e?.message ?? "unknown"}`);
    } finally {
      setRedialing(null);
    }
  }

  return (
    <>
      <Topbar title="Voice calls" subtitle="all outbound & inbound AI calls" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        {error ? (
          <Card className="border-destructive/30 bg-destructive/5">
            <CardContent className="p-3 text-sm text-destructive">
              {error.message}
            </CardContent>
          </Card>
        ) : null}

        {/* KPI Strip — single horizontal card with dividers */}
        <div className="flex items-center gap-0 bg-card rounded-xl shadow-card overflow-hidden">
          <KpiItem label="Total" v={stats.total} icon={PhoneCall} />
          <KpiItem label="Live" v={stats.live} icon={Loader2} iconClass="text-blue-500" />
          <KpiItem label="Callback" v={stats.callback} icon={CalendarClock} iconClass="text-amber-500" />
          <KpiItem label="Completed" v={stats.completed} icon={CheckCircle2} iconClass="text-green-600" />
          <KpiItem label="Failed" v={stats.failed} icon={PhoneOff} iconClass="text-destructive" />
          <KpiItem label="Voicemail" v={stats.voicemail} icon={Voicemail} iconClass="text-amber-500" />
          <KpiItem label="Passed" v={stats.passed} icon={CheckCircle2} iconClass="text-emerald-600" />
          <KpiItem label="Inbound" v={stats.inbound} icon={PhoneIncoming} iconClass="text-blue-500" last />
        </div>

        {/* Filter section — two rows with labels */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground w-12">
              Type:
            </span>
            <FilterChip
              label={`All (${data?.length ?? 0})`}
              active={kindFilter === "all"}
              onClick={() => setKindFilter("all")}
            />
            {(Object.keys(CALL_KIND_LABELS) as CallKind[]).map((k) => (
              <FilterChip
                key={k}
                label={`${CALL_KIND_LABELS[k]} (${kindCounts[k] || 0})`}
                active={kindFilter === k}
                onClick={() => setKindFilter(k)}
              />
            ))}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground w-12">
              Status:
            </span>
            {(Object.keys(STATUS_FILTER_LABELS) as StatusFilter[]).map((k) => (
              <FilterChip
                key={k}
                label={STATUS_FILTER_LABELS[k]}
                active={statusFilter === k}
                onClick={() => setStatusFilter(k)}
              />
            ))}
          </div>
        </div>

        {/* Data table */}
        <div className="bg-card rounded-xl shadow-card overflow-hidden">
          {/* Sticky header */}
          <div className="sticky top-0 z-10 bg-muted/50 backdrop-blur border-b border-border">
            <div className="grid grid-cols-[2fr_1.5fr_130px_160px_120px_80px_100px_100px] px-4 py-2">
              {["Candidate", "Role", "Type", "Status", "Score", "Duration", "Time", "Actions"].map(
                (col) => (
                  <span
                    key={col}
                    className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground"
                  >
                    {col}
                  </span>
                ),
              )}
            </div>
          </div>

          {/* Loading skeleton */}
          {isLoading ? (
            <div className="space-y-2 p-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-14 rounded skeleton" />
              ))}
            </div>
          ) : null}

          {/* Empty state */}
          {!isLoading && data && filtered.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center px-6">
              <PhoneCall className="h-10 w-10 text-primary" />
              <p className="text-base font-bold">
                {data.length === 0 ? "No voice calls yet" : "No calls matching filters"}
              </p>
              <p className="max-w-md text-sm text-muted-foreground">
                Calls are dispatched automatically when candidates progress through the pipeline, or
                you can trigger them manually from a candidate&apos;s page.
              </p>
            </div>
          ) : null}

          {/* Rows */}
          {!isLoading &&
            filtered.map((row) => {
              const callKind = row.call_kind || "screening";
              const KindIcon = KIND_ICONS[callKind] || PhoneCall;
              const kindLabel = CALL_KIND_LABELS[callKind] || callKind;
              const isInbound = callKind === "general_query";
              const canRedial =
                row.status === "no_answer" ||
                row.status === "failed" ||
                row.status === "voicemail" ||
                row.status === "callback_requested";

              return (
                <div
                  key={row.voice_call_id}
                  className="group grid grid-cols-[2fr_1.5fr_130px_160px_120px_80px_100px_100px] px-4 py-3 border-b border-border hover:bg-muted/30 transition cursor-pointer items-center"
                  onClick={() => setSelected(row)}
                >
                  {/* Candidate */}
                  <div className="min-w-0 pr-3">
                    <Link
                      href={`/candidates/${row.application_id}`}
                      className="block truncate font-semibold hover:underline text-sm"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.candidate_name ?? "Unnamed"}
                    </Link>
                    {row.candidate_phone ? (
                      <p className="truncate text-xs text-muted-foreground">
                        {row.candidate_phone}
                      </p>
                    ) : null}
                  </div>

                  {/* Role */}
                  <div className="min-w-0 pr-3">
                    <p className="text-sm truncate">{row.role_title ?? "—"}</p>
                  </div>

                  {/* Type */}
                  <div>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] ${
                        isInbound
                          ? "bg-blue-500/15 text-blue-600"
                          : "bg-accent/40 text-accent-foreground"
                      }`}
                    >
                      <KindIcon className="h-3 w-3 shrink-0" />
                      <span className="truncate">{kindLabel}</span>
                    </span>
                  </div>

                  {/* Status */}
                  <div>
                    <StatusTag stage={`voice_screen_${row.status}`} />
                  </div>

                  {/* Score */}
                  <div>
                    {callKind === "screening" && row.overall_score != null ? (
                      <ScoreBar score={row.overall_score} />
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </div>

                  {/* Duration */}
                  <div>
                    <span className="text-sm tabular-nums">
                      {fmtDuration(row.duration_sec)}
                    </span>
                  </div>

                  {/* Time */}
                  <div>
                    <span className="text-xs text-muted-foreground">
                      {fmtRelative(row.created_at)}
                    </span>
                  </div>

                  {/* Actions */}
                  <div
                    className="flex items-center gap-1"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {canRedial ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => redial(row.application_id, row.call_kind)}
                        disabled={redialing === row.application_id}
                        className="h-7 px-2 text-[11px]"
                      >
                        {redialing === row.application_id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <RotateCw className="h-3 w-3" />
                        )}
                      </Button>
                    ) : null}
                    <Link
                      href={`/candidates/${row.application_id}`}
                      className="inline-flex items-center justify-center h-7 w-7 rounded hover:bg-muted transition"
                    >
                      <ExternalLink className="h-3 w-3 text-muted-foreground" />
                    </Link>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
                  </div>
                </div>
              );
            })}
        </div>
      </div>

      {/* Detail drawer — replaces inline expand row */}
      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent width="md">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>{selected.candidate_name ?? "Unnamed"}</SheetTitle>
                <SheetDescription>
                  {selected.role_title ?? "—"} ·{" "}
                  <span className="capitalize">{selected.status.replace(/_/g, " ")}</span>
                </SheetDescription>
              </SheetHeader>

              <SheetBody className="space-y-5">
                <CallDetail key={selected.voice_call_id} row={selected} />
              </SheetBody>

              <SheetFooter className="flex justify-end gap-2">
                <Link
                  href={`/candidates/${selected.application_id}`}
                  className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary/90 transition"
                >
                  Open candidate <ChevronRight className="h-3.5 w-3.5" />
                </Link>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}

/* ─── KPI strip item ─────────────────────────────────────────────────── */

function KpiItem({
  label,
  v,
  icon: Icon,
  iconClass = "text-muted-foreground",
  last = false,
}: {
  label: string;
  v: number;
  icon: typeof PhoneCall;
  iconClass?: string;
  last?: boolean;
}) {
  return (
    <div
      className={`flex flex-1 items-center justify-between px-5 py-4 ${
        last ? "" : "border-r border-border"
      }`}
    >
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </p>
        <p className="text-2xl font-extrabold tabular-nums">{v}</p>
      </div>
      <Icon className={`h-5 w-5 shrink-0 ${iconClass}`} />
    </div>
  );
}

/* ─── Filter chip ────────────────────────────────────────────────────── */

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs transition ${
        active
          ? "bg-primary text-white border-primary"
          : "border-border bg-card hover:bg-muted text-foreground"
      }`}
    >
      {label}
    </button>
  );
}

/* ─── Call detail — rendered inside Sheet body ───────────────────────── */
/* key={row.voice_call_id} on the parent ensures state resets per selection */

function CallDetail({ row }: { row: VoiceCallListItem }) {
  const callKind = row.call_kind || "screening";

  const [showTranscript, setShowTranscript] = useState(false);
  const [transcript, setTranscript] = useState<{
    text: string | null;
    answers: Array<{
      question_id?: string;
      question: string;
      answer_transcript: string;
      duration_sec?: number | null;
    }>;
  } | null>(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [showRecording, setShowRecording] = useState(false);

  const loadTranscript = useCallback(async () => {
    if (transcript) {
      setShowTranscript((v) => !v);
      return;
    }
    setLoadingTranscript(true);
    try {
      const data = await voiceCalls.transcript(row.voice_call_id);
      setTranscript({ text: data.transcript_text, answers: data.answers });
      setShowTranscript(true);
    } catch {
      setTranscript({ text: "Failed to load transcript.", answers: [] });
      setShowTranscript(true);
    } finally {
      setLoadingTranscript(false);
    }
  }, [transcript, row.voice_call_id]);

  return (
    <div className="space-y-5">
      {/* Section: Outcome */}
      {((row.overall_score != null || row.verdict) && callKind === "screening") ||
      row.next_action ? (
        <div className="space-y-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Outcome
          </p>
          {(row.overall_score != null || row.verdict) && callKind === "screening" ? (
            <div className="flex items-center gap-3">
              <ScoreBar score={row.overall_score} />
              {row.verdict ? (
                <span
                  className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] ${
                    row.verdict === "clear_pass"
                      ? "bg-emerald-500/15 text-emerald-600"
                      : row.verdict === "clear_reject"
                      ? "bg-destructive/15 text-destructive"
                      : "bg-amber-500/15 text-foreground"
                  }`}
                >
                  {row.verdict.replace(/_/g, " ")}
                </span>
              ) : null}
            </div>
          ) : null}
          {row.next_action ? (
            <p className="rounded-md bg-primary/5 px-3 py-2 text-[11px]">
              <span className="font-mono uppercase tracking-[0.12em] text-muted-foreground">
                Next:
              </span>{" "}
              {row.next_action}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="border-t border-border" />

      {/* Section: Status details */}
      <div className="space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Status details
        </p>
        {row.status === "voicemail" ? (
          <p className="flex items-center gap-1.5 rounded-md bg-amber-500/10 px-3 py-2 text-[11px] text-foreground">
            <Voicemail className="h-3 w-3 text-amber-500 shrink-0" />
            Went to voicemail — auto-retry scheduled
            {row.callback_at
              ? ` for ${new Date(row.callback_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}`
              : ""}
          </p>
        ) : null}
        {row.callback_at && row.status !== "voicemail" ? (
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <CalendarClock className="h-3 w-3 shrink-0" />
            Callback:{" "}
            {new Date(row.callback_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
            {row.callback_reason ? ` — ${row.callback_reason}` : ""}
          </p>
        ) : null}
        {row.candidate_response ? (
          <p className="line-clamp-4 rounded-md bg-muted/40 px-3 py-2 text-[11px] italic">
            &ldquo;{row.candidate_response}&rdquo;
          </p>
        ) : null}
        <p className="font-mono text-[10px] text-muted-foreground">
          Attempt {row.attempt_no} &middot; {fmtDuration(row.duration_sec)}
        </p>
        {row.error ? (
          <p className="line-clamp-3 text-[11px] text-destructive">{row.error}</p>
        ) : null}
      </div>

      <div className="border-t border-border" />

      {/* Section: Transcript */}
      <div className="space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Transcript
        </p>
        {row.transcript_url ? (
          <>
            <button
              onClick={loadTranscript}
              disabled={loadingTranscript}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[11px] hover:bg-muted transition"
            >
              {loadingTranscript ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <FileText className="h-3 w-3" />
              )}
              {showTranscript ? "Hide" : "Load"} Transcript
              {showTranscript ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
            </button>
            {showTranscript && transcript ? (
              <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3">
                {transcript.answers.length > 0 ? (
                  <div className="space-y-3">
                    {transcript.answers.map((a, i) => (
                      <div key={a.question_id || i} className="space-y-1">
                        <p className="text-[11px] font-semibold text-primary">
                          Q{i + 1}: {a.question}
                        </p>
                        <p className="text-[11px] text-foreground leading-relaxed">
                          {a.answer_transcript || (
                            <span className="italic text-muted-foreground">No response</span>
                          )}
                        </p>
                        {a.duration_sec != null ? (
                          <p className="font-mono text-[10px] text-muted-foreground">
                            {Math.round(a.duration_sec)}s
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {transcript.text ? (
                  <div
                    className={
                      transcript.answers.length > 0 ? "border-t border-border pt-2" : ""
                    }
                  >
                    <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                      Full transcript
                    </p>
                    <pre className="whitespace-pre-wrap text-[11px] leading-relaxed text-foreground max-h-60 overflow-y-auto">
                      {transcript.text}
                    </pre>
                  </div>
                ) : null}
                {!transcript.text && transcript.answers.length === 0 ? (
                  <p className="text-[11px] italic text-muted-foreground">
                    No transcript available
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-[11px] italic text-muted-foreground">No transcript</p>
        )}
      </div>

      <div className="border-t border-border" />

      {/* Section: Recording */}
      <div className="space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Recording
        </p>
        {row.recording_url ? (
          <>
            <button
              onClick={() => setShowRecording((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[11px] hover:bg-muted transition"
            >
              <Volume2 className="h-3 w-3" />
              {showRecording ? "Hide" : "Play"} Recording
            </button>
            {showRecording ? (
              <div className="rounded-md border border-border bg-muted/30 p-2">
                <audio
                  controls
                  preload="metadata"
                  className="w-full h-8"
                  src={row.recording_url}
                />
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-[11px] italic text-muted-foreground">No recording</p>
        )}
      </div>
    </div>
  );
}
