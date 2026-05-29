"use client";

import { useEffect } from "react";
import useSWR, { useSWRConfig } from "swr";
import { Bot, ExternalLink, FileText, Paperclip, User } from "lucide-react";
import { useApplicationEvents } from "@/hooks/use-application-events";
import { MarkdownLite } from "@/components/markdown-lite";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface DashboardMessage {
  sequence: number;
  role: string;
  content: string | null;
  created_at: string;
  model?: string | null;
  latency_ms?: number | null;
  output_tokens?: number | null;
}

interface ConversationData {
  application_id: string;
  conversation_id: string;
  stage: string;
  created_at: string;
  updated_at: string;
  messages: DashboardMessage[];
  screening: {
    tailored_q1: string | null;
    tailored_a1: string | null;
    tailored_q2: string | null;
    tailored_a2: string | null;
    current_ctc_lpa: number | null;
    expected_ctc_lpa: number | null;
    notice_period_days: number | null;
    willing_to_relocate: boolean | null;
    composite_score: number | null;
    knock_out_triggered: boolean;
    knock_out_reason: string | null;
    submitted_at: string | null;
  } | null;
  assignment: {
    brief_md: string | null;
    problems: Array<{
      id: string;
      title: string;
      statement: string;
      estimated_minutes: number;
      expected_artifacts?: string[];
    }> | null;
    submission_url: string | null;
    submission_text: string | null;
    submission_r2_keys: string[] | null;
    submitted_at: string | null;
    score: number | null;
  } | null;
}

