"use client";

import { Check, Minus, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Horizontal pipeline stepper — shows actual progress, not misleading "done"
 * marks for stages the candidate hasn't reached.
 *
 * `needs_hr_review` and `report_ready` are parking stages that can happen at
 * various points. We use the audit log to infer the actual furthest stage.
 */

const STAGE_TO_STEP: Record<string, number> = {
  applied: 0,
  screening_sent: 1,
  screening_submitted: 1,
  screening_evaluated: 1,
  voice_screen_scheduled: 2,
  voice_screen_in_progress: 2,
  voice_screen_callback_requested: 2,
  voice_screen_completed: 2,
  voice_screen_evaluated: 2,
  assessment_invited: 3,
  assessment_completed: 3,
  assessment_pending_review: 3,
  assessment_evaluated: 3,
  assignment_sent: 3,
  assignment_submitted: 3,
  technical_meeting_scheduled: 4,
  technical_meeting_in_progress: 4,
  technical_meeting_completed: 4,
  technical_evaluated: 4,
  technical_pending_approval: 4,
  ceo_meeting_scheduled: 5,
  ceo_meeting_in_progress: 5,
  ceo_meeting_completed: 5,
  ceo_pending_approval: 5,
  hr_meeting_scheduled: 6,
  hr_meeting_in_progress: 6,
  hr_meeting_completed: 6,
  hr_evaluated: 6,
  report_ready: 6,
  hired: 7,
  rejected: 7,
};

const AUDIT_ACTION_TO_STEP: Record<string, number> = {
  intake_completed: 0,
  fit_scored: 1,
  screening_questions_generated: 1,
  screening_sent: 1,
  screening_submitted: 1,
  screening_evaluated: 1,
  voice_screen_scheduled: 2,
  voice_screen_completed: 2,
  voice_screen_evaluated: 2,
  assignment_sent: 3,
  assignment_submitted: 3,
  assignment_parsed: 3,
  tech_review_requested: 4,
  journey_report_generated: 4,
  ceo_review_requested: 5,
  hr_review_requested: 6,
};

const PENDING_STAGES = new Set([
  "assessment_pending_review",
  "technical_pending_approval",
  "ceo_pending_approval",
  "hr_evaluated",
]);

const STEPS = [
  { key: "applied", label: "Applied" },
  { key: "fit_score", label: "Fit Score" },
  { key: "voice", label: "Screening" },
  { key: "assessment", label: "Assessment" },
  { key: "technical", label: "Technical" },
  { key: "ceo", label: "CEO" },
  { key: "hr", label: "HR" },
  { key: "outcome", label: "Outcome" },
];

function inferActualStep(
  currentStage: string,
  auditActions: string[],
  rejectedFromStage?: string | null,
): number {
  const isRejected = currentStage === "rejected";
  const isParked = currentStage === "needs_hr_review";

  if (isRejected && rejectedFromStage) {
    return STAGE_TO_STEP[rejectedFromStage] ?? 0;
  }

  if (isParked || currentStage === "report_ready") {
    let maxStep = 0;
    for (const action of auditActions) {
      const step = AUDIT_ACTION_TO_STEP[action];
      if (step !== undefined && step > maxStep) maxStep = step;
    }
    return maxStep || 2;
  }

  return STAGE_TO_STEP[currentStage] ?? 0;
}

export function PipelineStepper({
  currentStage,
  rejectedFromStage,
  auditActions,
  className,
}: {
  currentStage: string;
  rejectedFromStage?: string | null;
  auditActions?: string[];
  className?: string;
}) {
  const isRejected = currentStage === "rejected";
  const isHired = currentStage === "hired";
  const isParked = currentStage === "needs_hr_review";
  const isPendingAdmin = PENDING_STAGES.has(currentStage);

  const idx = inferActualStep(
    currentStage,
    auditActions ?? [],
    rejectedFromStage,
  );

  const parkedLabel = isParked
    ? STEPS[idx]?.label.toLowerCase() ?? "screening"
    : null;

  return (
    <div className={cn("rounded-xl border border-border bg-card p-5", className)}>
      <div className="flex items-center">
        {STEPS.map((step, i) => {
          const last = i === STEPS.length - 1;
          let state: "done" | "active" | "parked" | "todo" | "rejected" = "todo";

          if (isRejected) {
            if (i < idx) state = "done";
            else if (i === idx) state = "rejected";
          } else if (isHired) {
            state = "done";
          } else if (i < idx) {
            state = "done";
          } else if (i === idx) {
            state = isParked ? "parked" : "active";
          }

          return (
            <div key={step.key} className={cn("flex items-center", !last && "flex-1")}>
              <div className="flex flex-col items-center gap-1.5">
                <span
                  className={cn(
                    "relative flex h-8 w-8 items-center justify-center rounded-full border-2 text-xs font-bold transition-all",
                    state === "done" && "border-primary bg-primary text-primary-foreground",
                    state === "active" && "border-primary bg-primary/10 text-primary",
                    state === "parked" && "border-warning bg-warning/10 text-warning",
                    state === "rejected" && "border-destructive bg-destructive text-destructive-foreground",
                    state === "todo" && "border-muted bg-muted/30 text-muted-foreground",
                  )}
                >
                  {state === "done" ? (
                    <Check className="h-4 w-4" strokeWidth={3} />
                  ) : state === "rejected" ? (
                    <span className="text-sm">×</span>
                  ) : state === "parked" ? (
                    <AlertCircle className="h-4 w-4" />
                  ) : state === "active" ? (
                    <>
                      <span>{i + 1}</span>
                      <span className="absolute inset-0 animate-ping rounded-full bg-primary/20" />
                    </>
                  ) : (
                    <span className="text-muted-foreground/60">{i + 1}</span>
                  )}
                </span>
                <span
                  className={cn(
                    "whitespace-nowrap text-center font-mono text-[10px] uppercase tracking-wider",
                    state === "done" && "text-foreground font-semibold",
                    state === "active" && "text-primary font-bold",
                    state === "parked" && "text-warning font-bold",
                    state === "rejected" && "text-destructive font-semibold",
                    state === "todo" && "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
              </div>
              {!last && (
                <div className="mx-1.5 h-[2px] flex-1 -translate-y-2.5 rounded-full overflow-hidden">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      i < idx ? "bg-primary" : "bg-muted",
                    )}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {isParked && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-4 py-2.5">
          <AlertCircle className="h-4 w-4 shrink-0 text-warning" />
          <p className="text-sm text-foreground">
            Parked for HR review after <span className="font-semibold">{parkedLabel}</span> stage
          </p>
        </div>
      )}
      {isRejected && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2.5">
          <span className="text-sm text-destructive">
            Rejected at the <span className="font-semibold">{STEPS[idx]?.label.toLowerCase() ?? "screening"}</span> stage
          </span>
        </div>
      )}
      {isPendingAdmin && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-4 py-2.5">
          <AlertCircle className="h-4 w-4 shrink-0 text-warning" />
          <p className="text-sm text-foreground">
            Awaiting admin review at <span className="font-semibold">{STEPS[idx]?.label.toLowerCase()}</span> stage
          </p>
        </div>
      )}
    </div>
  );
}
