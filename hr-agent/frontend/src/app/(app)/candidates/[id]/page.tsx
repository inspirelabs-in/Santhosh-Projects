"use client";

import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ArrowLeft,
  Download,
  ThumbsUp,
  ThumbsDown,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
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
  Send,
} from "lucide-react";
import { useState } from "react";

import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusTag, type Stage } from "@/components/status-tag";
import { SkeletonLines } from "@/components/skeleton";
import { swrFetcher, api } from "@/lib/api";
import { ActivityTimeline } from "@/components/candidate-detail/activity-timeline";
import { AdminReviewPanel } from "@/components/admin-review-panel";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Collapsible wrapper                                               */
/* ------------------------------------------------------------------ */

function Collapsible({
  title,
  badge,
  icon,
  defaultOpen = false,
  children,
}: {
  title: string;
  badge?: React.ReactNode;
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-5 py-3.5"
      >
        <div className="flex items-center gap-2 text-sm font-semibold">
          {icon}
          {title}
          {badge}
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {open && <CardContent className="border-t pt-4">{children}</CardContent>}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Tier / score badge                                                */
/* ------------------------------------------------------------------ */

function TierBadge({ tier, score }: { tier?: string; score?: number }) {
  const tierLabel = tier === "amber" ? "review" : tier;
  const colors: Record<string, string> = {
    green: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    amber: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
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
    <div className="space-y-4">
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
          <div className="space-y-1.5">
            {experience.map((w: any, i: number) => (
              <div key={i} className="text-sm">
                <span className="font-medium">{w.role ?? w.title}</span>
                {w.company && <span className="text-muted-foreground"> at {w.company}</span>}
                {w.duration && <span className="ml-2 text-xs text-muted-foreground">({w.duration})</span>}
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
          <div className="space-y-1.5">
            {education.map((e: any, i: number) => (
              <div key={i} className="text-sm">
                <span className="font-medium">{e.degree}</span>
                {e.institution && <span className="text-muted-foreground"> — {e.institution}</span>}
                {e.year && <span className="ml-2 text-xs text-muted-foreground">({e.year})</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {profile.linkedin_url && (
          <a href={profile.linkedin_url.startsWith("http") ? profile.linkedin_url : `https://${profile.linkedin_url}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-primary">
            <ExternalLink className="h-3 w-3" /> LinkedIn
          </a>
        )}
        {profile.github_url && (
          <a href={profile.github_url.startsWith("http") ? profile.github_url : `https://${profile.github_url}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-primary">
            <ExternalLink className="h-3 w-3" /> GitHub
          </a>
        )}
        {profile.portfolio_url && (
          <a href={profile.portfolio_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-primary">
            <ExternalLink className="h-3 w-3" /> Portfolio
          </a>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Fit Score Breakdown                                               */
/* ------------------------------------------------------------------ */

function FitBreakdownSection({ breakdown }: { breakdown: any }) {
  if (!breakdown) return null;

  const dims = breakdown.dimensions;
  const dimArray = Array.isArray(dims)
    ? dims
    : dims && typeof dims === "object"
      ? Object.entries(dims)
          .filter(([k]) => k !== "cultural_fit")
          .map(([k, v]: [string, any]) => ({
            name: k.replace(/_/g, " "),
            score: v?.score ?? v?.value ?? (typeof v === "number" ? v : null),
            rationale: v?.rationale,
            data_status: v?.data_status ?? "verified",
            evidence: v?.evidence ?? [],
          }))
      : [];

  const pendingItems: string[] = breakdown.pending_verification ?? [];
  const scoringPass = breakdown.scoring_pass;

  return (
    <Collapsible title="Fit Score Breakdown" badge={<TierBadge tier={breakdown.deterministic_tier} score={breakdown.overall_score} />}>
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

        {dimArray.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2">
            {dimArray.map((d: any, i: number) => {
              const isPending = d.data_status === "pending_verification" || d.score == null;
              return (
                <div key={i} className={cn("rounded-lg border p-3", isPending && "border-dashed border-muted-foreground/30 bg-muted/20")}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium capitalize">{d.name}</span>
                      {isPending && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-amber-700">
                          Pending
                        </span>
                      )}
                      {!isPending && d.data_status === "verified" && (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-emerald-700">
                          Verified
                        </span>
                      )}
                    </div>
                    {isPending ? (
                      <span className="text-xs italic text-muted-foreground">No data</span>
                    ) : (
                      <span className={cn(
                        "font-mono text-sm font-bold",
                        (d.score ?? 0) >= 60 ? "text-emerald-600" : "text-red-600"
                      )}>{d.score}</span>
                    )}
                  </div>
                  {d.rationale && <p className="mt-1 text-xs text-muted-foreground">{d.rationale}</p>}
                  {d.evidence?.length > 0 && (
                    <div className="mt-1.5 space-y-0.5">
                      {d.evidence.map((e: string, j: number) => (
                        <p key={j} className="text-[11px] italic text-muted-foreground/80">&ldquo;{e}&rdquo;</p>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
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

        {breakdown.green_flags?.length > 0 && (
          <div className="space-y-1">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600">
              <ThumbsUp className="h-3.5 w-3.5" /> Strengths
            </h4>
            <ul className="space-y-0.5">
              {breakdown.green_flags.map((f: string, i: number) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {f}
                </li>
              ))}
            </ul>
          </div>
        )}

        {breakdown.red_flags?.length > 0 && (
          <div className="space-y-1">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive">
              <ThumbsDown className="h-3.5 w-3.5" /> Red Flags
            </h4>
            <ul className="space-y-0.5">
              {breakdown.red_flags.map((f: string, i: number) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                </li>
              ))}
            </ul>
          </div>
        )}

        {breakdown.knock_outs?.length > 0 && (
          <div className="space-y-1">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive">
              <AlertTriangle className="h-3.5 w-3.5" /> Knock-outs
            </h4>
            <ul className="space-y-0.5">
              {breakdown.knock_outs.map((f: string, i: number) => (
                <li key={i} className="text-sm text-destructive">{f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Collapsible>
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
/*  Screening evaluation                                              */
/* ------------------------------------------------------------------ */

function ScreeningSection({ evaluation }: { evaluation: any }) {
  if (!evaluation) return null;
  const answers = evaluation.answers || evaluation.question_scores || [];

  return (
    <Collapsible
      title="Screening Evaluation"
      badge={evaluation.composite_score != null ? <TierBadge score={evaluation.composite_score} /> : undefined}
    >
      <div className="space-y-3">
        {evaluation.summary && <p className="text-sm text-muted-foreground">{evaluation.summary}</p>}
        {Array.isArray(answers) && answers.length > 0 && (
          <div className="space-y-2">
            {answers.map((a: any, i: number) => (
              <div key={i} className="rounded-lg border p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{a.question || `Q${i + 1}`}</span>
                  {a.score != null && <span className="font-mono text-xs font-semibold">{a.score}/10</span>}
                </div>
                {a.rationale && <p className="mt-1 text-xs text-muted-foreground">{a.rationale}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
    </Collapsible>
  );
}

/* ------------------------------------------------------------------ */
/*  Assignment                                                        */
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

function AssignmentSection({
  submission,
  role,
}: {
  submission: any;
  role: any;
}) {
  if (!submission && !role?.assignment_brief) return null;

  const parseResult = submission?.parse_result;
  const completeness = parseResult?.completeness;
  const quality = parseResult?.quality_signals;
  const submittedAt = submission?.submitted_at;

  return (
    <Collapsible
      title="Assignment"
      icon={<ClipboardCheck className="h-4 w-4" />}
      badge={
        parseResult ? (
          <span className={cn(
            "rounded-full px-2.5 py-0.5 text-xs font-semibold",
            completeness?.followed_instructions
              ? "bg-emerald-100 text-emerald-700"
              : "bg-amber-100 text-amber-700"
          )}>
            {completeness?.followed_instructions ? "Complete" : "Incomplete"}
          </span>
        ) : submission ? (
          <span className="rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-semibold text-blue-700">Submitted</span>
        ) : undefined
      }
      defaultOpen={true}
    >
      <div className="space-y-5">
        {/* Submission metadata */}
        {submission && (
          <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Submission Details</h4>
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

            {submission.project_choice && (
              <div>
                <span className="text-xs text-muted-foreground">Project Choice:</span>
                <span className="ml-2 text-sm font-medium">{submission.project_choice}</span>
              </div>
            )}

            {/* Links */}
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
                        target="_blank"
                        rel="noopener noreferrer"
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

            {/* Deployed URL */}
            {submission.deployed_url && (
              <div className="flex items-center gap-2">
                <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">Deployed:</span>
                <a href={submission.deployed_url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary hover:underline">
                  {submission.deployed_url}
                </a>
              </div>
            )}

            {/* Files */}
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

            {/* Notes */}
            {submission.notes && (
              <div>
                <span className="text-xs text-muted-foreground block mb-1">Candidate Notes:</span>
                <p className="whitespace-pre-wrap text-sm rounded-lg border bg-background p-3">{submission.notes}</p>
              </div>
            )}
          </div>
        )}

        {/* AI Analysis */}
        {parseResult && (
          <div className="space-y-4">
            {/* Summary */}
            {parseResult.summary && (
              <div className="rounded-lg border-l-4 border-primary bg-primary/5 p-4">
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-primary">AI Analysis Summary</h4>
                <p className="text-sm leading-relaxed">{parseResult.summary}</p>
              </div>
            )}

            {/* Quality signals */}
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

            {/* Completeness */}
            {completeness && (
              <div className="rounded-lg border p-3">
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
              </div>
            )}

            {/* Highlights + Concerns */}
            <div className="grid gap-3 sm:grid-cols-2">
              {parseResult.highlights?.length > 0 && (
                <div className="space-y-1">
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600">
                    <ThumbsUp className="h-3.5 w-3.5" /> Highlights
                  </h4>
                  <ul className="space-y-0.5">
                    {parseResult.highlights.map((h: string, i: number) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {h}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {parseResult.concerns?.length > 0 && (
                <div className="space-y-1">
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive">
                    <ThumbsDown className="h-3.5 w-3.5" /> Concerns
                  </h4>
                  <ul className="space-y-0.5">
                    {parseResult.concerns.map((c: string, i: number) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {c}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Evidence quotes */}
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

            {/* Suggested HR focus */}
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

        {/* Brief */}
        {role?.assignment_brief && (
          <div>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Assignment Brief</h4>
            <p className="whitespace-pre-wrap text-sm text-muted-foreground">{role.assignment_brief}</p>
          </div>
        )}

      </div>
    </Collapsible>
  );
}

/* ------------------------------------------------------------------ */
/*  Meeting reports                                                   */
/* ------------------------------------------------------------------ */

function MeetingReportsSection({ reports }: { reports: Record<string, any> | null }) {
  if (!reports || Object.keys(reports).length === 0) return null;
  const roundLabels: Record<string, string> = { technical: "Technical Round", ceo: "CEO Round", hr: "HR Discussion" };

  return (
    <Collapsible title="Interview Reports">
      <div className="space-y-4">
        {Object.entries(reports).map(([round, report]: [string, any]) => (
          <div key={round} className="rounded-lg border p-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold">{roundLabels[round] ?? `${round} Round`}</h4>
              {report.verdict && (
                <span className={cn(
                  "rounded-full px-2 py-0.5 text-xs font-medium",
                  report.verdict.includes("pass") ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
                )}>{report.verdict.replace(/_/g, " ")}</span>
              )}
            </div>
            {report.summary && <p className="mt-2 text-sm text-muted-foreground">{report.summary}</p>}
            {report.strengths?.length > 0 && (
              <div className="mt-2">
                <span className="text-xs font-semibold text-emerald-600">Strengths:</span>
                <ul className="ml-4 list-disc text-sm">{report.strengths.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
              </div>
            )}
            {report.red_flags?.length > 0 && (
              <div className="mt-2">
                <span className="text-xs font-semibold text-destructive">Concerns:</span>
                <ul className="ml-4 list-disc text-sm">{report.red_flags.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
              </div>
            )}
          </div>
        ))}
      </div>
    </Collapsible>
  );
}

/* ------------------------------------------------------------------ */
/*  Voice Screen Evaluation + Transcript                             */
/* ------------------------------------------------------------------ */

function VoiceScreenSection({ voice }: { voice: any }) {
  if (!voice) return null;
  const [showTranscript, setShowTranscript] = useState(false);
  const isPass = voice.verdict === "clear_pass";
  const transcript: any[] = voice.transcript ?? [];
  const questions: any[] = voice.questions ?? [];
  const perQ: any[] = voice.per_question ?? [];

  const qMap = new Map<string, any>();
  if (Array.isArray(questions)) {
    for (const q of questions) {
      if (q.id) qMap.set(q.id, q);
    }
  }

  const durationMin = voice.duration_sec ? Math.round(voice.duration_sec / 60) : null;

  return (
    <Collapsible
      title="Voice Screen"
      icon={<Phone className="h-4 w-4" />}
      badge={
        <span className={cn(
          "rounded-full px-2.5 py-0.5 text-xs font-semibold",
          isPass
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
            : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
        )}>
          {voice.overall_score != null && <>{voice.overall_score} · </>}
          {isPass ? "Pass" : "Rejected"}
        </span>
      }
      defaultOpen={true}
    >
      <div className="space-y-4">
        {/* Verdict rationale */}
        {voice.verdict_rationale && (
          <div className={cn(
            "rounded-lg border p-3",
            isPass ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20" : "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20"
          )}>
            <div className="flex items-center gap-2 text-sm font-semibold">
              {isPass ? <ThumbsUp className="h-4 w-4 text-emerald-600" /> : <ThumbsDown className="h-4 w-4 text-red-600" />}
              {isPass ? "Selected" : "Rejected"} — Reason
            </div>
            <p className="mt-1.5 text-sm">{voice.verdict_rationale}</p>
          </div>
        )}

        {/* Meta row */}
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          {durationMin != null && (
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3 w-3" /> {durationMin} min call
            </span>
          )}
          {voice.started_at && (
            <span>
              {new Date(voice.started_at).toLocaleString("en-IN", {
                timeZone: "Asia/Kolkata", month: "short", day: "numeric",
                hour: "2-digit", minute: "2-digit",
              })}
            </span>
          )}
        </div>

        {/* Strengths + Red flags */}
        <div className="grid gap-3 sm:grid-cols-2">
          {voice.strengths?.length > 0 && (
            <div className="space-y-1">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-600">
                <ThumbsUp className="h-3.5 w-3.5" /> Strengths
              </h4>
              <ul className="space-y-0.5">
                {voice.strengths.map((s: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" /> {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {voice.red_flags?.length > 0 && (
            <div className="space-y-1">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-destructive">
                <ThumbsDown className="h-3.5 w-3.5" /> Red Flags
              </h4>
              <ul className="space-y-0.5">
                {voice.red_flags.map((f: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" /> {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Per-question scores */}
        {perQ.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Question Scores</h4>
            <div className="space-y-2">
              {perQ.map((pq: any, i: number) => {
                const q = qMap.get(pq.question_id);
                return (
                  <div key={i} className="rounded-lg border p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">
                        {q?.question || pq.question_id || `Q${i + 1}`}
                      </span>
                      {pq.score != null && (
                        <span className={cn(
                          "font-mono text-sm font-bold",
                          pq.score >= 60 ? "text-emerald-600" : "text-red-600"
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
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Transcript toggle */}
        {transcript.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setShowTranscript(!showTranscript)}
              className="flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              {showTranscript ? "Hide" : "Show"} Conversation Transcript ({transcript.length} Q&A)
            </button>
            {showTranscript && (
              <div className="mt-3 space-y-3 rounded-lg border bg-muted/30 p-4">
                {transcript.map((item: any, i: number) => {
                  const questionText = item.question || item.question_text || qMap.get(item.question_id)?.question || `Question ${i + 1}`;
                  const answerText = item.answer_transcript || item.answer || "";
                  return (
                    <div key={i} className="space-y-1.5">
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
    </Collapsible>
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
    const dims = fb.dimensions;
    if (dims && typeof dims === "object" && !Array.isArray(dims)) {
      for (const [k, v] of Object.entries(dims) as [string, any][]) {
        if (v?.score != null) {
          evidence.push({ key: k.replace(/_/g, " "), value: `${v.score}/100`, source: "Fit Score", confidence: v.score / 100 });
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

function EvidenceAndDecisions({ data }: { data: any }) {
  const [tab, setTab] = useState<"evidence" | "decisions">("evidence");
  const { evidence, decisions } = deriveEvidenceAndDecisions(data);

  return (
    <Card>
      <div className="flex border-b">
        <button
          onClick={() => setTab("evidence")}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition",
            tab === "evidence" ? "border-b-2 border-primary text-primary" : "text-muted-foreground hover:text-foreground"
          )}
        >
          <FileSearch className="h-3.5 w-3.5" /> Evidence ({evidence.length})
        </button>
        <button
          onClick={() => setTab("decisions")}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition",
            tab === "decisions" ? "border-b-2 border-primary text-primary" : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Scale className="h-3.5 w-3.5" /> Decisions ({decisions.length})
        </button>
      </div>

      <CardContent className="p-0">
        {tab === "evidence" && (
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

        {tab === "decisions" && (
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
  );
}

/* ------------------------------------------------------------------ */
/*  Proceed to Next Round                                             */
/* ------------------------------------------------------------------ */

const ROUND_TRANSITIONS: Record<string, { targetStage: string; nextRound: string; label: string; description: string }> = {
  // After assignment evaluation → Technical Interview
  assignment_submitted: { targetStage: "assessment_evaluated", nextRound: "technical", label: "Proceed to Technical Interview", description: "Panel members will receive an email to confirm their availability for the technical round." },
  assessment_completed: { targetStage: "assessment_evaluated", nextRound: "technical", label: "Proceed to Technical Interview", description: "Panel members will receive an email to confirm their availability for the technical round." },
  assessment_pending_review: { targetStage: "assessment_evaluated", nextRound: "technical", label: "Proceed to Technical Interview", description: "Panel members will receive an email to confirm their availability for the technical round." },
  report_ready: { targetStage: "assessment_evaluated", nextRound: "technical", label: "Proceed to Technical Interview", description: "Panel members will receive an email to confirm their availability for the technical round." },
  // After technical evaluation → CEO Interview
  technical_meeting_completed: { targetStage: "technical_evaluated", nextRound: "ceo", label: "Proceed to CEO Interview", description: "CEO panel members will receive an email to confirm their availability." },
  technical_evaluated: { targetStage: "technical_evaluated", nextRound: "ceo", label: "Proceed to CEO Interview", description: "CEO panel members will receive an email to confirm their availability." },
  technical_pending_approval: { targetStage: "technical_evaluated", nextRound: "ceo", label: "Proceed to CEO Interview", description: "CEO panel members will receive an email to confirm their availability." },
  // After CEO meeting → HR Discussion
  ceo_meeting_completed: { targetStage: "ceo_meeting_completed", nextRound: "hr", label: "Proceed to HR Discussion", description: "HR panel members will receive an email to confirm their availability." },
  ceo_pending_approval: { targetStage: "ceo_meeting_completed", nextRound: "hr", label: "Proceed to HR Discussion", description: "HR panel members will receive an email to confirm their availability." },
};

function ProceedToNextRound({
  applicationId,
  currentStage,
  onChanged,
}: {
  applicationId: string;
  currentStage: string;
  onChanged: () => void;
}) {
  const transition = ROUND_TRANSITIONS[currentStage];
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Test panel email
  const [testingEmail, setTestingEmail] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  if (!transition && !done) return null;

  const handleProceed = async () => {
    if (!transition) return;
    setLoading(true);
    setError(null);
    try {
      await api.post(`/dashboard/v1/candidates/${applicationId}/stage`, {
        stage: transition.targetStage,
        note: `Proceeded to ${transition.nextRound} round`,
      });
      setDone(true);
      onChanged();
    } catch (e: any) {
      setError(e.message || "Failed to advance");
    } finally {
      setLoading(false);
    }
  };

  const handleTestEmail = async () => {
    const round = transition?.nextRound;
    if (!round) return;
    setTestingEmail(true);
    setTestError(null);
    setTestResult(null);
    try {
      const res = await api.post<{ message: string; meeting_session_id: string }>(
        `/dashboard/v1/candidates/${applicationId}/trigger-panel-availability`,
        { round },
      );
      setTestResult(res.message || `Panel emails sent for ${round} round`);
    } catch (e: any) {
      setTestError(e.message || "Failed to send test emails");
    } finally {
      setTestingEmail(false);
    }
  };

  if (done) {
    return (
      <Card className="border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20">
        <CardContent className="flex items-center gap-3 p-4">
          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
          <div>
            <p className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
              Candidate advanced to {transition?.nextRound} round
            </p>
            <p className="text-xs text-emerald-600/80 dark:text-emerald-400/80">
              Panel members will receive availability confirmation emails shortly.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{transition!.label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{transition!.description}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTestEmail}
              disabled={testingEmail}
              className="gap-1.5"
              title="Send panel availability emails without changing the candidate's stage"
            >
              {testingEmail ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
              Test Panel Email
            </Button>
            <Button onClick={handleProceed} disabled={loading} className="gap-1.5">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
              {transition!.label}
            </Button>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> {error}
          </div>
        )}
        {testError && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> {testError}
          </div>
        )}
        {testResult && (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> {testResult}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                         */
/* ------------------------------------------------------------------ */

export default function CandidateDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

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

  return (
    <>
      <Topbar title={cand.name ?? "Candidate"} />
      <div className="mx-auto max-w-5xl space-y-5 p-6 pb-24">
        {/* Header */}
        <div className="flex items-start gap-4">
          <Button variant="ghost" size="sm" className="mt-0.5" onClick={() => router.back()}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold">{cand.name}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
              {cand.email && <span className="inline-flex items-center gap-1"><Mail className="h-3 w-3" />{cand.email}</span>}
              {cand.phone && <span className="inline-flex items-center gap-1"><Phone className="h-3 w-3" />{cand.phone}</span>}
              {profile.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{profile.location}</span>}
              {role.title && <span className="inline-flex items-center gap-1"><Briefcase className="h-3 w-3" />{role.title}</span>}
            </div>
          </div>
          <StatusTag stage={data.current_stage as Stage} />
        </div>

        {isRejected && <RejectionReasonBanner audit={data.audit ?? []} voiceEvaluation={data.voice_evaluation} />}

        {/* Admin review panel */}
        <AdminReviewPanel
          applicationId={id}
          currentStage={data.current_stage}
          meetingReports={data.meeting_reports}
          adminReview={data.admin_review}
          onChanged={() => mutate()}
        />

        {/* Score cards */}
        <div className="grid gap-3 sm:grid-cols-3">
          {data.fit_score != null && (
            <Card className="p-4">
              <div className="text-xs font-medium text-muted-foreground">Fit Score</div>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-2xl font-bold">{data.fit_score}</span>
                <TierBadge tier={data.fit_tier} />
              </div>
            </Card>
          )}
          {data.screening_evaluation?.composite_score != null && (
            <Card className="p-4">
              <div className="text-xs font-medium text-muted-foreground">Screening Score</div>
              <div className="mt-1">
                <span className="text-2xl font-bold">{data.screening_evaluation.composite_score}</span>
              </div>
            </Card>
          )}
          {data.voice_evaluation?.overall_score != null && (
            <Card className="p-4">
              <div className="text-xs font-medium text-muted-foreground">Voice Screen</div>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-2xl font-bold">{data.voice_evaluation.overall_score}</span>
                <span className={cn(
                  "rounded-full px-2 py-0.5 text-xs font-semibold",
                  data.voice_evaluation.verdict === "clear_pass"
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-red-100 text-red-700"
                )}>
                  {data.voice_evaluation.verdict === "clear_pass" ? "Pass" : "Rejected"}
                </span>
              </div>
            </Card>
          )}
          {data.resume_download_url && (
            <Card className="p-4">
              <div className="text-xs font-medium text-muted-foreground">Resume</div>
              <div className="mt-1">
                <a href={data.resume_download_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline">
                  <Download className="h-3.5 w-3.5" /> {data.resume_filename ?? "Download"}
                </a>
              </div>
            </Card>
          )}
        </div>

        {/* Profile snapshot */}
        {profile && Object.keys(profile).length > 2 && (
          <Collapsible title="Candidate Profile" defaultOpen={false}>
            <ProfileSnapshot profile={profile} />
          </Collapsible>
        )}

        {/* Fit breakdown */}
        <FitBreakdownSection breakdown={data.fit_breakdown} />

        {/* Screening */}
        <ScreeningSection evaluation={data.screening_evaluation} />

        {/* Voice Screen */}
        <VoiceScreenSection voice={data.voice_evaluation} />

        {/* Assignment */}
        <AssignmentSection submission={data.assignment_submission} role={role} />

        {/* Proceed to next round */}
        <ProceedToNextRound
          applicationId={id}
          currentStage={data.current_stage}
          onChanged={() => mutate()}
        />

        {/* Meeting reports */}
        <MeetingReportsSection reports={data.meeting_reports} />

        {/* Evidence & Decisions */}
        <EvidenceAndDecisions data={data} />

        {/* Activity timeline */}
        <Collapsible title="Activity Timeline" defaultOpen={true}>
          <ActivityTimeline entries={data.audit ?? []} />
        </Collapsible>
      </div>
    </>
  );
}
