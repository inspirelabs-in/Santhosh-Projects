import { cn } from "@/lib/utils";

export type Stage =
  // V1 stages
  | "applied"
  | "screening_sent"
  | "screening_submitted"
  | "screening_evaluated"
  | "needs_hr_review"
  | "assignment_sent"
  | "assignment_submitted"
  | "report_ready"
  // V2 agentic stages
  | "voice_screen_pending"
  | "voice_screen_dialing"
  | "voice_screen_scheduled"
  | "voice_screen_in_progress"
  | "voice_screen_callback_requested"
  | "voice_screen_completed"
  | "voice_screen_evaluated"
  | "voice_screen_failed"
  | "voice_screen_no_answer"
  | "voice_screen_declined"
  | "assessment_invited"
  | "assessment_completed"
  | "assessment_evaluated"
  | "technical_meeting_scheduled"
  | "technical_meeting_in_progress"
  | "technical_meeting_completed"
  | "technical_evaluated"
  | "ceo_meeting_scheduled"
  | "ceo_meeting_in_progress"
  | "ceo_meeting_completed"
  // Terminal
  | "rejected"
  | "hired";

export const STAGE_LABELS: Record<Stage, string> = {
  applied: "applied",
  screening_sent: "screening sent",
  screening_submitted: "screening submitted",
  screening_evaluated: "screening evaluated",
  needs_hr_review: "needs hr review",
  assignment_sent: "assignment sent",
  assignment_submitted: "assignment submitted",
  report_ready: "report ready",
  voice_screen_pending: "queued",
  voice_screen_dialing: "dialing candidate",
  voice_screen_scheduled: "voice screen scheduled",
  voice_screen_in_progress: "on call",
  voice_screen_callback_requested: "callback requested",
  voice_screen_completed: "call ended",
  voice_screen_evaluated: "answers validated",
  voice_screen_failed: "call failed",
  voice_screen_no_answer: "did not pick up",
  voice_screen_declined: "candidate declined",
  assessment_invited: "assessment invited",
  assessment_completed: "assessment completed",
  assessment_evaluated: "assessment evaluated",
  technical_meeting_scheduled: "technical scheduled",
  technical_meeting_in_progress: "technical in progress",
  technical_meeting_completed: "technical completed",
  technical_evaluated: "technical evaluated",
  ceo_meeting_scheduled: "ceo scheduled",
  ceo_meeting_in_progress: "ceo in progress",
  ceo_meeting_completed: "ceo completed",
  rejected: "rejected",
  hired: "hired",
};

export const STAGE_ORDER: Stage[] = [
  "applied",
  "screening_sent",
  "screening_submitted",
  "screening_evaluated",
  "voice_screen_scheduled",
  "voice_screen_completed",
  "voice_screen_evaluated",
  "assessment_invited",
  "assessment_completed",
  "assessment_evaluated",
  "technical_meeting_scheduled",
  "technical_meeting_completed",
  "technical_evaluated",
  "ceo_meeting_scheduled",
  "ceo_meeting_completed",
  "assignment_sent",
  "assignment_submitted",
  "report_ready",
];

type Style = { bg: string; dot: string; text: string; border?: string };

const NEUTRAL: Style = { bg: "bg-card",         dot: "bg-muted-foreground/60", text: "text-muted-foreground" };
const ACCENT:  Style = { bg: "bg-accent/40",    dot: "bg-primary",             text: "text-accent-foreground" };
const INFO:    Style = { bg: "bg-info/10",      dot: "bg-info",                text: "text-info" };
const WARN:    Style = { bg: "bg-warning/15",   dot: "bg-warning",             text: "text-foreground" };
const SUCCESS: Style = { bg: "bg-success/15",   dot: "bg-success",             text: "text-foreground" };
const DANGER:  Style = { bg: "bg-destructive/10", dot: "bg-destructive",       text: "text-destructive" };
const HIRED:   Style = { bg: "bg-primary",      dot: "bg-primary-foreground",  text: "text-primary-foreground" };

const styles: Record<Stage, Style> = {
  applied:                          NEUTRAL,
  screening_sent:                   ACCENT,
  screening_submitted:              ACCENT,
  screening_evaluated:              ACCENT,
  needs_hr_review:                  { ...WARN, border: "border border-warning/40" },
  assignment_sent:                  ACCENT,
  assignment_submitted:             SUCCESS,
  report_ready:                     SUCCESS,
  voice_screen_pending:             NEUTRAL,
  voice_screen_dialing:             INFO,
  voice_screen_scheduled:           INFO,
  voice_screen_in_progress:         INFO,
  voice_screen_callback_requested:  WARN,
  voice_screen_completed:           INFO,
  voice_screen_evaluated:           SUCCESS,
  voice_screen_failed:              DANGER,
  voice_screen_no_answer:           WARN,
  voice_screen_declined:            DANGER,
  assessment_invited:               INFO,
  assessment_completed:             INFO,
  assessment_evaluated:             SUCCESS,
  technical_meeting_scheduled:      INFO,
  technical_meeting_in_progress:    INFO,
  technical_meeting_completed:      INFO,
  technical_evaluated:              SUCCESS,
  ceo_meeting_scheduled:            { ...ACCENT, border: "border border-primary/40" },
  ceo_meeting_in_progress:          { ...ACCENT, border: "border border-primary/40" },
  ceo_meeting_completed:            SUCCESS,
  rejected:                         DANGER,
  hired:                            HIRED,
};

export function StatusTag({ stage, className }: { stage: Stage | string; className?: string }) {
  const key = (stage as Stage) in styles ? (stage as Stage) : "applied";
  const s = styles[key];
  const label = STAGE_LABELS[key] ?? String(stage);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] leading-none",
        s.bg,
        s.text,
        s.border,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
      {label}
    </span>
  );
}
