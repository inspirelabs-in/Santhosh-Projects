"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ArrowLeft,
  Download,
  ThumbsUp,
  ThumbsDown,
  AlertTriangle,
  ClipboardCheck,
  FileSearch,
  Scale,
  Brain,
  Phone,
  Mail,
  MapPin,
  Briefcase,
  GraduationCap,
  ExternalLink,
  MessageSquare,
  Clock,
  Github,
  Video,
  Globe,
  FileCode,
  ArrowRight,
  Loader2,
  CheckCircle2,
  Calendar,
  User,
} from "lucide-react";
import { useState } from "react";

import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { StatusTag, type Stage } from "@/components/status-tag";
import { SkeletonLines } from "@/components/skeleton";
import { swrFetcher, api } from "@/lib/api";
import { ActivityTimeline } from "@/components/candidate-detail/activity-timeline";
import { PipelineStepper } from "@/components/candidate-detail/pipeline-stepper";
import { AdminReviewPanel } from "@/components/admin-review-panel";
import type { StageViewEntry, CriterionScore, DimensionScore, FitAssessment } from "@/lib/types";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Tab definitions                                                    */
/* ------------------------------------------------------------------ */

const TABS = ["Overview", "Screening", "Assignment", "Voice", "Interviews", "Timeline"] as const;
type TabName = (typeof TABS)[number];

/* ------------------------------------------------------------------ */
/*  Tier / score badge                                                */
/* ------------------------------------------------------------------ */

