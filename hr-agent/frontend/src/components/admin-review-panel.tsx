"use client";

import { useState } from "react";
import { Check, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { PI_PERSONAS } from "@/lib/pi-personas";
import { MeetingReportCard } from "@/components/meeting-report-card";
import type { StageViewEntry } from "@/lib/types";

type Stage = string;

/**
 * Where the review panel is being rendered. Overview ("any") is the universal
 * action surface and shows whatever review is pending; each stage tab passes
 * its own surface so it only renders the review that belongs to that stage.
 */
export type ReviewSurface = "any" | "overview" | "fit" | "voice" | "assessment" | "interview";

function stageTypeToSurface(stageType: string | undefined): ReviewSurface {
  switch (stageType) {
    case "voice_screen":
      return "voice";
    case "assignment":
      return "assessment";
    case "fit":
      return "fit";
    default:
      // fit/screening-style stages have no dedicated tab → Overview only.
      return "overview";
  }
}

interface ScoreRationale {
  overall?: string;
  technical?: string;
  communication?: string;
  confidence?: string;
}

interface MeetingReport {
  overall_score?: number | null;
  technical_score?: number | null;
  communication_score?: number | null;
  confidence_score?: number | null;
  score_rationale?: ScoreRationale | null;
  verdict?: string | null;
  summary?: string | null;
  strengths?: string[] | null;
  red_flags?: string[] | null;
  highlights?: string[] | null;
}

interface RoundReview {
  decision?: string;
  notes?: string | null;
  pi_assessment_link?: string | null;
  pi_persona?: string | null;
  reviewer?: string | null;
}

interface Props {
  applicationId: string;
  currentStage: Stage;
  stageStatus?: string | null;
  stageView?: StageViewEntry[] | null;
  meetingReports?: {
    technical?: MeetingReport | null;
    ceo?: MeetingReport | null;
    hr?: MeetingReport | null;
  };
  adminReview?: Record<string, RoundReview> | null;
  /** Which surface this instance is rendered on. Defaults to "any" (Overview). */
  surface?: ReviewSurface;
  onChanged: () => void;
}

export function AdminReviewPanel({ applicationId, currentStage, stageStatus, stageView, meetingReports, adminReview, surface = "any", onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");
  const [piLink, setPiLink] = useState("");
  const [piPersona, setPiPersona] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  // Overview ("any") renders whatever review is pending; a stage tab only renders
  // the review that belongs to it, so the same panel can live in both places
  // without a borderline-fit form leaking onto, say, the Voice tab.
  const matches = (target: ReviewSurface) => surface === "any" || surface === target;

  // If a decision was already recorded for the matching round, suppress the
  // panel even if the stage somehow regressed -- prevents double-decision.
  const stageRound = stageToRoundKey(currentStage);
  // ceo's human-facing name is the Management Round (stage_key stays "ceo").
  const roundDisplay = (r: string) =>
    r === "ceo" ? "Management" : r[0].toUpperCase() + r.slice(1);
  if (stageRound && adminReview?.[stageRound]?.decision && matches(stageRound === "assessment" ? "assessment" : "interview")) {
    return (
      <div className="rounded-xl border border-border bg-muted/40 p-4 text-sm">
        <div className="font-semibold">{roundDisplay(stageRound)} round decided</div>
        <div className="mt-1 text-muted-foreground">
          {adminReview[stageRound].decision}
          {adminReview[stageRound].reviewer ? ` by ${adminReview[stageRound].reviewer}` : ""}
        </div>
      </div>
    );
  }

  // Generic borderline-review gate: any scoring stage (fit/screening/voice/
  // interview) can land in `needs_review`, which PARKS the candidate and records
  // verdict="needs_review" on that stage. HR confirms pass (advance) or reject.
  const reviewStage = (stageView ?? []).find((s) => s.verdict === "needs_review");
  if (reviewStage && stageStatus === "parked" && matches(stageTypeToSurface(reviewStage.stage_type))) {
    return (
      <Panel title={`${reviewStage.label || reviewStage.stage_key} — borderline, needs review`}>
        <p className="text-sm text-muted-foreground">
          This candidate scored in the borderline band at the{" "}
          <span className="font-medium text-foreground">
            {reviewStage.label || reviewStage.stage_key}
          </span>{" "}
          stage and was parked for a human call. Pass advances them through the
          pipeline; reject ends the application.
        </p>
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit(`/dashboard/v1/candidates/${applicationId}/resolve-review`, {
              decision: "pass",
              note: notes || null,
            })
          }
          onReject={() =>
            submit(`/dashboard/v1/candidates/${applicationId}/resolve-review`, {
              decision: "reject",
              note: notes || null,
            })
          }
          approveLabel="Pass"
          rejectLabel="Reject"
        />
      </Panel>
    );
  }

  if (currentStage === "needs_hr_review" && matches("voice")) {
    return (
      <Panel title="Voice screen — HR review needed">
        <p className="text-sm text-muted-foreground">
          Phone screen completed. Review the conversation, score, and verdict below.
          Push the candidate into the assessment round (sends assignment email) or reject.
        </p>
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit("/agentic/voice-screen/promote", {
              application_id: applicationId,
              note: notes,
            })
          }
          onReject={() =>
            submit("/dashboard/v1/candidates/" + applicationId + "/reject", {
              category: "screening_call",
              reason_template: notes || "Did not meet bar at phone-screen stage.",
            })
          }
          approveLabel="Push to assessment"
          rejectLabel="Reject (screening)"
        />
      </Panel>
    );
  }

  if ((currentStage === "assessment_pending_review" || currentStage === "assessment_completed") && matches("assessment")) {
    return (
      <Panel title="Assessment review needed">
        <p className="text-sm text-muted-foreground">
          Candidate completed PI cognitive + assignment. Capture the PI assessment link, persona, and decide whether to advance to the technical round.
        </p>
        <Field label="PI assessment link">
          <input
            type="url"
            value={piLink}
            onChange={(e) => setPiLink(e.target.value)}
            placeholder="https://..."
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          />
        </Field>
        <Field label="PI persona">
          <select
            value={piPersona}
            onChange={(e) => setPiPersona(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
          >
            <option value="">— select —</option>
            {PI_PERSONAS.map((p) => (
              <option key={p} value={p.toLowerCase()}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit("/agentic/assessment/review", {
              application_id: applicationId,
              decision: "approve",
              pi_assessment_link: piLink || null,
              pi_persona: piPersona || null,
              notes: notes || null,
            })
          }
          onReject={() =>
            submit("/agentic/assessment/review", {
              application_id: applicationId,
              decision: "reject",
              notes: notes || null,
            })
          }
          approveLabel="Approve → schedule technical"
        />
      </Panel>
    );
  }

  if ((currentStage === "technical_pending_approval" || currentStage === "technical_evaluated") && matches("interview")) {
    return (
      <Panel title="Technical round complete — needs approval">
        <MeetingReportCard report={meetingReports?.technical} />
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit("/agentic/technical/approve", {
              application_id: applicationId,
              decision: "approve",
              notes: notes || null,
            })
          }
          onReject={() =>
            submit("/agentic/technical/approve", {
              application_id: applicationId,
              decision: "reject",
              notes: notes || null,
            })
          }
          approveLabel="Approve → schedule Management round"
        />
      </Panel>
    );
  }

  if (currentStage === "ceo_pending_approval" && matches("interview")) {
    return (
      <Panel title="Management round complete — needs approval">
        <MeetingReportCard report={meetingReports?.ceo} />
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit("/agentic/ceo/approve", {
              application_id: applicationId,
              decision: "approve",
              notes: notes || null,
            })
          }
          onReject={() =>
            submit("/agentic/ceo/approve", {
              application_id: applicationId,
              decision: "reject",
              notes: notes || null,
            })
          }
          approveLabel="Approve → schedule HR"
        />
      </Panel>
    );
  }

  if ((currentStage === "hr_evaluated" || currentStage === "hr_meeting_completed") && matches("interview")) {
    return (
      <Panel title="HR discussion complete — finalize">
        <MeetingReportCard report={meetingReports?.hr} />
        <NotesField notes={notes} setNotes={setNotes} />
        <ActionRow
          busy={busy}
          error={error}
          onApprove={() =>
            submit("/agentic/hr/finalize", {
              application_id: applicationId,
              decision: "hired",
              notes: notes || null,
            })
          }
          onReject={() =>
            submit("/agentic/hr/finalize", {
              application_id: applicationId,
              decision: "rejected",
              notes: notes || null,
            })
          }
          approveLabel="Mark hired"
          rejectLabel="Mark rejected"
        />
      </Panel>
    );
  }

  return null;

  async function submit(path: string, body: Record<string, unknown>) {
    const decision = body.decision as string;
    if (decision === "reject" || decision === "rejected") {
      if (!window.confirm("Reject this candidate? This cannot be undone.")) return;
    }
    if (decision === "hired") {
      if (!window.confirm("Mark this candidate as hired and send the offer email?")) return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(path, body);
      onChanged();
    } catch (e: any) {
      setError(e?.detail?.detail ?? e?.message ?? "Action failed");
    } finally {
      setBusy(false);
    }
  }
}

function stageToRoundKey(stage: string): string | null {
  if (stage.startsWith("assessment_")) return "assessment";
  if (stage.startsWith("technical_")) return "technical";
  if (stage.startsWith("ceo_")) return "ceo";
  if (stage.startsWith("hr_")) return "hr";
  return null;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3 rounded-xl border border-warning/40 bg-warning/5 p-4">
      <h3 className="text-sm font-bold uppercase tracking-wider text-foreground">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

function NotesField({ notes, setNotes }: { notes: string; setNotes: (s: string) => void }) {
  return (
    <Field label="Notes (optional)">
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={3}
        className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
      />
    </Field>
  );
}

function ActionRow({
  busy,
  error,
  onApprove,
  onReject,
  approveLabel,
  rejectLabel = "Reject",
}: {
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onReject: () => void;
  approveLabel: string;
  rejectLabel?: string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onApprove} disabled={busy}>
          {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-1.5 h-3.5 w-3.5" />}
          {approveLabel}
        </Button>
        <Button size="sm" variant="outline" onClick={onReject} disabled={busy}>
          <X className="mr-1.5 h-3.5 w-3.5" />
          {rejectLabel}
        </Button>
      </div>
      {error ? <p className="text-xs text-rose-600">{error}</p> : null}
    </div>
  );
}
