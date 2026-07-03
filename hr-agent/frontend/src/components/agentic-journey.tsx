"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { useApplicationEvents } from "@/hooks/use-application-events";
import {
  PhoneCall,
  ClipboardCheck,
  Video,
  Loader2,
  ExternalLink,
  Play,
  CalendarClock,
  Crown,
  Users,
} from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ManualScheduleModal } from "@/components/manual-schedule-modal";
import { StatusTag } from "@/components/status-tag";
import { ScoreChip } from "@/components/tier-badge";
import {
  ceoDashboard,
  voiceCalls,
  assessments,
  meetings,
  type CEODetail,
  type AssessmentSummary,
} from "@/lib/api/agentic";

/**
 * Agentic-V2 journey panel embedded in the candidate detail page.
 *
 * Surfaces phone-screen, assessment, and meeting state for one application
 * and lets recruiters trigger the next stage in a single click. Reuses the
 * `/dashboard/ceo/applications/{id}` endpoint as the data source -- it's
 * the same shape, just gated by `require_ceo` for that route. For
 * recruiter-side embedding we expose the same data here (backend allows
 * recruiter role to read it; if not we'd fall back to a dedicated route).
 */
export function AgenticJourney({ applicationId }: { applicationId: string }) {
  const { data, isLoading, error, mutate } = useSWR<CEODetail>(
    `/dashboard/ceo/applications/${applicationId}`,
    () => ceoDashboard.detail(applicationId),
    { refreshInterval: 30_000 },
  );

  // Live updates: when the backend pushes an event for this application,
  // revalidate the SWR cache immediately instead of waiting for the
  // 30-second poll interval.
  const { counter } = useApplicationEvents(applicationId);
  useEffect(() => {
    if (counter > 0) mutate();
  }, [counter, mutate]);

  const hasAnything =
    !!data &&
    (data.voice_calls.length > 0 ||
      data.assessments.length > 0 ||
      data.meetings.length > 0);

  return (
    <Card className="border-primary/20">
      <CardContent className="space-y-5 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold uppercase tracking-[0.12em]">
              Agentic journey
            </h3>
            <p className="text-xs text-muted-foreground">
              Phone screen · Assessments · Meeting analysis
            </p>
          </div>
          <DispatchButtons applicationId={applicationId} onAfter={() => mutate()} />
        </div>

        {error ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
            {error.message}
          </p>
        ) : null}

        {isLoading ? <div className="h-32 rounded-lg skeleton" /> : null}

        {data && !hasAnything ? (
          <p className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
            No agentic rounds run yet for this candidate. Use the dispatch buttons above.
          </p>
        ) : null}

        {data && hasAnything ? (
          <div className="space-y-5">
            {data.voice_calls.length > 0 ? (
              <Section icon={PhoneCall} title="Phone screen">
                <PhoneScreenCard calls={data.voice_calls} />
              </Section>
            ) : null}

            {data.assessments.length > 0 ? (
              <Section icon={ClipboardCheck} title="Assessments">
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {data.assessments.map((a) => (
                    <AssessmentCard key={a.assessment_id} a={a} />
                  ))}
                </div>
              </Section>
            ) : null}

            {data.meetings.length > 0 ? (
              <Section icon={Video} title="Meetings">
                <div className="space-y-2">
                  {data.meetings.map((m) => (
                    <div
                      key={m.meeting_session_id}
                      className="rounded-lg border border-border bg-card p-3"
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-primary">
                            {m.round}
                          </span>
                          <StatusTag stage={`technical_meeting_${m.bot_status}`} />
                        </div>
                        {m.verdict ? (
                          <span className="rounded-md bg-muted px-2 py-0.5 font-mono text-[10px]">
                            {m.verdict}
                          </span>
                        ) : null}
                      </div>
                      <div className="grid grid-cols-4 gap-2 text-xs">
                        <Mini label="Overall" v={m.overall_score} />
                        <Mini label="Tech" v={m.technical_score} />
                        <Mini label="Comm" v={m.communication_score} />
                        <Mini label="Conf" v={m.confidence_score} />
                      </div>
                    </div>
                  ))}
                </div>
              </Section>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function TranscriptView({
  text,
  downloadUrl,
}: {
  text: string;
  downloadUrl?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);

  // Parse "[role] message" lines into structured turns. Tolerant of blank
  // trailing lines and lines without a leading role tag.
  const turns: Array<{ role: "agent" | "candidate"; text: string }> = [];
  for (const raw of text.split("\n")) {
    const m = raw.match(/^\[(agent|candidate|user)\]\s*(.*)$/i);
    if (m) {
      const r = m[1].toLowerCase();
      const role: "agent" | "candidate" = r === "agent" ? "agent" : "candidate";
      const msg = m[2].trim();
      if (msg) turns.push({ role, text: msg });
    } else if (raw.trim() && turns.length > 0) {
      turns[turns.length - 1].text += " " + raw.trim();
    }
  }

  const previewLimit = 4;
  const visible = expanded ? turns : turns.slice(0, previewLimit);
  const hidden = Math.max(turns.length - previewLimit, 0);

  return (
    <div className="mt-3 border-t border-border pt-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Conversation
        </span>
        {downloadUrl ? (
          <a
            href={downloadUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
            title="Download raw transcript"
          >
            raw ↗
          </a>
        ) : null}
      </div>
      <ul className="space-y-1.5">
        {visible.map((t, i) => (
          <li
            key={i}
            className={`flex gap-2 rounded-md px-2 py-1 text-[12px] leading-snug ${
              t.role === "agent"
                ? "bg-primary/5"
                : "bg-muted/40"
            }`}
          >
            <span
              className={`shrink-0 font-mono text-[9px] uppercase tracking-[0.15em] ${
                t.role === "agent" ? "text-primary" : "text-muted-foreground"
              }`}
              style={{ minWidth: "60px" }}
            >
              {t.role}
            </span>
            <span className="text-foreground/90">{t.text}</span>
          </li>
        ))}
      </ul>
      {hidden > 0 && !expanded ? (
        <button
          onClick={() => setExpanded(true)}
          className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
        >
          ▸ show {hidden} more turn{hidden === 1 ? "" : "s"}
        </button>
      ) : null}
      {expanded ? (
        <button
          onClick={() => setExpanded(false)}
          className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
        >
          ▾ collapse
        </button>
      ) : null}
    </div>
  );
}

function PhoneScreenCard({
  calls,
}: {
  calls: NonNullable<CEODetail["voice_calls"]>;
}) {
  const [showHistory, setShowHistory] = useState(false);
  // Backend orders voice_calls newest-first.
  const latest = calls[0];
  const older = calls.slice(1);
  const totalAttempts = Math.max(...calls.map((c) => c.attempt_no), 0);

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      {/* Headline: only the latest call */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusTag stage={`voice_screen_${latest.status}`} />
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            attempt {latest.attempt_no} of {totalAttempts}
          </span>
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">
          {latest.duration_sec ? `${Math.round(latest.duration_sec)}s` : "—"}
        </span>
      </div>
      <ScoreChip score={latest.overall_score} />

      {latest.next_action ? (
        <p className="mt-2 rounded-md bg-primary/5 px-2 py-1 text-[11px]">
          <span className="font-mono uppercase tracking-[0.12em] text-muted-foreground">
            Next:
          </span>{" "}
          {latest.next_action}
        </p>
      ) : null}

      {latest.callback_at ? (
        <p className="mt-1 text-[11px] text-muted-foreground">
          <CalendarClock className="mr-1 inline h-3 w-3" />
          Callback: {new Date(latest.callback_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
          {latest.callback_reason ? ` — ${latest.callback_reason}` : ""}
        </p>
      ) : null}

      {latest.candidate_response ? (
        <p className="mt-1 line-clamp-3 rounded-md bg-muted/40 px-2 py-1 text-[11px] italic">
          “{latest.candidate_response}”
        </p>
      ) : null}

      {latest.error ? (
        <p className="mt-1 line-clamp-2 text-[11px] text-destructive">
          {latest.error}
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap gap-2">
        {latest.recording_url ? (
          <a
            href={latest.recording_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] hover:bg-muted"
          >
            <Play className="h-3 w-3" /> Recording
          </a>
        ) : null}
        {latest.transcript_url && !latest.transcript_text ? (
          <a
            href={latest.transcript_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] hover:bg-muted"
          >
            Transcript <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>

      {latest.transcript_text ? (
        <TranscriptView text={latest.transcript_text} downloadUrl={latest.transcript_url} />
      ) : null}

      {/* Collapsed history */}
      {older.length > 0 ? (
        <div className="mt-3 border-t border-border pt-2">
          <button
            onClick={() => setShowHistory((s) => !s)}
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
          >
            {showHistory ? "▾" : "▸"} {older.length} earlier attempt
            {older.length === 1 ? "" : "s"}
          </button>
          {showHistory ? (
            <ul className="mt-2 space-y-1">
              {older.map((v) => (
                <li
                  key={v.voice_call_id}
                  className="flex items-center justify-between gap-2 rounded-md bg-muted/30 px-2 py-1 text-[11px]"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-muted-foreground">
                      #{v.attempt_no}
                    </span>
                    <StatusTag stage={`voice_screen_${v.status}`} />
                    {v.verdict ? (
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                        {v.verdict.replace("_", " ")}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {v.duration_sec ? (
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {Math.round(v.duration_sec)}s
                      </span>
                    ) : null}
                    {v.recording_url ? (
                      <a
                        href={v.recording_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-muted-foreground hover:text-foreground"
                        title="Recording"
                      >
                        <Play className="h-3 w-3" />
                      </a>
                    ) : null}
                    {v.transcript_url ? (
                      <a
                        href={v.transcript_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-muted-foreground hover:text-foreground"
                        title="Transcript"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof PhoneCall;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary" />
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function AssessmentCard({ a }: { a: AssessmentSummary }) {
  const bandColor =
    a.fit_band === "green"
      ? "bg-success/15 text-success"
      : a.fit_band === "amber"
      ? "bg-warning/15 text-foreground"
      : a.fit_band === "red"
      ? "bg-destructive/15 text-destructive"
      : "bg-muted text-muted-foreground";

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {a.kind ?? a.provider}
        </span>
        {a.fit_band ? (
          <span
            className={`rounded-full px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.15em] ${bandColor}`}
          >
            {a.fit_band}
          </span>
        ) : null}
      </div>
      <div className="text-xl font-extrabold tabular-nums">
        {a.percentile != null
          ? `${Math.round(a.percentile)}%`
          : a.normalized_score != null
          ? a.normalized_score.toFixed(1)
          : "—"}
      </div>
      <div className="text-[10px] text-muted-foreground">
        {a.percentile != null ? "percentile" : "score"} · {a.status}
      </div>
    </div>
  );
}

function Mini({ label, v }: { label: string; v: number | null }) {
  return (
    <div>
      <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </p>
      <p className="text-sm font-bold tabular-nums">{v != null ? v : "—"}</p>
    </div>
  );
}

function DispatchButtons({
  applicationId,
  onAfter,
}: {
  applicationId: string;
  onAfter: () => void;
}) {
  const [busy, setBusy] = useState<
    "voice" | "assess" | "tech" | "ceo" | "hr" | null
  >(null);
  const [manualOpen, setManualOpen] = useState(false);

  async function run(
    key: typeof busy,
    fn: () => Promise<unknown>,
    label: string,
  ) {
    setBusy(key);
    try {
      await fn();
      onAfter();
    } catch (e: any) {
      alert(`${label} failed: ${e?.message ?? "unknown"}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          run("voice", () => voiceCalls.dispatch({ application_id: applicationId }), "Voice")
        }
        disabled={!!busy}
      >
        {busy === "voice" ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        ) : (
          <PhoneCall className="mr-1 h-3 w-3" />
        )}
        Phone screen
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          run(
            "assess",
            () => assessments.dispatch({ application_id: applicationId }),
            "Assessment",
          )
        }
        disabled={!!busy}
      >
        {busy === "assess" ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        ) : (
          <ClipboardCheck className="mr-1 h-3 w-3" />
        )}
        Assessment
      </Button>

      {/* AI scheduling buttons -- agent picks slot, books Teams, calls candidate */}
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          run(
            "tech",
            () =>
              meetings.aiSchedule({ application_id: applicationId, round: "technical" }),
            "Technical schedule",
          )
        }
        disabled={!!busy}
        title="AI picks a slot, books Teams, calls candidate to confirm"
      >
        {busy === "tech" ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        ) : (
          <CalendarClock className="mr-1 h-3 w-3" />
        )}
        Schedule technical
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          run(
            "ceo",
            () =>
              meetings.aiSchedule({ application_id: applicationId, round: "ceo" }),
            "CEO schedule",
          )
        }
        disabled={!!busy}
      >
        {busy === "ceo" ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        ) : (
          <Crown className="mr-1 h-3 w-3" />
        )}
        Schedule CEO
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          run(
            "hr",
            () =>
              meetings.aiSchedule({ application_id: applicationId, round: "hr" }),
            "HR schedule",
          )
        }
        disabled={!!busy}
      >
        {busy === "hr" ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        ) : (
          <Users className="mr-1 h-3 w-3" />
        )}
        Schedule HR
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setManualOpen(true)}
        disabled={!!busy}
        title="Pick slot + panel manually (used when auto-schedule fails)"
      >
        <CalendarClock className="mr-1 h-3 w-3" />
        Manual schedule
      </Button>
      {manualOpen ? (
        <ManualScheduleModal
          applicationId={applicationId}
          onClose={() => setManualOpen(false)}
          onSaved={() => {
            setManualOpen(false);
            onAfter();
          }}
        />
      ) : null}
    </div>
  );
}
