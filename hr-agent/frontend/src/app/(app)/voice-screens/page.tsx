"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  PhoneCall,
  ExternalLink,
  Play,
  RotateCw,
  CalendarClock,
  CheckCircle2,
  XCircle,
  PhoneOff,
  Loader2,
  PhoneIncoming,
  PhoneOutgoing,
  Voicemail,
  Tag,
  FileText,
  ChevronDown,
  ChevronUp,
  Volume2,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusTag } from "@/components/status-tag";
import { ScoreBar } from "@/components/tier-badge";
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

export default function VoiceCallsPage() {
  const { data, error, isLoading, mutate } = useSWR<VoiceCallListItem[]>(
    "/agentic/voice-calls",
    () => voiceCalls.list({ limit: 200 }),
    { refreshInterval: 15_000 },
  );
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [kindFilter, setKindFilter] = useState<CallKind | "all">("all");
  const [redialing, setRedialing] = useState<string | null>(null);

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

        {/* KPI strip */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
          <Kpi label="Total" v={stats.total} icon={PhoneCall} />
          <Kpi label="Live" v={stats.live} icon={Loader2} accent="info" />
          <Kpi label="Callback" v={stats.callback} icon={CalendarClock} accent="warn" />
          <Kpi label="Completed" v={stats.completed} icon={CheckCircle2} accent="info" />
          <Kpi label="Failed" v={stats.failed} icon={PhoneOff} accent="danger" />
          <Kpi label="Voicemail" v={stats.voicemail} icon={Voicemail} accent="warn" />
          <Kpi label="Passed" v={stats.passed} icon={CheckCircle2} accent="success" />
          <Kpi label="Inbound" v={stats.inbound} icon={PhoneIncoming} accent="info" />
        </div>

        {/* Call kind filter chips */}
        <div className="space-y-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Call type
          </p>
          <div className="flex flex-wrap gap-2">
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
        </div>

        {/* Status filter chips */}
        <div className="flex flex-wrap gap-2">
          {(Object.keys(STATUS_FILTER_LABELS) as StatusFilter[]).map((k) => (
            <FilterChip
              key={k}
              label={STATUS_FILTER_LABELS[k]}
              active={statusFilter === k}
              onClick={() => setStatusFilter(k)}
            />
          ))}
          <button
            onClick={() => mutate()}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-card px-3 py-1 font-mono text-[11px] uppercase tracking-[0.12em] hover:bg-muted"
          >
            <RotateCw className="h-3 w-3" /> Refresh
          </button>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-24 rounded-lg skeleton" />
            ))}
          </div>
        ) : null}

        {data && filtered.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <PhoneCall className="h-8 w-8 text-primary" />
              <p className="text-base font-bold">
                {data.length === 0
                  ? "No voice calls yet"
                  : "No calls matching filters"}
              </p>
              <p className="max-w-md text-sm text-muted-foreground">
                Calls are dispatched automatically when candidates progress
                through the pipeline, or you can trigger them manually from a
                candidate's page.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {filtered.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {filtered.map((row) => (
              <CallCard
                key={row.voice_call_id}
                row={row}
                onRedial={() => redial(row.application_id, row.call_kind)}
                redialing={redialing === row.application_id}
              />
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}

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
      className={`rounded-full border px-3 py-1 font-mono text-[11px] uppercase tracking-[0.12em] transition ${
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-card hover:bg-muted"
      }`}
    >
      {label}
    </button>
  );
}

function Kpi({
  label,
  v,
  icon: Icon,
  accent,
}: {
  label: string;
  v: number;
  icon: typeof PhoneCall;
  accent?: "info" | "warn" | "danger" | "success";
}) {
  const colour =
    accent === "info"
      ? "text-info"
      : accent === "warn"
      ? "text-warning"
      : accent === "danger"
      ? "text-destructive"
      : accent === "success"
      ? "text-success"
      : "text-muted-foreground";
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            {label}
          </p>
          <p className="text-2xl font-extrabold tabular-nums">{v}</p>
        </div>
        <Icon className={`h-5 w-5 ${colour}`} />
      </CardContent>
    </Card>
  );
}