function fmtTime(s: string): string {
  return new Date(s).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ScreeningSummary({ s }: { s: NonNullable<ConversationData["screening"]> }) {
  const fields: Array<[string, string]> = [
    ["Current CTC", s.current_ctc_lpa != null ? `${s.current_ctc_lpa} LPA` : "—"],
    ["Expected CTC", s.expected_ctc_lpa != null ? `${s.expected_ctc_lpa} LPA` : "—"],
    ["Notice", s.notice_period_days != null ? `${s.notice_period_days} days` : "—"],
    [
      "Relocate",
      s.willing_to_relocate == null
        ? "—"
        : s.willing_to_relocate
          ? "Yes"
          : "No",
    ],
  ];
  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          screening summary
        </div>
        {s.composite_score != null && (
          <div className="text-right">
            <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
              score
            </div>
            <div
              className={cn(
                "text-xl font-extrabold tabular-nums",
                s.knock_out_triggered ? "text-destructive" : "text-primary",
              )}
            >
              {s.composite_score}
            </div>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        {fields.map(([k, v]) => (
          <div key={k} className="rounded-md border border-border bg-background p-2">
            <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {k}
            </div>
            <div className="mt-0.5 font-semibold">{v}</div>
          </div>
        ))}
      </div>
      {s.knock_out_triggered && s.knock_out_reason && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          Knock-out: {s.knock_out_reason}
        </div>
      )}
      {(s.tailored_q1 || s.tailored_q2) && (
        <ul className="space-y-2 text-xs">
          {[1, 2].map((i) => {
            // ``s`` carries booleans / numbers too; index it through unknown
            // before narrowing to string|null so TS doesn't complain about
            // the structural mismatch.
            const idx = s as unknown as Record<string, string | null>;
            const q = idx[`tailored_q${i}`];
            const a = idx[`tailored_a${i}`];
            if (!q) return null;
            return (
              <li key={i} className="rounded-md border border-border bg-background p-2">
                <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  Q{i}
                </div>
                <div className="mt-0.5 font-semibold">{q}</div>
                {a && (
                  <div className="mt-1 whitespace-pre-wrap text-foreground/85">{a}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function AssignmentSummary({ a }: { a: NonNullable<ConversationData["assignment"]> }) {
  return (
    <div className="space-y-3 rounded-lg border border-amber-300/50 bg-amber-50/50 p-4 dark:border-amber-700/40 dark:bg-amber-950/20">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          <FileText className="h-3.5 w-3.5" /> agent-generated assignment
        </div>
        {a.score != null && (
          <div className="text-right">
            <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              score
            </div>
            <div className="text-xl font-extrabold tabular-nums text-primary">
              {a.score}
            </div>
          </div>
        )}
      </div>

      {a.brief_md && (
        <div className="-my-2">
          <MarkdownLite source={a.brief_md} />
        </div>
      )}

      {a.problems && a.problems.length > 0 && (
        <ul className="space-y-2 text-xs">
          {a.problems.map((p, i) => (
            <li
              key={p.id}
              className="rounded-md border border-amber-200/60 bg-background/60 p-2 dark:border-amber-800/40"
            >
              <div className="font-semibold">
                {i + 1}. {p.title}{" "}
                <span className="font-mono text-[10px] text-muted-foreground">
                  ~{p.estimated_minutes}m
                </span>
              </div>
              <div className="mt-1 leading-relaxed text-foreground/85">{p.statement}</div>
            </li>
          ))}
        </ul>
      )}

      {(a.submission_url || a.submission_text || (a.submission_r2_keys?.length ?? 0) > 0) && (
        <div className="space-y-1 rounded-md border border-border bg-background p-2 text-xs">
          <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            submission
          </div>
          {a.submission_url && (
            <a
              href={a.submission_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              {a.submission_url} <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {a.submission_text && (
            <div className="whitespace-pre-wrap text-foreground/85">{a.submission_text}</div>
          )}
          {a.submission_r2_keys?.map((k) => (
            <div key={k} className="inline-flex items-center gap-1 font-mono">
              <Paperclip className="h-3 w-3" /> {k.split("/").pop()}
            </div>
          ))}
          {a.submitted_at && (
            <div className="text-muted-foreground">
              Submitted {fmtTime(a.submitted_at)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MessageRow({ m }: { m: DashboardMessage }) {
  if (!m.content) return null;
  if (m.role !== "user" && m.role !== "assistant") return null;
  const isUser = m.role === "user";
  return (
    <div
      className={cn(
        "flex gap-2.5 rounded-lg border border-border bg-background p-3",
        isUser && "border-primary/30 bg-primary/5",
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px]",
          isUser ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
        )}
      >
        {isUser ? <User className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[11px] text-muted-foreground">
          <span className="font-mono uppercase tracking-wider">
            {isUser ? "candidate" : "agent"}
          </span>
          <span>·</span>
          <span>{fmtTime(m.created_at)}</span>
          {m.latency_ms != null && (
            <span className="font-mono">· {m.latency_ms}ms</span>
          )}
        </div>
        {isUser ? (
          <div className="mt-1 whitespace-pre-wrap text-sm">{m.content}</div>
        ) : (
          <div className="mt-1 -mb-2">
            <MarkdownLite source={m.content} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Render the V2 chat conversation if one exists.
 *
 * V1-only candidates (created before the migration, or roles set to
 * ``screening_modality='voice'``) won't have a conversation row -- we
 * surface ``hidden=true`` via the imperative ``onHidden`` callback so the
 * parent page can drop the section entirely instead of rendering an empty
 * placeholder.
 */
export function ConversationView({
  applicationId,
  onHidden,
}: {
  applicationId: string;
  onHidden?: () => void;
}) {
  const { counter } = useApplicationEvents(applicationId);
  const swrKey = applicationId
    ? `/dashboard/v2/applications/${applicationId}/conversation`
    : null;
  const { data, error, isLoading } = useSWR<ConversationData>(swrKey, swrFetcher, {
    refreshInterval: 0,
    shouldRetryOnError: false,
  });
  const { mutate } = useSWRConfig();

  // Live update: when the per-application SSE channel ticks (chat_message
  // / chat_stage_change events), invalidate the dashboard cache so HR sees
  // turns appear in the transcript without polling.
  useEffect(() => {
    if (!swrKey || counter === 0) return;
    void mutate(swrKey);
  }, [swrKey, counter, mutate]);

  // Suppress the section entirely for V1-only / voice-track candidates.
  useEffect(() => {
    if (data === null || (error && (error as { status?: number }).status === 404)) {
      onHidden?.();
    }
  }, [data, error, onHidden]);

  if (error) {
    if ((error as { status?: number }).status === 404) {
      return null;
    }
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
        Failed to load conversation: {(error as Error).message}
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
        Loading conversation…
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-xs">
        <div className="font-mono uppercase tracking-wider text-muted-foreground">
          stage · {data.stage}
        </div>
        <div className="text-muted-foreground">
          {data.messages.length} messages · started {fmtTime(data.created_at)}
        </div>
      </div>

      {data.screening && <ScreeningSummary s={data.screening} />}
      {data.assignment && <AssignmentSummary a={data.assignment} />}

      <div className="space-y-2">
        <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          transcript
        </div>
        {data.messages.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
            No turns yet.
          </div>
        ) : (
          <div className="space-y-2">
            {data.messages.map((m) => (
              <MessageRow key={m.sequence} m={m} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
