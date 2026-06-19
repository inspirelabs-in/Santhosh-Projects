"use client";

import Link from "next/link";
import { useState } from "react";
import { Activity, Briefcase, CalendarClock, Clock, ExternalLink, Mail, Send, Sparkles, User, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Attachment } from "@/lib/useRecruiterChat";
import { QuickDateTime } from "@/components/quick-datetime";
import { ConfirmCard } from "./ConfirmCard";
import { LinkedInPostCard } from "./LinkedInPostCard";
import { NudgeCard } from "./NudgeCard";

interface ActionCtx {
  /** Drop a templated message into the chat to invoke an action via Pulse. */
  dispatch?: (msg: string) => void;
}

function ActionLink({
  label,
  command,
  icon: Icon,
  dispatch,
}: {
  label: string;
  command: string;
  icon: React.ComponentType<{ className?: string }>;
  dispatch?: (m: string) => void;
}) {
  if (!dispatch) return null;
  return (
    <button
      type="button"
      onClick={() => dispatch(command)}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-[10px] hover:bg-accent/40"
      title={label}
    >
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}

function StagePill({ stage }: { stage: string }) {
  const tone = (() => {
    const s = (stage || "").toLowerCase();
    if (s.includes("reject")) return "bg-red-500/10 text-red-600 dark:text-red-400";
    if (s.includes("hire")) return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    if (s.includes("assignment")) return "bg-amber-500/10 text-amber-600 dark:text-amber-400";
    if (s.includes("screen")) return "bg-blue-500/10 text-blue-600 dark:text-blue-400";
    if (s.includes("review")) return "bg-purple-500/10 text-purple-600 dark:text-purple-400";
    return "bg-muted text-muted-foreground";
  })();
  return (
    <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium tabular-nums", tone)}>
      {stage.replaceAll("_", " ")}
    </span>
  );
}

function ScoreChip({ score, label = "score" }: { score: number | null | undefined; label?: string }) {
  if (score == null) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-mono text-primary">
      {label} {score}
    </span>
  );
}

interface CandidateRow {
  application_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  role_title: string | null;
  stage: string;
  fit_tier?: string | null;
  fit_score?: number | null;
  screening_score?: number | null;
  applied_at?: string | null;
}

