"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  Check,
  X,
  Mail,
  FileText,
  AlertTriangle,
  MessageCircle,
  RefreshCw,
  Briefcase,
  Phone,
  Star,
} from "lucide-react";

interface AuditEntry {
  id: number;
  action: string;
  actor: string;
  details: any;
  created_at: string;
}

const ICON_MAP: Record<string, { icon: typeof Check; color: string }> = {
  intake_completed: { icon: Mail, color: "bg-info/15 text-info" },
  acknowledgement_sent: { icon: Mail, color: "bg-info/15 text-info" },
  screening_questions_generated: { icon: FileText, color: "bg-secondary/15 text-secondary" },
  screening_sent: { icon: Mail, color: "bg-secondary/15 text-secondary" },
  screening_submitted: { icon: Check, color: "bg-success/15 text-success" },
  screening_evaluated: { icon: Check, color: "bg-success/15 text-success" },
  fit_scored: { icon: Star, color: "bg-amber-100 text-amber-600" },
  fit_auto_rejected: { icon: X, color: "bg-destructive/15 text-destructive" },
  auto_rejected_fit: { icon: X, color: "bg-destructive/15 text-destructive" },
  voice_call_initiated: { icon: Phone, color: "bg-secondary/15 text-secondary" },
  voice_call_completed: { icon: Phone, color: "bg-success/15 text-success" },
  voice_call_evaluated: { icon: Check, color: "bg-success/15 text-success" },
  assignment_sent: { icon: Briefcase, color: "bg-warning/15 text-warning" },
  assignment_submitted: { icon: Check, color: "bg-success/15 text-success" },
  assignment_parsed: { icon: FileText, color: "bg-secondary/15 text-secondary" },
  journey_report_generated: { icon: FileText, color: "bg-success/15 text-success" },
  candidate_rejected: { icon: X, color: "bg-destructive/15 text-destructive" },
  candidate_merged: { icon: RefreshCw, color: "bg-info/15 text-info" },
  pipeline_error: { icon: AlertTriangle, color: "bg-destructive/15 text-destructive" },
  parked_parse_failed: { icon: AlertTriangle, color: "bg-destructive/15 text-destructive" },
  verdict_downgraded: { icon: AlertTriangle, color: "bg-warning/15 text-warning" },
  hr_stage_override: { icon: RefreshCw, color: "bg-info/15 text-info" },
  role_reassigned: { icon: Briefcase, color: "bg-info/15 text-info" },
  dedup_duplicate_application_withdrawn: { icon: RefreshCw, color: "bg-muted text-muted-foreground" },
};

const DEFAULT_ICON = { icon: MessageCircle, color: "bg-muted text-muted-foreground" };

const LABELS: Record<string, string> = {
  intake_completed: "Application received",
  acknowledgement_sent: "Acknowledgement email sent",
  screening_questions_generated: "Screening questions generated",
  screening_sent: "Screening link sent",
  screening_submitted: "Screening submitted",
  screening_evaluated: "Screening evaluated",
  fit_scored: "Fit score computed",
  fit_auto_rejected: "Auto-rejected (low fit)",
  auto_rejected_fit: "Auto-rejected (low fit score)",
  voice_call_initiated: "Voice call initiated",
  voice_call_completed: "Voice call completed",
  voice_call_evaluated: "Voice call evaluated",
  assignment_sent: "Assignment sent",
  assignment_submitted: "Assignment received",
  assignment_parsed: "Assignment reviewed",
  journey_report_generated: "Journey report generated",
  candidate_rejected: "Rejected",
  candidate_merged: "Merged with existing candidate",
  pipeline_error: "Pipeline error",
  parked_parse_failed: "Parse failed, needs HR review",
  verdict_downgraded: "Downgraded to HR review",
  hr_stage_override: "HR stage override",
  role_reassigned: "Role reassigned",
  journey_report_requeued: "Report re-queued",
  screening_eval_requeued: "Screening eval re-queued",
  dedup_duplicate_application_withdrawn: "Duplicate application withdrawn",
};

function extractDetail(entry: AuditEntry): string | null {
  const d = entry.details;
  if (!d) return null;

  switch (entry.action) {
    case "candidate_rejected":
    case "fit_auto_rejected":
    case "auto_rejected_fit":
      return d.reason || d.rejection_reason || d.category || d.summary || (
        d.fit_tier ? `Fit tier: ${d.fit_tier}${d.fit_score != null ? `, score: ${d.fit_score}` : ""}` : null
      );
    case "pipeline_error":
    case "parked_parse_failed":
      return d.error || d.reason || d.stage || null;
    case "fit_scored":
      return d.summary || (d.overall_score != null ? `Score: ${d.overall_score}, tier: ${d.deterministic_tier || "unknown"}` : null);
    case "screening_evaluated":
      return d.composite_score != null ? `Score: ${d.composite_score}` : null;
    case "voice_call_completed":
      return d.decision || d.status || null;
    case "role_reassigned":
      return d.note || null;
    case "dedup_duplicate_application_withdrawn":
      return d.reason || null;
    case "candidate_merged":
      return d.placeholder_id ? `Merged from ${d.placeholder_id.slice(0, 8)}...` : null;
    default:
      return null;
  }
}

export function ActivityTimeline({ entries }: { entries: AuditEntry[] }) {
  const [showAll, setShowAll] = useState(false);
  const sorted = [...entries].sort((a, b) => b.id - a.id);
  const visible = showAll ? sorted : sorted.slice(0, 10);
  const remaining = sorted.length - visible.length;

  if (entries.length === 0) {
    return <p className="text-sm italic text-muted-foreground">No activity yet.</p>;
  }

  return (
    <div className="relative">
      <div className="absolute left-4 top-0 bottom-0 w-px bg-border" />
      <ul className="space-y-0">
        {visible.map((entry) => {
          const { icon: Icon, color } = ICON_MAP[entry.action] ?? DEFAULT_ICON;
          const label = LABELS[entry.action] ?? entry.action.replace(/_/g, " ");
          const detail = extractDetail(entry);
          return (
            <li key={entry.id} className="relative flex gap-3 pb-4">
              <div className={cn("relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full", color)}>
                <Icon className="h-3.5 w-3.5" />
              </div>
              <div className="min-w-0 pt-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium">{label}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    by {entry.actor}
                  </span>
                </div>
                {detail && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
                )}
                <time className="font-mono text-[11px] tabular-nums text-muted-foreground">
                  {new Date(entry.created_at).toLocaleString("en-IN", {
                    timeZone: "Asia/Kolkata",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
              </div>
            </li>
          );
        })}
      </ul>
      {remaining > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="ml-11 rounded-full border border-border px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground hover:border-foreground hover:text-foreground transition"
        >
          show {remaining} more
        </button>
      )}
    </div>
  );
}