function TierBadge({ tier, score }: { tier?: string; score?: number }) {
  const tierLabel = tier === "amber" ? "review" : tier;
  const colors: Record<string, string> = {
    green: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    amber: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    red: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  };
  if (!tier && score == null) return null;
  return (
    <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-semibold", colors[tier ?? ""] ?? "bg-muted text-muted-foreground")}>
      {score != null && <>{score}</>}
      {score != null && tierLabel && " · "}
      {tierLabel && <>{tierLabel}</>}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Profile snapshot                                                  */
/* ------------------------------------------------------------------ */

function ProfileSnapshot({ profile }: { profile: any }) {
  if (!profile) return null;
  const skills = profile.skills?.slice(0, 12) ?? [];
  const experience = profile.work_history ?? [];
  const education = profile.education ?? [];

  return (
    <div className="space-y-5">
      {profile.summary && (
        <p className="text-sm leading-relaxed text-muted-foreground">{profile.summary}</p>
      )}

      {skills.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Skills</h4>
          <div className="flex flex-wrap gap-1.5">
            {skills.map((s: string, i: number) => (
              <span key={i} className="rounded-md border bg-muted/50 px-2 py-0.5 text-xs">{s}</span>
            ))}
            {(profile.skills?.length ?? 0) > 12 && (
              <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">+{profile.skills.length - 12} more</span>
            )}
          </div>
        </div>
      )}

      {experience.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Briefcase className="mr-1 inline h-3 w-3" />Experience
          </h4>
          <div className="space-y-2">
            {experience.map((w: any, i: number) => (
              <div key={i} className="flex items-start gap-2">
                <div className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60 mt-[7px]" />
                <div className="text-sm">
                  <span className="font-medium">{w.role ?? w.title}</span>
                  {w.company && <span className="text-muted-foreground"> at {w.company}</span>}
                  {w.duration && <span className="ml-2 text-xs text-muted-foreground">({w.duration})</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {education.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <GraduationCap className="mr-1 inline h-3 w-3" />Education
          </h4>
          <div className="space-y-2">
            {education.map((e: any, i: number) => (
              <div key={i} className="flex items-start gap-2">
                <div className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                <div className="text-sm">
                  <span className="font-medium">{e.degree}</span>
                  {e.institution && <span className="text-muted-foreground"> — {e.institution}</span>}
                  {e.year && <span className="ml-2 text-xs text-muted-foreground">({e.year})</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Fit Score Breakdown                                               */
/* ------------------------------------------------------------------ */

function DimRow({
  label,
  weight,
  score,
  rationale,
  evidence,
  data_status,
}: {
  label: string;
  weight: number | null;
  score: number | null;
  rationale?: string;
  evidence?: string[];
  data_status: "verified" | "pending_verification";
}) {
  const isPending = data_status === "pending_verification" || score == null;
  return (
    <div className={cn("rounded-lg border p-3", isPending && "border-dashed border-muted-foreground/30 bg-muted/20")}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{label}</span>
          {weight != null && (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] font-semibold tabular-nums text-muted-foreground">
              w {weight}
            </span>
          )}
          {isPending ? (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-amber-700">Pending</span>
          ) : (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-emerald-700">Verified</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {weight != null && (
            <span className="text-[10px] text-muted-foreground/60">×{weight}%</span>
          )}
          {isPending ? (
            <span className="text-xs italic text-muted-foreground">No data</span>
          ) : (
            <span className={cn("font-mono text-sm font-bold", (score ?? 0) >= 60 ? "text-emerald-600" : "text-red-600")}>{score}</span>
          )}
        </div>
      </div>
      {rationale && <p className="mt-1 text-xs text-muted-foreground">{rationale}</p>}
      {evidence && evidence.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {evidence.map((e: string, j: number) => (
            <p key={j} className="text-[11px] italic text-muted-foreground/80">&ldquo;{e}&rdquo;</p>
          ))}
        </div>
      )}
    </div>
  );
}

function FitBreakdownContent({ breakdown }: { breakdown: FitAssessment | null | undefined }) {
  if (!breakdown) return null;

  const criteriaScores: CriterionScore[] = Array.isArray(breakdown.criteria_scores)
    ? breakdown.criteria_scores
    : [];

  const weightsUsed: Record<string, number> = breakdown.weights_used ?? {};
  const fixedDims: Array<{ label: string; weight: number | null; dim: DimensionScore }> =
    criteriaScores.length === 0
      ? (
          [
            breakdown.dimensions?.skills_match
              ? { label: "skills match", weight: weightsUsed["skills"] ?? null, dim: breakdown.dimensions.skills_match }
              : null,
            breakdown.dimensions?.experience_level
              ? { label: "experience level", weight: weightsUsed["experience"] ?? null, dim: breakdown.dimensions.experience_level }
              : null,
          ] as Array<{ label: string; weight: number | null; dim: DimensionScore } | null>
        ).filter((x): x is { label: string; weight: number | null; dim: DimensionScore } => x !== null)
      : [];

  const pendingItems: string[] = breakdown.pending_verification ?? [];
  const scoringPass = breakdown.scoring_pass;

  return (
    <div className="space-y-4">
      {scoringPass && (
        <div className="flex items-center gap-2">
          <span className={cn(
            "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
            scoringPass === "post_voice" ? "bg-emerald-100 text-emerald-700" : "bg-blue-100 text-blue-700"
          )}>
            {scoringPass === "post_voice" ? "Verified (Post Voice Screen)" : "Resume Only"}
          </span>
        </div>
      )}

      {breakdown.summary && (
        <p className="text-sm text-muted-foreground">{breakdown.summary}</p>
      )}

      {criteriaScores.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {criteriaScores.map((c, i) => (
            <DimRow
              key={c.key ?? i}
              label={c.label}
              weight={c.weight ?? null}
              score={c.score}
              rationale={c.rationale}
              evidence={c.evidence}
              data_status={c.data_status}
            />
          ))}
        </div>
      )}

      {fixedDims.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {fixedDims.map(({ label, weight, dim }, i) => (
            <DimRow
              key={i}
              label={label}
              weight={weight}
              score={dim.score}
              rationale={dim.rationale}
              evidence={dim.evidence}
              data_status={dim.data_status}
            />
          ))}
        </div>
      )}

      {pendingItems.length > 0 && (
        <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950/20">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" /> Pending Voice Screen Verification
          </h4>
          <ul className="mt-1 space-y-0.5">
            {pendingItems.map((item: string, i: number) => (
              <li key={i} className="text-sm text-amber-800 dark:text-amber-300">{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Rejection banner                                                  */
/* ------------------------------------------------------------------ */

function RejectionReasonBanner({ audit, voiceEvaluation }: { audit: any[]; voiceEvaluation?: any }) {
  const rejection = [...audit]
    .reverse()
    .find((a) =>
      a.action === "candidate_rejected" ||
      a.action === "fit_auto_rejected" ||
      a.action === "auto_rejected_fit" ||
      a.action === "voice_screen_evaluated"
    );
  if (!rejection) return null;

  const details = rejection.details ?? {};
  const reason = details.reason || details.rejection_reason || details.category || details.summary || null;
  const tier = details.fit_tier || details.tier;
  const score = details.fit_score ?? details.score;

  const voiceRationale = voiceEvaluation?.verdict === "clear_reject" ? voiceEvaluation.verdict_rationale : null;
  const voiceRedFlags = voiceEvaluation?.verdict === "clear_reject" ? (voiceEvaluation.red_flags ?? []) : [];

  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 space-y-2">
      <div className="flex items-center gap-2 text-sm font-semibold text-destructive">
        <AlertTriangle className="h-4 w-4" /> Rejection Reason
      </div>
      {reason && <p className="text-sm">{reason}</p>}
      {!reason && voiceRationale && (
        <div>
          <p className="text-sm">{voiceRationale}</p>
          {voiceRedFlags.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {voiceRedFlags.map((f: string, i: number) => (
                <li key={i} className="flex items-start gap-2 text-sm text-destructive/80">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!reason && !voiceRationale && tier && (
        <p className="text-sm">Auto-rejected: Fit tier &quot;{tier}&quot;{score != null ? ` (score: ${score})` : ""}</p>
      )}
      {!reason && !voiceRationale && !tier && <p className="text-sm text-muted-foreground">No detailed reason recorded.</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Quality badge (for assignment)                                    */
/* ------------------------------------------------------------------ */

function QualityBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    high: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    low: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  };
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase", colors[level] ?? "bg-muted text-muted-foreground")}>
      {level}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Evidence & Decisions (derived from audit + data)                  */
/* ------------------------------------------------------------------ */

interface DerivedEvidence {
  key: string;
  value: string;
  source: string;
  confidence?: number;
  timestamp?: string;
}

interface DerivedDecision {
  type: string;
  outcome: string;
  detail?: string;
  timestamp?: string;
}

function deriveEvidenceAndDecisions(data: any): { evidence: DerivedEvidence[]; decisions: DerivedDecision[] } {
  const evidence: DerivedEvidence[] = [];
  const decisions: DerivedDecision[] = [];
  const audit: any[] = data.audit ?? [];

  if (data.fit_breakdown) {
    const fb = data.fit_breakdown;
    evidence.push({ key: "Fit Score", value: `${fb.overall_score}/100`, source: "Resume Analysis", confidence: (fb.overall_score ?? 0) / 100 });
    if (fb.deterministic_tier) {
      evidence.push({ key: "Fit Tier", value: fb.deterministic_tier.toUpperCase(), source: "Resume Analysis" });
    }
    const criteriaScores = Array.isArray(fb.criteria_scores) ? fb.criteria_scores : [];
    if (criteriaScores.length > 0) {
      for (const c of criteriaScores as any[]) {
        if (c?.score != null) {
          evidence.push({ key: String(c.label || c.key || "").replace(/_/g, " "), value: `${c.score}/100`, source: "Fit Score", confidence: c.score / 100 });
        }
      }
    } else {
      const dims = fb.dimensions;
      if (dims && typeof dims === "object" && !Array.isArray(dims)) {
        for (const [k, v] of Object.entries(dims) as [string, any][]) {
          if (v?.score != null) {
            evidence.push({ key: k.replace(/_/g, " "), value: `${v.score}/100`, source: "Fit Score", confidence: v.score / 100 });
          }
        }
      }
    }
    fb.green_flags?.forEach((f: string) => evidence.push({ key: "Strength", value: f, source: "Fit Score" }));
    fb.red_flags?.forEach((f: string) => evidence.push({ key: "Red Flag", value: f, source: "Fit Score" }));
  }

  if (data.screening_evaluation) {
    const se = data.screening_evaluation;
    if (se.composite_score != null) {
      evidence.push({ key: "Screening Score", value: `${se.composite_score}/100`, source: "Screening", confidence: se.composite_score / 100 });
    }
    se.strengths?.forEach((s: string) => evidence.push({ key: "Screening Strength", value: s, source: "Screening" }));
    se.red_flags?.forEach((r: string) => evidence.push({ key: "Screening Red Flag", value: r, source: "Screening" }));
  }

  if (data.voice_evaluation) {
    const ve = data.voice_evaluation;
    if (ve.overall_score != null) {
      evidence.push({ key: "Voice Screen Score", value: `${ve.overall_score}/100`, source: "Voice Screen", confidence: ve.overall_score / 100 });
    }
    ve.strengths?.forEach((s: string) => evidence.push({ key: "Voice Strength", value: s, source: "Voice Screen" }));
    ve.red_flags?.forEach((r: string) => evidence.push({ key: "Voice Red Flag", value: r, source: "Voice Screen" }));
    const facts = ve.extracted_facts;
    if (facts) {
      if (facts.current_ctc_lpa != null) evidence.push({ key: "Current CTC (Voice)", value: `${facts.current_ctc_lpa} LPA`, source: "Voice Screen" });
      if (facts.expected_ctc_lpa != null) evidence.push({ key: "Expected CTC (Voice)", value: `${facts.expected_ctc_lpa} LPA`, source: "Voice Screen" });
      if (facts.notice_period_days != null) evidence.push({ key: "Notice Period (Voice)", value: `${facts.notice_period_days} days`, source: "Voice Screen" });
      if (facts.current_location) evidence.push({ key: "Location (Voice)", value: facts.current_location, source: "Voice Screen" });
    }
  }

  const profile = data.profile ?? data.candidate ?? {};
  if (profile.current_ctc_lpa != null) evidence.push({ key: "Current CTC", value: `${profile.current_ctc_lpa} LPA`, source: "Resume / Voice" });
  if (profile.expected_ctc_lpa != null) evidence.push({ key: "Expected CTC", value: `${profile.expected_ctc_lpa} LPA`, source: "Resume / Voice" });
  if (profile.notice_period_days != null) evidence.push({ key: "Notice Period", value: `${profile.notice_period_days} days`, source: "Resume / Voice" });
  if (profile.location) evidence.push({ key: "Location", value: profile.location, source: "Resume" });
  if (profile.total_years_experience != null) evidence.push({ key: "Experience", value: `${profile.total_years_experience} years`, source: "Resume" });

  for (const entry of audit) {
    const d = entry.details ?? {};
    switch (entry.action) {
      case "fit_scored":
        decisions.push({
          type: "Fit Score Decision",
          outcome: d.deterministic_tier === "red" ? "reject" : "pass",
          detail: `Score: ${d.overall_score}, Tier: ${d.deterministic_tier}${d.scoring_pass === "post_voice" ? " (updated after voice screen)" : ""}${d.pending_verification?.length ? ` · Pending: ${d.pending_verification.join(", ")}` : ""}`,
          timestamp: entry.created_at,
        });
        break;
      case "auto_shortlisted_voice":
        decisions.push({ type: "Auto-shortlisted for Voice Screen", outcome: "pass", detail: `Fit tier: ${d.fit_tier}`, timestamp: entry.created_at });
        break;
      case "voice_screen_evaluated":
        decisions.push({
          type: "Voice Screen Evaluation",
          outcome: d.verdict === "clear_pass" ? "pass" : d.verdict === "clear_reject" ? "reject" : "review",
          detail: `Score: ${d.overall_score}, Verdict: ${d.verdict}`,
          timestamp: entry.created_at,
        });
        if (d.overall_score != null) {
          evidence.push({ key: "Voice Screen Score", value: `${d.overall_score}/100`, source: "Voice Screen", confidence: d.overall_score / 100, timestamp: entry.created_at });
        }
        break;
      case "voice_screen_promoted_to_assessment":
        decisions.push({ type: "Promoted to Assessment", outcome: "pass", detail: d.note || "HR approved", timestamp: entry.created_at });
        break;
      case "candidate_rejected":
      case "fit_auto_rejected":
        decisions.push({
          type: "Candidate Rejected",
          outcome: "reject",
          detail: d.reason || d.rejection_reason || d.category || `Fit tier: ${d.fit_tier}`,
          timestamp: entry.created_at,
        });
        break;
      case "auto_progress_fired":
        decisions.push({ type: "Auto-Progress", outcome: "executed", detail: d.decision, timestamp: entry.created_at });
        break;
    }
  }

  return { evidence, decisions };
}

/* ------------------------------------------------------------------ */
/*  V2 stage_view helpers                                             */
/* ------------------------------------------------------------------ */

function pipelineHasStageType(stageView: StageViewEntry[] | null | undefined, type: string): boolean {
  if (!stageView || stageView.length === 0) return true;
  return stageView.some((s) => s.stage_type === type && s.is_enabled);
}

function reachedStageType(stageView: StageViewEntry[] | null | undefined, type: string): boolean {
  if (!stageView || stageView.length === 0) return true;
  const current = stageView.find((s) => s.is_current);
  const currentPos = current ? current.position : Number.POSITIVE_INFINITY;
  return stageView.some(
    (s) =>
      s.stage_type === type &&
      s.is_enabled &&
      (s.position <= currentPos ||
        s.verdict !== "pending" ||
        s.processing_status !== "unprocessed"),
  );
}

/* ------------------------------------------------------------------ */
/*  Proceed to Next Round                                             */
/* ------------------------------------------------------------------ */

function ProceedToNextRound({
  applicationId,
  currentStageKey,
  stageView,
  onChanged,
}: {
  applicationId: string;
  currentStageKey: string;
  stageView?: StageViewEntry[] | null;
  onChanged: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [doneLabel, setDoneLabel] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  let current: StageViewEntry | undefined;
  let next: StageViewEntry | undefined;
  if (stageView && stageView.length > 0) {
    const enabled = [...stageView].filter((s) => s.is_enabled).sort((a, b) => a.position - b.position);
    const idx = enabled.findIndex((s) => s.is_current || s.stage_key === currentStageKey);
    if (idx >= 0) {
      current = enabled[idx];
      next = enabled[idx + 1];
    }
  }

  const settled =
    !!current &&
    current.verdict !== "fail" &&
    current.verdict !== "on_going" &&
    (current.verdict === "pass" || current.processing_status === "processed");
  const canProceed = !!next && settled;

  if (!canProceed && !done) return null;

  const handleProceed = async () => {
    if (!next) return;
    setLoading(true);
    setError(null);
    try {
      await api.post(`/dashboard/v1/candidates/${applicationId}/advance`, {
        note: `Proceeded from ${current?.label ?? currentStageKey}`,
      });
      setDoneLabel(next.label);
      setDone(true);
      onChanged();
    } catch (e: any) {
      setError(e.message || "Failed to advance");
    } finally {
      setLoading(false);
    }
  };

  if (done) {
    return (
      <Card className="border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20">
        <CardContent className="flex items-center gap-3 p-4">
          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
          <p className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
            Candidate advanced to {doneLabel}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Proceed to {next!.label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">Advance candidate to the {next!.label} stage.</p>
          </div>
          <Button onClick={handleProceed} disabled={loading} className="shrink-0 gap-1.5">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            Proceed to {next!.label}
          </Button>
        </div>
        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Overview                                                     */
/* ------------------------------------------------------------------ */

function OverviewTab({ data }: { data: any }) {
  const cand = data.candidate ?? {};
  const profile = data.profile ?? {};
  const role = data.role ?? {};
  const breakdown = data.fit_breakdown as FitAssessment | null | undefined;

  const initials = (cand.name ?? "?")
    .split(" ")
    .map((n: string) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  const fitScore = data.fit_score;
  const fitTier = data.fit_tier;
  const scoreColor =
    fitTier === "green" ? "text-emerald-600" :
    fitTier === "red" ? "text-red-500" :
    "text-amber-500";

  const hasProfile = profile && Object.keys(profile).length > 2;

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      {/* Left column: profile card + about */}
      <div className="lg:col-span-2 space-y-5">
        {/* Profile card */}
        <Card>
          <CardContent className="p-5">
            <div className="flex items-start gap-4">
              {/* Avatar */}
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-primary text-2xl font-bold text-primary-foreground">
                {initials}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="text-lg font-bold leading-tight">{cand.name ?? "Unknown"}</h2>
                {role.title && <p className="text-sm text-muted-foreground mt-0.5">{role.title}</p>}
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                  {cand.email && (
                    <a href={`mailto:${cand.email}`} className="inline-flex items-center gap-1.5 hover:text-primary">
                      <Mail className="h-3.5 w-3.5" /> {cand.email}
                    </a>
                  )}
                  {cand.phone && (
                    <span className="inline-flex items-center gap-1.5">
                      <Phone className="h-3.5 w-3.5" /> {cand.phone}
                    </span>
                  )}
                  {profile.location && (
                    <span className="inline-flex items-center gap-1.5">
                      <MapPin className="h-3.5 w-3.5" /> {profile.location}
                    </span>
                  )}
                </div>
                {/* Links */}
                <div className="mt-3 flex flex-wrap gap-3">
                  {profile.linkedin_url && (
                    <a
                      href={profile.linkedin_url.startsWith("http") ? profile.linkedin_url : `https://${profile.linkedin_url}`}
                      target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted transition"
                    >
                      <ExternalLink className="h-3 w-3" /> LinkedIn
                    </a>
                  )}
                  {profile.github_url && (
                    <a
                      href={profile.github_url.startsWith("http") ? profile.github_url : `https://${profile.github_url}`}
                      target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted transition"
                    >
                      <Github className="h-3 w-3" /> GitHub
                    </a>
                  )}
                  {profile.portfolio_url && (
                    <a
                      href={profile.portfolio_url.startsWith("http") ? profile.portfolio_url : `https://${profile.portfolio_url}`}
                      target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted transition"
                    >
                      <Globe className="h-3 w-3" /> Portfolio
                    </a>
                  )}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* About / Profile snapshot */}
        {hasProfile && (
          <Card>
            <CardContent className="p-5">
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">About</h3>
              <ProfileSnapshot profile={profile} />
            </CardContent>
          </Card>
        )}

        {/* Original application email (always visible if present) */}
        {data.application_mail && (
          <Card>
            <CardContent className="p-5">
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                <Mail className="mr-1 inline h-3.5 w-3.5" /> Original Application
              </h3>
              <div className="space-y-2">
                {data.application_mail.subject && (
                  <p className="text-sm font-medium">{data.application_mail.subject}</p>
                )}
                {data.application_mail.body_preview && (
                  <div className="max-h-48 overflow-y-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                    {data.application_mail.body_preview}
                  </div>
                )}
                {(data.application_mail.forwarder_name || data.application_mail.source) && (
                  <p className="text-xs text-muted-foreground">
                    {data.application_mail.forwarder_name && <>Forwarded by {data.application_mail.forwarder_name}{data.application_mail.forwarder_email && ` (${data.application_mail.forwarder_email})`}</>}
                    {data.application_mail.source && <> · Source: {data.application_mail.source}</>}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Right column: fit score + flags + resume */}
      <div className="space-y-4">
        {/* Fit score card */}
        {fitScore != null && pipelineHasStageType(data.stage_view as StageViewEntry[] | null | undefined, "fit") && (
          <Card>
            <CardContent className="p-5 text-center">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Fit Score</p>
              <div className={cn("text-5xl font-bold tabular-nums", scoreColor)}>{fitScore}</div>
              <div className="mt-2">
                <TierBadge tier={fitTier} />
              </div>
              {breakdown?.summary && (
                <p className="mt-3 text-xs text-muted-foreground leading-relaxed">{breakdown.summary}</p>
              )}
            </CardContent>
          </Card>
        )}

        {/* Screening score */}
        {data.screening_evaluation?.composite_score != null && (
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-medium text-muted-foreground">Screening Score</p>
              <div className="mt-1 text-2xl font-bold">{data.screening_evaluation.composite_score}</div>
            </CardContent>
          </Card>
        )}

        {/* Voice score */}
        {data.voice_evaluation?.overall_score != null && pipelineHasStageType(data.stage_view as StageViewEntry[] | null | undefined, "voice_screen") && (
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-medium text-muted-foreground">Voice Screen</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-2xl font-bold">{data.voice_evaluation.overall_score}</span>
                <span className={cn(
                  "rounded-full px-2 py-0.5 text-xs font-semibold",
                  data.voice_evaluation.verdict === "clear_pass"
                    ? "bg-emerald-100 text-emerald-700"
                    : data.voice_evaluation.verdict === "needs_hr_review"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-red-100 text-red-700"
                )}>
                  {data.voice_evaluation.verdict === "clear_pass" ? "Pass" : data.voice_evaluation.verdict === "needs_hr_review" ? "HR Review" : "Rejected"}
                </span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Green flags */}
        {(breakdown?.green_flags?.length ?? 0) > 0 && (
          <Card>
            <CardContent className="p-4">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600 mb-3">
                <ThumbsUp className="h-3.5 w-3.5" /> Strengths
              </h4>
              <ul className="space-y-1.5">
                {breakdown!.green_flags!.map((f: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {f}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {/* Red flags */}
        {(breakdown?.red_flags?.length ?? 0) > 0 && (
          <Card>
            <CardContent className="p-4">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive mb-3">
                <ThumbsDown className="h-3.5 w-3.5" /> Red Flags
              </h4>
              <ul className="space-y-1.5">
                {breakdown!.red_flags!.map((f: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {/* Resume download */}
        {data.resume_download_url && (
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-medium text-muted-foreground mb-2">Resume</p>
              <a
                href={data.resume_download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted transition"
              >
                <Download className="h-4 w-4" />
                {data.resume_filename ?? "Download Resume"}
              </a>
            </CardContent>
          </Card>
        )}

        {/* Knock-outs */}
        {(breakdown?.knock_outs?.length ?? 0) > 0 && (
          <Card className="border-destructive/30">
            <CardContent className="p-4">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive mb-3">
                <AlertTriangle className="h-3.5 w-3.5" /> Knock-outs
              </h4>
              <ul className="space-y-1">
                {breakdown!.knock_outs!.map((f: string, i: number) => (
                  <li key={i} className="text-sm text-destructive">{f}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Screening                                                    */
/* ------------------------------------------------------------------ */

function ScreeningTab({ data }: { data: any }) {
  const evaluation = data.screening_evaluation;

  if (!evaluation) {
    return (
      <div className="py-16 text-center text-muted-foreground">
        <FileSearch className="mx-auto mb-3 h-8 w-8 opacity-40" />
        <p className="text-sm">No screening data available yet.</p>
      </div>
    );
  }

  const answers = evaluation.answers || evaluation.question_scores || [];
  const fitScore = data.fit_score;
  const fitTier = data.fit_tier;
  const fitScoreColor =
    fitTier === "green" ? "text-emerald-600" :
    fitTier === "red" ? "text-red-500" :
    "text-amber-500";
  const hasFitData = data.fit_breakdown != null || fitScore != null;

  return (
    <div className="space-y-5">
      {/* Fit Assessment — shown when fit score or breakdown is available */}
      {hasFitData && (
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Fit Assessment</h3>
            <div className="mb-4 flex items-center gap-3">
              {fitScore != null && (
                <span className={cn("text-4xl font-bold tabular-nums", fitScoreColor)}>{fitScore}</span>
              )}
              <TierBadge tier={fitTier} />
            </div>
            <FitBreakdownContent breakdown={data.fit_breakdown} />
          </CardContent>
        </Card>
      )}

      {/* Score summary */}
      <div className="flex items-center gap-4">
        {evaluation.composite_score != null && (
          <div className="rounded-xl border bg-card p-4 text-center min-w-[100px]">
            <p className="text-xs text-muted-foreground mb-1">Score</p>
            <p className="text-3xl font-bold tabular-nums">{evaluation.composite_score}</p>
          </div>
        )}
        {evaluation.summary && (
          <p className="flex-1 text-sm text-muted-foreground leading-relaxed">{evaluation.summary}</p>
        )}
      </div>

      {/* Strengths & red flags side by side */}
      {((evaluation.strengths?.length ?? 0) > 0 || (evaluation.red_flags?.length ?? 0) > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {evaluation.strengths?.length > 0 && (
            <Card>
              <CardContent className="p-4">
                <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600 mb-3">
                  <ThumbsUp className="h-3.5 w-3.5" /> Strengths
                </h4>
                <ul className="space-y-1.5">
                  {evaluation.strengths.map((s: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {s}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
          {evaluation.red_flags?.length > 0 && (
            <Card>
              <CardContent className="p-4">
                <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive mb-3">
                  <ThumbsDown className="h-3.5 w-3.5" /> Concerns
                </h4>
                <ul className="space-y-1.5">
                  {evaluation.red_flags.map((f: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Q&A format */}
      {Array.isArray(answers) && answers.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Responses</h3>
          <div className="space-y-3">
            {answers.map((a: any, i: number) => (
              <Card key={i}>
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium">{a.question || `Question ${i + 1}`}</p>
                    {a.score != null && (
                      <span className={cn(
                        "shrink-0 font-mono text-xs font-semibold px-2 py-0.5 rounded-full",
                        a.score >= 7 ? "bg-emerald-100 text-emerald-700" : a.score >= 5 ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700"
                      )}>
                        {a.score}/10
                      </span>
                    )}
                  </div>
                  {a.answer && (
                    <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{a.answer}</p>
                  )}
                  {a.rationale && <p className="mt-1.5 text-xs text-muted-foreground/70 italic">{a.rationale}</p>}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Assignment                                                   */
/* ------------------------------------------------------------------ */

function AssignmentTab({ data }: { data: any }) {
  const submission = data.assignment_submission;
  const role = data.role ?? {};
  const breakdown = data.fit_breakdown as FitAssessment | null | undefined;

  const hasAssignment = pipelineHasStageType(data.stage_view as StageViewEntry[] | null | undefined, "assignment");
  const reached = reachedStageType(data.stage_view as StageViewEntry[] | null | undefined, "assignment");

  if (!hasAssignment || (!reached && !submission)) {
    return (
      <div className="py-16 text-center text-muted-foreground">
        <ClipboardCheck className="mx-auto mb-3 h-8 w-8 opacity-40" />
        <p className="text-sm">Assignment stage not reached yet.</p>
      </div>
    );
  }

  const parseResult = submission?.parse_result;
  const completeness = parseResult?.completeness;
  const quality = parseResult?.quality_signals;
  const submittedAt = submission?.submitted_at;

  let deadlineLabel: string | null = null;
  let deadlineOverdue = false;
  if (role?.assignment_deadline_days) {
    if (submittedAt) {
      deadlineLabel = new Date(submittedAt).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata", month: "short", day: "numeric", year: "numeric",
      });
    } else {
      const sentAt = submission?.sent_at;
      const from = sentAt ? new Date(sentAt) : new Date();
      const due = new Date(from);
      due.setDate(due.getDate() + role.assignment_deadline_days);
      deadlineLabel = `Due ${due.toLocaleDateString("en-IN", { month: "short", day: "numeric", year: "numeric" })}`;
      deadlineOverdue = due < new Date();
    }
  }

  return (
    <div className="space-y-5">
      {/* Deadline banner (pending only) */}
      {role?.assignment_deadline_days && !submission && (
        <div className={cn(
          "flex items-center gap-2 rounded-lg border p-3 text-sm",
          deadlineOverdue
            ? "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/20 dark:text-red-400"
            : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-400"
        )}>
          <Clock className="h-4 w-4 shrink-0" />
          <span>{deadlineLabel}</span>
        </div>
      )}

      {/* Submission metadata */}
      {submission && (
        <Card>
          <CardContent className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Submission Details</h3>
              <div className="flex items-center gap-3">
                {deadlineLabel && submittedAt && (
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <Calendar className="h-3 w-3" /> {deadlineLabel}
                  </span>
                )}
                {submittedAt && (
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {new Date(submittedAt).toLocaleString("en-IN", {
                      timeZone: "Asia/Kolkata", month: "short", day: "numeric",
                      hour: "2-digit", minute: "2-digit", year: "numeric",
                    })}
                  </span>
                )}
              </div>
            </div>

            {submission.project_choice && (
              <div>
                <span className="text-xs text-muted-foreground">Project Choice:</span>
                <span className="ml-2 text-sm font-medium">{submission.project_choice}</span>
              </div>
            )}

            {submission.links?.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground block mb-1.5">Links:</span>
                <div className="flex flex-wrap gap-2">
                  {submission.links.map((link: string, i: number) => {
                    const isGithub = link.toLowerCase().includes("github");
                    const isLoom = link.toLowerCase().includes("loom");
                    const Icon = isGithub ? Github : isLoom ? Video : ExternalLink;
                    const cleanUrl = link.includes(": ") ? link.split(": ").slice(1).join(": ") : link;
                    return (
                      <a
                        key={i}
                        href={cleanUrl.startsWith("http") ? cleanUrl : `https://${cleanUrl}`}
                        target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-lg border bg-background px-3 py-1.5 text-xs font-medium text-primary hover:bg-accent transition"
                      >
                        <Icon className="h-3.5 w-3.5" />
                        {isGithub ? "GitHub Repo" : isLoom ? "Loom Video" : cleanUrl.length > 50 ? cleanUrl.slice(0, 50) + "..." : cleanUrl}
                      </a>
                    );
                  })}
                </div>
              </div>
            )}

            {submission.deployed_url && (
              <div className="flex items-center gap-2">
                <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">Deployed:</span>
                <a href={submission.deployed_url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary hover:underline">
                  {submission.deployed_url}
                </a>
              </div>
            )}

            {submission.files?.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground block mb-1.5">Files:</span>
                <div className="flex flex-wrap gap-2">
                  {submission.files.map((f: any, i: number) => (
                    <span key={i} className="inline-flex items-center gap-1.5 rounded-lg border bg-background px-3 py-1.5 text-xs">
                      <FileCode className="h-3.5 w-3.5 text-muted-foreground" />
                      {f.filename}
                      {f.size_bytes && <span className="text-muted-foreground">({(f.size_bytes / 1024).toFixed(1)} KB)</span>}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {submission.notes && (
              <div>
                <span className="text-xs text-muted-foreground block mb-1">Candidate Notes:</span>
                <p className="whitespace-pre-wrap text-sm rounded-lg border bg-background p-3">{submission.notes}</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* AI Analysis */}
      {parseResult && (
        <div className="space-y-4">
          {parseResult.summary && (
            <div className="rounded-lg border-l-4 border-primary bg-primary/5 p-4">
              <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-primary">AI Analysis Summary</h4>
              <p className="text-sm leading-relaxed">{parseResult.summary}</p>
            </div>
          )}

          {quality && (
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Quality Signals</h4>
              <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
                {(["depth", "originality", "clarity", "technical_rigor"] as const).map((dim) => (
                  quality[dim] ? (
                    <div key={dim} className="rounded-lg border p-3 text-center">
                      <div className="text-xs text-muted-foreground capitalize">{dim.replace(/_/g, " ")}</div>
                      <div className="mt-1"><QualityBadge level={quality[dim]} /></div>
                    </div>
                  ) : null
                ))}
              </div>
            </div>
          )}

          {completeness && (
            <Card>
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Completeness</h4>
                  <span className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
                    completeness.followed_instructions ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
                  )}>
                    {completeness.followed_instructions ? "Instructions Followed" : "Instructions Not Followed"}
                  </span>
                </div>
                {completeness.missing_items?.length > 0 && (
                  <div className="mt-2">
                    <span className="text-xs text-destructive font-medium">Missing:</span>
                    <ul className="mt-1 space-y-0.5">
                      {completeness.missing_items.map((item: string, i: number) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-destructive/80">
                          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {item}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            {parseResult.highlights?.length > 0 && (
              <Card>
                <CardContent className="p-4">
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600 mb-3">
                    <ThumbsUp className="h-3.5 w-3.5" /> Highlights
                  </h4>
                  <ul className="space-y-1">
                    {parseResult.highlights.map((h: string, i: number) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {h}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
            {parseResult.concerns?.length > 0 && (
              <Card>
                <CardContent className="p-4">
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive mb-3">
                    <ThumbsDown className="h-3.5 w-3.5" /> Concerns
                  </h4>
                  <ul className="space-y-1">
                    {parseResult.concerns.map((c: string, i: number) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {c}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </div>

          {parseResult.evidence_quotes?.length > 0 && (
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Evidence Quotes</h4>
              <div className="space-y-2">
                {parseResult.evidence_quotes.map((q: string, i: number) => (
                  <blockquote key={i} className="border-l-2 border-muted-foreground/30 pl-3 text-sm italic text-muted-foreground">
                    &ldquo;{q}&rdquo;
                  </blockquote>
                ))}
              </div>
            </div>
          )}

          {parseResult.suggested_hr_focus?.length > 0 && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-800 dark:bg-blue-950/20">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-blue-700 dark:text-blue-400">
                <Brain className="h-3.5 w-3.5" /> Suggested HR Focus
              </h4>
              <ul className="mt-1.5 space-y-0.5">
                {parseResult.suggested_hr_focus.map((f: string, i: number) => (
                  <li key={i} className="text-sm text-blue-800 dark:text-blue-300">{f}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Fit score breakdown for assignment tab */}
      {breakdown && !pipelineHasStageType(data.stage_view as StageViewEntry[] | null | undefined, "fit") && (
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Fit Score Breakdown</h3>
            <FitBreakdownContent breakdown={breakdown} />
          </CardContent>
        </Card>
      )}

      {/* Brief & Instructions */}
      {(role?.assignment_brief || role?.assignment_instructions) && (
        <Card>
          <CardContent className="p-5 space-y-4">
            {role.assignment_brief && (
              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Assignment Brief</h4>
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{role.assignment_brief}</p>
              </div>
            )}
            {role.assignment_instructions && (
              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Instructions</h4>
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{role.assignment_instructions}</p>
              </div>
            )}
            {role.has_problem_doc && (
              <div>
                <a
                  href={`/dashboard/roles/${role.id}/problem-doc/download`}
                  className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
                  target="_blank" rel="noopener noreferrer"
                >
                  <Download className="h-4 w-4" />
                  {role.assignment_problem_doc_filename || "Download Problem Document"}
                </a>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Voice                                                        */
/* ------------------------------------------------------------------ */

function VoiceTab({ data }: { data: any }) {
  const voice = data.voice_evaluation;
  const hasVoiceStage = pipelineHasStageType(data.stage_view as StageViewEntry[] | null | undefined, "voice_screen");

  const [showTranscript, setShowTranscript] = useState(false);
  const [showRecording, setShowRecording] = useState(false);

  if (!hasVoiceStage || !voice) {
    return (
      <div className="py-16 text-center text-muted-foreground">
        <Phone className="mx-auto mb-3 h-8 w-8 opacity-40" />
        <p className="text-sm">{!hasVoiceStage ? "Voice screen stage not configured for this role." : "No voice screen data available yet."}</p>
      </div>
    );
  }

  const verdict = voice.verdict as string | undefined;
  const isPass = verdict === "clear_pass";
  const isHrReview = verdict === "needs_hr_review";
  const transcript: any[] = voice.transcript ?? [];
  const questions: any[] = voice.questions ?? [];
  const perQ: any[] = voice.per_question ?? [];
  const durationMin = voice.duration_sec ? Math.round(voice.duration_sec / 60) : null;

  const qMap = new Map<string, any>();
  if (Array.isArray(questions)) {
    for (const q of questions) {
      if (q.id) qMap.set(q.id, q);
    }
  }

  return (
    <div className="space-y-5">
      {/* Verdict banner */}
      {voice.verdict_rationale && (
        <div className={cn(
          "rounded-xl border p-4",
          isPass
            ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20"
            : isHrReview
            ? "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20"
            : "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20"
        )}>
          <div className="flex items-center gap-2 text-sm font-semibold mb-2">
            {isPass ? <ThumbsUp className="h-4 w-4 text-emerald-600" /> : isHrReview ? <AlertTriangle className="h-4 w-4 text-amber-600" /> : <ThumbsDown className="h-4 w-4 text-red-600" />}
            {isPass ? "Selected" : isHrReview ? "Escalated to HR Review" : "Rejected"}
            {voice.overall_score != null && <span className="ml-auto font-mono text-lg font-bold">{voice.overall_score}<span className="text-xs font-normal">/100</span></span>}
          </div>
          <p className="text-sm">{voice.verdict_rationale}</p>
        </div>
      )}

      {/* Meta row */}
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {durationMin != null && (
          <span className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1">
            <Clock className="h-3 w-3" /> {durationMin} min call
          </span>
        )}
        {voice.started_at && (
          <span className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1">
            {new Date(voice.started_at).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata", month: "short", day: "numeric",
              hour: "2-digit", minute: "2-digit",
            })}
          </span>
        )}
        {voice.recording_url && (
          <button
            type="button"
            onClick={() => setShowRecording(!showRecording)}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 font-medium hover:bg-muted transition"
          >
            {showRecording ? "Hide" : "Play"} Recording
          </button>
        )}
      </div>

      {showRecording && voice.recording_url && (
        <div className="rounded-lg border bg-muted/20 p-2">
          <audio controls preload="metadata" className="w-full h-9" src={voice.recording_url}>
            Your browser does not support the audio element.
          </audio>
        </div>
      )}

      {/* Strengths + Red flags */}
      <div className="grid gap-4 sm:grid-cols-2">
        {voice.strengths?.length > 0 && (
          <Card>
            <CardContent className="p-4">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600 mb-3">
                <ThumbsUp className="h-3.5 w-3.5" /> Strengths
              </h4>
              <ul className="space-y-1.5">
                {voice.strengths.map((s: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {s}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
        {voice.red_flags?.length > 0 && (
          <Card>
            <CardContent className="p-4">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive mb-3">
                <ThumbsDown className="h-3.5 w-3.5" /> Red Flags
              </h4>
              <ul className="space-y-1.5">
                {voice.red_flags.map((f: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Per-question scores */}
      {perQ.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Question Scores</h3>
          <div className="space-y-2">
            {perQ.map((pq: any, i: number) => {
              const q = qMap.get(pq.question_id);
              return (
                <Card key={i}>
                  <CardContent className="p-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{q?.question || pq.question_id || `Q${i + 1}`}</span>
                      {pq.score != null && (
                        <span className={cn(
                          "font-mono text-sm font-bold px-2 py-0.5 rounded-full",
                          pq.score >= 60 ? "bg-emerald-100 text-emerald-600" : "bg-red-100 text-red-600"
                        )}>{pq.score}</span>
                      )}
                    </div>
                    {pq.relevance && (
                      <span className={cn(
                        "mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                        pq.relevance === "high" ? "bg-emerald-100 text-emerald-700" :
                        pq.relevance === "medium" ? "bg-amber-100 text-amber-700" :
                        "bg-muted text-muted-foreground"
                      )}>{pq.relevance}</span>
                    )}
                    {pq.notes && <p className="mt-1 text-xs text-muted-foreground">{pq.notes}</p>}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Transcript */}
      {transcript.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowTranscript(!showTranscript)}
            className="flex items-center gap-1.5 text-sm font-medium text-primary hover:underline mb-3"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            {showTranscript ? "Hide" : "Show"} Conversation Transcript ({transcript.length} exchanges)
          </button>
          {showTranscript && (
            <div className="space-y-4 rounded-xl border bg-muted/30 p-5">
              {transcript.map((item: any, i: number) => {
                const questionText = item.question || item.question_text || qMap.get(item.question_id)?.question || `Question ${i + 1}`;
                const answerText = item.answer_transcript || item.answer || "";
                return (
                  <div key={i} className="space-y-2">
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-primary">Agent</span>
                      <p className="text-sm">{questionText}</p>
                    </div>
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 shrink-0 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">Candidate</span>
                      <p className="text-sm text-muted-foreground">
                        {answerText || <span className="italic">No response</span>}
                      </p>
                    </div>
                    {i < transcript.length - 1 && <hr className="border-border/50" />}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Interviews                                                   */
/* ------------------------------------------------------------------ */

function InterviewsTab({
  data,
  applicationId,
  mutate,
}: {
  data: any;
  applicationId: string;
  mutate: () => void;
}) {
  const reports = data.meeting_reports as Record<string, any> | null;
  const stageView = data.stage_view as StageViewEntry[] | null | undefined;

  const fallbackLabels: Record<string, string> = {
    technical: "Technical Round",
    ceo: "Management Round",
    hr: "HR Discussion",
  };
  const labelFor = (round: string) =>
    stageView?.find((s) => s.stage_key === round)?.label ??
    fallbackLabels[round] ??
    `${round.replace(/_/g, " ")} round`;

  // Sort round keys by pipeline position if stage_view is available; fall back to natural order
  const roundKeys: string[] = reports
    ? stageView && stageView.length > 0
      ? Object.keys(reports).sort((a, b) => {
          const posA = stageView.find((s) => s.stage_key === a)?.position ?? 9999;
          const posB = stageView.find((s) => s.stage_key === b)?.position ?? 9999;
          return posA - posB;
        })
      : Object.keys(reports)
    : [];

  const defaultRound = roundKeys[roundKeys.length - 1] ?? null;
  const [selectedRound, setSelectedRound] = useState<string | null>(defaultRound);

  const hasReports = roundKeys.length > 0;
  // Active round — fall back to first if selectedRound was removed
  const activeRound = selectedRound && roundKeys.includes(selectedRound) ? selectedRound : defaultRound;
  const activeReport = activeRound && reports ? reports[activeRound] : null;

  const verdictPillClass = (verdict: string | undefined) => {
    if (!verdict) return "bg-muted text-muted-foreground";
    if (verdict.includes("pass")) return "bg-emerald-100 text-emerald-700";
    if (verdict.includes("fail") || verdict.includes("reject")) return "bg-red-100 text-red-700";
    return "bg-amber-100 text-amber-700";
  };

  return (
    <div className="space-y-5">
      {/* Proceed to next round (relevant here too) */}
      <ProceedToNextRound
        applicationId={applicationId}
        currentStageKey={data.current_stage_key || data.current_stage}
        stageView={stageView}
        onChanged={mutate}
      />

      {/* Meeting reports — round selector + detail */}
      {hasReports ? (
        <div className="space-y-4">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Interview Reports</h3>

          {/* Round selector */}
          <div className="flex flex-wrap gap-2">
            {roundKeys.map((round) => {
              const report = reports![round];
              const isActive = round === activeRound;
              return (
                <button
                  key={round}
                  type="button"
                  onClick={() => setSelectedRound(round)}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm font-medium transition",
                    isActive
                      ? "bg-primary text-white shadow-sm"
                      : "border border-border bg-background text-foreground hover:bg-muted"
                  )}
                >
                  {labelFor(round)}
                  {report?.verdict && (
                    <span className={cn(
                      "rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
                      isActive
                        ? report.verdict.includes("pass")
                          ? "bg-white/20 text-white"
                          : report.verdict.includes("fail") || report.verdict.includes("reject")
                          ? "bg-white/20 text-white"
                          : "bg-white/20 text-white"
                        : verdictPillClass(report.verdict)
                    )}>
                      {report.verdict.replace(/_/g, " ")}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Selected round detail */}
          {activeReport && activeRound && (
            <Card>
              <CardContent className="p-5">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-sm font-semibold">{labelFor(activeRound)}</h4>
                  {activeReport.verdict && (
                    <span className={cn(
                      "rounded-full px-2.5 py-0.5 text-xs font-medium",
                      verdictPillClass(activeReport.verdict)
                    )}>{activeReport.verdict.replace(/_/g, " ")}</span>
                  )}
                </div>

                {/* Scores row */}
                {(activeReport.overall_score != null || activeReport.technical_score != null || activeReport.communication_score != null) && (
                  <div className="mb-3 flex flex-wrap gap-3">
                    {activeReport.overall_score != null && (
                      <div className="text-center rounded-lg border px-3 py-2 min-w-[60px]">
                        <div className="text-xs text-muted-foreground">Overall</div>
                        <div className="font-bold tabular-nums">{activeReport.overall_score}</div>
                      </div>
                    )}
                    {activeReport.technical_score != null && (
                      <div className="text-center rounded-lg border px-3 py-2 min-w-[60px]">
                        <div className="text-xs text-muted-foreground">Technical</div>
                        <div className="font-bold tabular-nums">{activeReport.technical_score}</div>
                      </div>
                    )}
                    {activeReport.communication_score != null && (
                      <div className="text-center rounded-lg border px-3 py-2 min-w-[60px]">
                        <div className="text-xs text-muted-foreground">Comms</div>
                        <div className="font-bold tabular-nums">{activeReport.communication_score}</div>
                      </div>
                    )}
                    {activeReport.confidence_score != null && (
                      <div className="text-center rounded-lg border px-3 py-2 min-w-[60px]">
                        <div className="text-xs text-muted-foreground">Confidence</div>
                        <div className="font-bold tabular-nums">{activeReport.confidence_score}</div>
                      </div>
                    )}
                  </div>
                )}

                {activeReport.summary && <p className="text-sm text-muted-foreground mb-3 leading-relaxed">{activeReport.summary}</p>}

                <div className="grid gap-3 sm:grid-cols-2">
                  {activeReport.strengths?.length > 0 && (
                    <div>
                      <span className="text-xs font-semibold text-emerald-600 block mb-1.5">Strengths</span>
                      <ul className="space-y-1">
                        {activeReport.strengths.map((s: string, i: number) => (
                          <li key={i} className="flex items-start gap-2 text-sm">
                            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {s}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {activeReport.red_flags?.length > 0 && (
                    <div>
                      <span className="text-xs font-semibold text-destructive block mb-1.5">Concerns</span>
                      <ul className="space-y-1">
                        {activeReport.red_flags.map((s: string, i: number) => (
                          <li key={i} className="flex items-start gap-2 text-sm">
                            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {s}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        <div className="py-16 text-center text-muted-foreground">
          <User className="mx-auto mb-3 h-8 w-8 opacity-40" />
          <p className="text-sm">No interview reports yet.</p>
        </div>
      )}

      {/* Admin review panel */}
      <AdminReviewPanel
        applicationId={applicationId}
        currentStage={data.current_stage}
        stageStatus={data.stage_status}
        stageView={stageView}
        meetingReports={data.meeting_reports}
        adminReview={data.admin_review}
        onChanged={mutate}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Timeline                                                     */
/* ------------------------------------------------------------------ */

function TimelineTab({ data }: { data: any }) {
  const { evidence, decisions } = deriveEvidenceAndDecisions(data);
  const [evidenceTab, setEvidenceTab] = useState<"evidence" | "decisions">("evidence");

  return (
    <div className="space-y-5">
      {/* Journey report (CEO brief) */}
      {data.journey_report && (
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              <FileSearch className="h-3.5 w-3.5" /> CEO Brief
            </h3>
            <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap">
              {data.journey_report}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Evidence & Decisions card */}
      <Card>
        <div className="flex border-b border-border">
          <button
            onClick={() => setEvidenceTab("evidence")}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition",
              evidenceTab === "evidence" ? "border-b-2 border-primary text-primary" : "text-muted-foreground hover:text-foreground"
            )}
          >
            <FileSearch className="h-3.5 w-3.5" /> Evidence ({evidence.length})
          </button>
          <button
            onClick={() => setEvidenceTab("decisions")}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition",
              evidenceTab === "decisions" ? "border-b-2 border-primary text-primary" : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Scale className="h-3.5 w-3.5" /> Decisions ({decisions.length})
          </button>
        </div>

        <CardContent className="max-h-[50vh] overflow-y-auto p-0">
          {evidenceTab === "evidence" && (
            evidence.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <Brain className="h-7 w-7 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">No evidence collected yet</p>
              </div>
            ) : (
              <div className="divide-y">
                {evidence.map((e, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <span className="text-sm font-medium">{e.key}</span>
                      <span className="ml-2 text-sm text-muted-foreground">{e.value}</span>
                    </div>
                    <span className="shrink-0 rounded bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{e.source}</span>
                    {e.confidence != null && (
                      <div className="w-14 shrink-0">
                        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                          <div
                            className={cn("h-full rounded-full", e.confidence >= 0.6 ? "bg-emerald-500" : "bg-red-500")}
                            style={{ width: `${Math.round(e.confidence * 100)}%` }}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          )}

          {evidenceTab === "decisions" && (
            decisions.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <Scale className="h-7 w-7 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">No decisions recorded yet</p>
              </div>
            ) : (
              <div className="divide-y">
                {decisions.map((d, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                    <span className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
                      d.outcome === "pass" || d.outcome === "executed" ? "bg-emerald-100 text-emerald-700" :
                      d.outcome === "reject" ? "bg-red-100 text-red-700" :
                      "bg-blue-100 text-blue-700"
                    )}>{d.outcome}</span>
                    <div className="min-w-0 flex-1">
                      <span className="text-sm font-medium">{d.type}</span>
                      {d.detail && <span className="ml-2 text-xs text-muted-foreground">{d.detail}</span>}
                    </div>
                    {d.timestamp && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {new Date(d.timestamp).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )
          )}
        </CardContent>
      </Card>

      {/* Full activity timeline */}
      <Card>
        <CardContent className="p-5">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Activity Timeline</h3>
          <ActivityTimeline entries={data.audit ?? []} />
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                         */
/* ------------------------------------------------------------------ */

export default function CandidateDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<TabName>("Overview");

  const { data, error, isLoading, mutate } = useSWR<any>(
    id ? `/dashboard/v1/candidates/${id}` : null,
    swrFetcher,
  );

  if (isLoading) {
    return (
      <>
        <Topbar title="Candidate" />
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <SkeletonLines lines={12} />
        </div>
      </>
    );
  }

  if (error || !data) {
    return (
      <>
        <Topbar title="Candidate" />
        <div className="mx-auto max-w-5xl p-6">
          <p className="text-destructive">{error?.message ?? "Candidate not found."}</p>
          <Button variant="ghost" className="mt-4" onClick={() => router.back()}>
            <ArrowLeft className="mr-1.5 h-4 w-4" /> Back
          </Button>
        </div>
      </>
    );
  }

  const cand = data.candidate ?? {};
  const profile = data.profile ?? {};
  const role = data.role ?? {};
  const isRejected = data.current_stage === "rejected";
  const isInvalidIntake = !!data.intake_error;

  return (
    <div className="flex h-full flex-col">
      <Topbar title={cand.name ?? "Candidate"} />

      <div className="scrollbar-slim min-h-0 flex-1 overflow-y-auto">
        {/* Sticky header */}
        <div className="sticky top-0 z-20 bg-background/95 backdrop-blur-sm border-b border-border px-6 py-3">
          <div className="mx-auto max-w-5xl flex items-center gap-3">
            <button
              type="button"
              onClick={() => router.back()}
              className="flex items-center gap-1 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition shrink-0"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="flex-1 min-w-0">
              <h1 className="text-base font-bold leading-tight truncate">{cand.name}</h1>
              <p className="text-xs text-muted-foreground truncate">
                {cand.email}{role.title ? ` · ${role.title}` : ""}
              </p>
            </div>
            <StatusTag stage={data.current_stage as Stage} />
            {data.fit_score != null && <TierBadge tier={data.fit_tier} score={data.fit_score} />}
            {data.resume_download_url && (
              <a
                href={data.resume_download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted transition"
              >
                <Download className="h-3.5 w-3.5" /> Resume
              </a>
            )}
          </div>
        </div>

        <div className="mx-auto max-w-5xl px-6">
          {/* Pipeline stepper — always visible */}
          {!isInvalidIntake && data.stage_view?.length > 0 && (
            <div className="py-4 border-b border-border/50">
              <PipelineStepper stages={data.stage_view as StageViewEntry[]} />
            </div>
          )}

          {/* Invalid intake banner */}
          {isInvalidIntake && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
              <span className="font-semibold">Invalid application —</span>{" "}
              {data.application_mail?.subject
                ? <>email subject <span className="font-mono">&ldquo;{data.application_mail.subject}&rdquo;</span> did not match any open role.</>
                : "no matching role found for this application."}
              {" "}No pipeline evaluation was run.
            </div>
          )}

          {/* Rejection banner */}
          {isRejected && (
            <div className="mt-4">
              <RejectionReasonBanner audit={data.audit ?? []} voiceEvaluation={data.voice_evaluation} />
            </div>
          )}

          {/* Tab navigation */}
          <div className="mt-4 flex gap-0 border-b border-border overflow-x-auto">
            {TABS.map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={cn(
                  "whitespace-nowrap px-4 py-2.5 text-sm font-medium transition-colors",
                  activeTab === tab
                    ? "text-primary border-b-2 border-primary -mb-px"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="py-5">
            {activeTab === "Overview" && <OverviewTab data={data} />}
            {activeTab === "Screening" && <ScreeningTab data={data} />}
            {activeTab === "Assignment" && <AssignmentTab data={data} />}
            {activeTab === "Voice" && <VoiceTab data={data} />}
            {activeTab === "Interviews" && (
              <InterviewsTab data={data} applicationId={id} mutate={() => mutate()} />
            )}
            {activeTab === "Timeline" && <TimelineTab data={data} />}
          </div>
        </div>
      </div>
    </div>
  );
}