function CallCard({
  row,
  onRedial,
  redialing,
}: {
  row: VoiceCallListItem;
  onRedial: () => void;
  redialing: boolean;
}) {
  const canRedial =
    row.status === "no_answer" ||
    row.status === "failed" ||
    row.status === "voicemail" ||
    row.status === "callback_requested";

  const callKind = row.call_kind || "screening";
  const KindIcon = KIND_ICONS[callKind] || PhoneCall;
  const kindLabel = CALL_KIND_LABELS[callKind] || callKind;
  const isInbound = callKind === "general_query";

  const [showTranscript, setShowTranscript] = useState(false);
  const [transcript, setTranscript] = useState<{
    text: string | null;
    answers: Array<{ question_id?: string; question: string; answer_transcript: string; duration_sec?: number | null }>;
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
    <Card className="overflow-hidden">
      <CardContent className="space-y-2 p-4">
        {/* Header: name + kind badge + status */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <Link
              href={`/candidates/${row.application_id}`}
              className="block truncate font-semibold hover:underline"
            >
              {row.candidate_name ?? "Unnamed"}
            </Link>
            <p className="truncate text-xs text-muted-foreground">
              {row.role_title ?? "—"}
              {row.candidate_phone ? ` · ${row.candidate_phone}` : ""}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-1.5">
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] ${
                  isInbound
                    ? "bg-info/15 text-info"
                    : "bg-accent/40 text-accent-foreground"
                }`}
              >
                <KindIcon className="h-3 w-3" />
                {kindLabel}
              </span>
              <StatusTag stage={`voice_screen_${row.status}`} />
            </div>
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              attempt {row.attempt_no}
              {row.duration_sec ? ` · ${Math.round(row.duration_sec)}s` : ""}
            </span>
          </div>
        </div>

        {/* Score + verdict (screening calls only) */}
        {(row.overall_score != null || row.verdict) && callKind === "screening" ? (
          <div className="flex items-center gap-3">
            <ScoreBar score={row.overall_score} />
            {row.verdict ? (
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] ${
                  row.verdict === "clear_pass"
                    ? "bg-success/15 text-success"
                    : row.verdict === "clear_reject"
                    ? "bg-destructive/15 text-destructive"
                    : "bg-warning/15 text-foreground"
                }`}
              >
                {row.verdict.replace("_", " ")}
              </span>
            ) : null}
          </div>
        ) : null}

        {/* Next action */}
        {row.next_action ? (
          <p className="rounded-md bg-primary/5 px-2 py-1 text-[11px]">
            <span className="font-mono uppercase tracking-[0.12em] text-muted-foreground">
              Next:
            </span>{" "}
            {row.next_action}
          </p>
        ) : null}

        {/* Voicemail indicator */}
        {row.status === "voicemail" ? (
          <p className="flex items-center gap-1 rounded-md bg-warning/10 px-2 py-1 text-[11px] text-foreground">
            <Voicemail className="h-3 w-3 text-warning" />
            Went to voicemail — auto-retry scheduled
            {row.callback_at
              ? ` for ${new Date(row.callback_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}`
              : ""}
          </p>
        ) : null}

        {/* Callback time */}
        {row.callback_at && row.status !== "voicemail" ? (
          <p className="text-[11px] text-muted-foreground">
            <CalendarClock className="mr-1 inline h-3 w-3" />
            Callback: {new Date(row.callback_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
            {row.callback_reason ? ` — ${row.callback_reason}` : ""}
          </p>
        ) : null}

        {/* Last response quote */}
        {row.candidate_response ? (
          <p className="line-clamp-3 rounded-md bg-muted/40 px-2 py-1 text-[11px] italic">
            &ldquo;{row.candidate_response}&rdquo;
          </p>
        ) : null}

        {/* Error */}
        {row.error ? (
          <p className="line-clamp-2 text-[11px] text-destructive">{row.error}</p>
        ) : null}

        {/* Footer: actions + meta */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {row.recording_url ? (
            <button
              onClick={() => setShowRecording((v) => !v)}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] hover:bg-muted"
            >
              <Volume2 className="h-3 w-3" />
              {showRecording ? "Hide" : "Play"} Recording
            </button>
          ) : null}
          {row.transcript_url ? (
            <button
              onClick={loadTranscript}
              disabled={loadingTranscript}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] hover:bg-muted"
            >
              {loadingTranscript ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <FileText className="h-3 w-3" />
              )}
              Transcript
              {showTranscript ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
            </button>
          ) : null}
          {canRedial ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onRedial}
              disabled={redialing}
              className="h-6 px-2 text-[11px]"
            >
              {redialing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RotateCw className="mr-1 h-3 w-3" />
              )}
              Re-dial
            </Button>
          ) : null}
          <span className="ml-auto text-[10px] text-muted-foreground">
            {fmtRelative(row.created_at)}
          </span>
        </div>

        {/* Inline audio player */}
        {showRecording && row.recording_url ? (
          <div className="rounded-md border border-border bg-muted/30 p-2">
            <audio
              controls
              preload="metadata"
              className="w-full h-8"
              src={row.recording_url}
            />
          </div>
        ) : null}

        {/* Inline transcript */}
        {showTranscript && transcript ? (
          <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
            {transcript.answers.length > 0 ? (
              <div className="space-y-3">
                {transcript.answers.map((a, i) => (
                  <div key={a.question_id || i} className="space-y-1">
                    <p className="text-[11px] font-semibold text-primary">
                      Q{i + 1}: {a.question}
                    </p>
                    <p className="text-[11px] text-foreground leading-relaxed">
                      {a.answer_transcript || <span className="italic text-muted-foreground">No response</span>}
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
              <div className={transcript.answers.length > 0 ? "border-t border-border pt-2" : ""}>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  Full transcript
                </p>
                <pre className="whitespace-pre-wrap text-[11px] leading-relaxed text-foreground max-h-60 overflow-y-auto">
                  {transcript.text}
                </pre>
              </div>
            ) : null}
            {!transcript.text && transcript.answers.length === 0 ? (
              <p className="text-[11px] italic text-muted-foreground">No transcript available</p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