function CandidateListCard({
  items,
  ctx,
}: {
  items: CandidateRow[];
  ctx?: ActionCtx;
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/40 p-3 text-xs text-muted-foreground">
        No candidates matched.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <ul className="divide-y divide-border">
        {items.map((c) => (
          <li key={c.application_id} className="px-3 py-2 hover:bg-accent/30">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <User className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="truncate text-sm font-semibold">{c.candidate_name || "(no name)"}</span>
                  <StagePill stage={c.stage} />
                </div>
                <div className="truncate text-[11px] text-muted-foreground">
                  {c.candidate_email || "(no email)"} · {c.role_title || "(no role)"}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {c.fit_score != null && <ScoreChip score={c.fit_score} label="fit" />}
                {c.screening_score != null && <ScoreChip score={c.screening_score} label="scr" />}
                <Link
                  href={`/candidates/${c.application_id}`}
                  className="text-muted-foreground hover:text-foreground"
                  title="Open detail"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </Link>
              </div>
            </div>
            {ctx?.dispatch && (
              <div className="ml-11 mt-1 flex flex-wrap gap-1">
                <ActionLink
                  label="Open"
                  icon={ExternalLink}
                  command={`Open candidate ${c.application_id}`}
                  dispatch={ctx.dispatch}
                />
                <ActionLink
                  label="Re-invite"
                  icon={Send}
                  command={`Re-send chat invite to application ${c.application_id}`}
                  dispatch={ctx.dispatch}
                />
                <ActionLink
                  label="Email"
                  icon={Mail}
                  command={`Send a custom email to application ${c.application_id} -- ask me what to write.`}
                  dispatch={ctx.dispatch}
                />
                <ActionLink
                  label="Reject"
                  icon={X}
                  command={`Reject application ${c.application_id}. Use override_stage to_stage=rejected.`}
                  dispatch={ctx.dispatch}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface RoleRow {
  id: string;
  title: string;
  status: string;
  screening_modality?: string;
  ctc_range?: string | null;
  location?: string | null;
  applicant_count?: number;
}

function RoleListCard({ items }: { items: RoleRow[] }) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/40 p-3 text-xs text-muted-foreground">
        No roles found.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <ul className="divide-y divide-border">
        {items.map((r) => (
          <li key={r.id} className="flex items-center gap-3 px-3 py-2 hover:bg-accent/30">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Briefcase className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-sm font-semibold">{r.title}</span>
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {r.status}
                </span>
                {r.screening_modality && (
                  <span className="rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
                    {r.screening_modality}
                  </span>
                )}
              </div>
              <div className="truncate text-[11px] text-muted-foreground">
                {[r.ctc_range, r.location].filter(Boolean).join(" · ")}
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="text-sm font-bold tabular-nums">{r.applicant_count ?? 0}</div>
              <div className="text-[10px] text-muted-foreground">applicants</div>
            </div>
            <Link
              href={`/roles/${r.id}`}
              className="text-muted-foreground hover:text-foreground"
              title="Open role"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface MetricsData {
  by_stage?: Record<string, number>;
  applied_last_7_days?: number;
  total_applications?: number;
}

function MetricsCard({ data }: { data: MetricsData }) {
  const stages = Object.entries(data.by_stage || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-3">
      <div className="flex items-baseline justify-between">
        <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          pipeline
        </div>
        <div className="text-right">
          <div className="text-2xl font-extrabold tabular-nums">{data.total_applications ?? 0}</div>
          <div className="text-[10px] text-muted-foreground">total · {data.applied_last_7_days ?? 0} this week</div>
        </div>
      </div>
      <ul className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
        {stages.map(([stage, n]) => (
          <li key={stage} className="rounded-md bg-background p-2">
            <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
              {stage.replaceAll("_", " ")}
            </div>
            <div className="text-lg font-bold tabular-nums">{n}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface StuckRow {
  application_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  role_title: string | null;
  stage: string;
  hours_in_stage: number;
  time_in_stage?: string;
}

function fmtStuckDuration(hours: number): string {
  const days = Math.floor(hours / 24);
  const hrs = Math.round(hours % 24);
  if (days > 0) return `${days}d ${hrs}h`;
  return `${hrs}h`;
}

function StuckListCard({ items }: { items: StuckRow[] }) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/40 p-3 text-xs text-muted-foreground">
        Nothing stuck.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <ul className="divide-y divide-border">
        {items.map((c) => (
          <li key={c.application_id} className="flex items-center gap-3 px-3 py-2">
            <Clock className="h-4 w-4 shrink-0 text-amber-500" />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-sm font-medium">{c.candidate_name || "(no name)"}</span>
                <StagePill stage={c.stage} />
              </div>
              <div className="truncate text-[11px] text-muted-foreground">{c.role_title || "(no role)"}</div>
            </div>
            <div className="text-right">
              <div className="text-sm font-bold tabular-nums">{c.time_in_stage ?? fmtStuckDuration(c.hours_in_stage)}</div>
              <div className="text-[10px] text-muted-foreground">in stage</div>
            </div>
            <Link href={`/candidates/${c.application_id}`} className="text-muted-foreground hover:text-foreground">
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface AuditRow {
  ts: string | null;
  action: string;
  actor: string;
  application_id: string | null;
  details: Record<string, unknown> | null;
}

function AuditListCard({ items }: { items: AuditRow[] }) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/40 p-3 text-xs text-muted-foreground">
        No recent audit entries.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <ul className="divide-y divide-border text-xs">
        {items.map((r, i) => (
          <li key={i} className="flex items-center gap-2 px-3 py-2">
            <Activity className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="font-mono text-[11px]">{r.action}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">{r.actor}</span>
            <span className="ml-auto text-[10px] text-muted-foreground tabular-nums">
              {r.ts ? new Date(r.ts).toLocaleTimeString() : "..."}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface ActionResultData {
  ok?: boolean;
  message?: string;
  error?: string;
}

function ActionResultCard({ data }: { data: ActionResultData }) {
  const ok = !!data.ok && !data.error;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border p-3 text-sm",
        ok
          ? "border-emerald-300/50 bg-emerald-50/50 text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-100"
          : "border-destructive/40 bg-destructive/10 text-destructive",
      )}
    >
      <Sparkles className="h-4 w-4" />
      <span className="flex-1">{data.message || data.error || (ok ? "Done" : "Failed")}</span>
    </div>
  );
}

interface CandidateDetailData {
  application_id: string;
  candidate?: { name?: string | null; email?: string | null };
  role?: { title?: string | null };
  stage?: string;
  fit?: { score?: number | null; tier?: string | null };
  screening?: {
    expected_ctc_lpa?: number | null;
    notice_period_days?: number | null;
    score?: number | null;
  } | null;
  candidate_detail_url?: string;
}

function CandidateDetailCard({ data }: { data: CandidateDetailData }) {
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
          <User className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-semibold">{data.candidate?.name || "(no name)"}</span>
            <StagePill stage={data.stage || "applied"} />
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {[data.candidate?.email, data.role?.title].filter(Boolean).join(" · ")}
          </div>
        </div>
        <Link
          href={data.candidate_detail_url || `/candidates/${data.application_id}`}
          className="text-muted-foreground hover:text-foreground"
        >
          <ExternalLink className="h-4 w-4" />
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4 text-xs">
        {data.fit?.score != null && (
          <div className="rounded-md bg-muted/50 p-2">
            <div className="text-[10px] uppercase text-muted-foreground">fit</div>
            <div className="font-bold tabular-nums">{data.fit.score} <span className="text-[10px] text-muted-foreground">{data.fit.tier}</span></div>
          </div>
        )}
        {data.screening?.score != null && (
          <div className="rounded-md bg-muted/50 p-2">
            <div className="text-[10px] uppercase text-muted-foreground">screening</div>
            <div className="font-bold tabular-nums">{data.screening.score}</div>
          </div>
        )}
        {data.screening?.expected_ctc_lpa != null && (
          <div className="rounded-md bg-muted/50 p-2">
            <div className="text-[10px] uppercase text-muted-foreground">expected</div>
            <div className="font-bold tabular-nums">{data.screening.expected_ctc_lpa} LPA</div>
          </div>
        )}
        {data.screening?.notice_period_days != null && (
          <div className="rounded-md bg-muted/50 p-2">
            <div className="text-[10px] uppercase text-muted-foreground">notice</div>
            <div className="font-bold tabular-nums">{data.screening.notice_period_days}d</div>
          </div>
        )}
      </div>
    </div>
  );
}

interface RescheduleRequestData {
  application_id: string;
  meeting_session_id: string | null;
  round: string;
  candidate_name: string | null;
  role_title: string | null;
  current_scheduled_at: string | null;
  current_label: string | null;
  requested_at: string | null;
  requested_label: string | null;
  reason: string | null;
  suggested_slots: { scheduled_at: string; label: string }[];
}

function RescheduleRequestCard({ data, ctx }: { data: RescheduleRequestData; ctx?: ActionCtx }) {
  const [custom, setCustom] = useState<string | null>(null);
  const dispatch = ctx?.dispatch;
  const round = data.round || "interview";
  const target = data.meeting_session_id
    ? `meeting_session_id: ${data.meeting_session_id}`
    : `application_id: ${data.application_id}`;
  // A precise instruction Pulse maps straight to the reschedule_meeting tool.
  const cmd = (iso: string) =>
    `Reschedule the ${round} interview (${target}) to ${iso}. Keep the same panel.`;

  // Candidate's requested time first (starred), then de-duped suggestions.
  const options: { scheduled_at: string; label: string; preferred?: boolean }[] = [];
  if (data.requested_at) {
    options.push({
      scheduled_at: data.requested_at,
      label: data.requested_label || data.requested_at,
      preferred: true,
    });
  }
  for (const s of data.suggested_slots || []) {
    if (s.scheduled_at !== data.requested_at) options.push(s);
  }

  return (
    <div className="rounded-lg border border-amber-300/60 bg-amber-50/50 p-3.5 text-sm dark:border-amber-700/40 dark:bg-amber-950/20">
      <div className="flex items-center gap-2 font-medium text-foreground">
        <CalendarClock className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <span>
          {data.candidate_name || "Candidate"} requested to reschedule their {round} interview
        </span>
      </div>
      {data.current_label && (
        <div className="mt-1 text-xs text-muted-foreground">Currently booked: {data.current_label}</div>
      )}
      {data.reason && (
        <div className="mt-1 text-xs italic text-muted-foreground">&ldquo;{data.reason}&rdquo;</div>
      )}

      {dispatch ? (
        <>
          <div className="mt-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Pick a new time
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {options.map((o) => (
              <button
                key={o.scheduled_at}
                type="button"
                onClick={() => dispatch(cmd(o.scheduled_at))}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs transition",
                  o.preferred
                    ? "border-primary bg-primary/10 font-medium text-primary hover:bg-primary/20"
                    : "border-border bg-background hover:border-primary/40 hover:bg-accent/40",
                )}
                title={o.preferred ? "Candidate's preferred time" : "Suggested time"}
              >
                {o.preferred ? "★ " : ""}
                {o.label}
              </button>
            ))}
          </div>

          <div className="mt-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Or a custom time
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <QuickDateTime onChange={setCustom} />
            <button
              type="button"
              disabled={!custom}
              onClick={() => custom && dispatch(cmd(custom))}
              className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              Reschedule
            </button>
          </div>
        </>
      ) : (
        <div className="mt-2 text-xs text-muted-foreground">
          Open this conversation to pick a new time.
        </div>
      )}
    </div>
  );
}

export function AttachmentRenderer({
  att,
  conversationId,
  dispatch,
}: {
  att: Attachment;
  conversationId?: string | null;
  dispatch?: (msg: string) => void;
}) {
  const ctx: ActionCtx = { dispatch };
  switch (att.kind) {
    case "reschedule-request":
      return <RescheduleRequestCard data={att.data as RescheduleRequestData} ctx={ctx} />;
    case "candidate-list":
      return <CandidateListCard items={(att.data as CandidateRow[]) || (att.raw as { items?: CandidateRow[] })?.items || []} ctx={ctx} />;
    case "role-list":
      return <RoleListCard items={(att.data as RoleRow[]) || (att.raw as { items?: RoleRow[] })?.items || []} />;
    case "stuck-list":
      return <StuckListCard items={(att.data as StuckRow[]) || (att.raw as { items?: StuckRow[] })?.items || []} />;
    case "audit-list":
      return <AuditListCard items={(att.data as AuditRow[]) || (att.raw as { items?: AuditRow[] })?.items || []} />;
    case "metrics":
      return <MetricsCard data={(att.data as MetricsData) || {}} />;
    case "candidate-detail":
      return <CandidateDetailCard data={(att.data as CandidateDetailData) || { application_id: "" }} />;
    case "action-result":
      return <ActionResultCard data={(att.data as ActionResultData) || {}} />;
    case "confirm-card":
      if (!conversationId) return null;
      return (
        <ConfirmCard
          data={att.data as { request_id: string; tool: string; args: Record<string, unknown>; preview: string }}
          conversationId={conversationId}
        />
      );
    case "nudge-card":
      return <NudgeCard data={att.data as { application_id: string; event: string; candidate_name: string | null; role_title: string | null; ts: string }} />;
    case "linkedin-post":
      return <LinkedInPostCard data={(att.data as Parameters<typeof LinkedInPostCard>[0]["data"]) || {}} />;
    case "role-created": {
      const d = att.data as { role_id?: string; role?: { title?: string }; assignment?: { problem_count?: number; error?: string } };
      const assignmentFailed = !!d?.assignment?.error;
      const problemCount = d?.assignment?.problem_count ?? 0;
      return (
        <div className="rounded-lg border border-emerald-300/50 bg-emerald-50/50 p-3 text-sm dark:border-emerald-700/40 dark:bg-emerald-950/20">
          <div className="font-semibold text-emerald-900 dark:text-emerald-100">
            Role &quot;{d?.role?.title || "(untitled)"}&quot; created
          </div>
          {assignmentFailed ? (
            <div className="mt-0.5 text-xs text-amber-600 dark:text-amber-400">
              Role saved — assignment generation failed. You can retry from the role page.
            </div>
          ) : (
            <div className="mt-0.5 text-xs text-muted-foreground">
              {problemCount} take-home problem{problemCount === 1 ? "" : "s"} saved.
            </div>
          )}
          <div className="mt-2 flex items-center gap-3">
            {d?.role_id && (
              <Link
                href={`/roles/${d.role_id}`}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                Open role <ExternalLink className="h-3 w-3" />
              </Link>
            )}
            <Link
              href="/roles"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              All roles <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </div>
      );
    }
    default:
      return null;
  }
}
