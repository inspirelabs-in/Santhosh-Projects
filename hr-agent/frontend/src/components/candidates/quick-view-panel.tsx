"use client";

import { X, Mail, Phone, ExternalLink } from "lucide-react";
import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { evidenceApi, type PipelineConfidenceResponse } from "@/lib/api/supervisor";
import { StatusTag, type Stage } from "@/components/status-tag";
import { ConfidenceBadge } from "@/components/confidence-badge";
import { Avatar } from "@/components/ui/avatar";
import { SkeletonLines } from "@/components/skeleton";
import Link from "next/link";

interface QuickDetail {
  application_id: string;
  candidate: { name: string | null; email: string | null; phone: string | null };
  role: { title: string } | null;
  current_stage: Stage;
  profile: Record<string, any> | null;
  screening_evaluation: any;
  created_at: string;
}

export function QuickViewPanel({
  applicationId,
  onClose,
}: {
  applicationId: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useSWR<QuickDetail>(
    `/dashboard/v1/candidates/${applicationId}`,
    swrFetcher,
    { shouldRetryOnError: false },
  );
  const { data: confidence } = useSWR<PipelineConfidenceResponse>(
    applicationId ? `confidence:${applicationId}` : null,
    () => evidenceApi.confidence(applicationId),
    { shouldRetryOnError: false },
  );

  return (
    <div className="slide-in-right flex h-full w-96 flex-col border-l border-border bg-card shadow-pop">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
          Quick view
        </span>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {isLoading || !data ? (
          <SkeletonLines lines={6} />
        ) : (
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              <Avatar name={data.candidate.name} size="lg" />
              <div className="min-w-0">
                <h3 className="text-lg font-bold truncate">
                  {data.candidate.name ?? data.candidate.email ?? "Unnamed"}
                </h3>
                {data.candidate.email && (
                  <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Mail className="h-3 w-3" /> {data.candidate.email}
                  </div>
                )}
                {data.candidate.phone && (
                  <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                    <Phone className="h-3 w-3" /> {data.candidate.phone}
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <StatusTag stage={data.current_stage} />
                <ConfidenceBadge value={confidence?.overall} />
              </div>
              {data.role && (
                <span className="text-xs text-muted-foreground">{data.role.title}</span>
              )}
            </div>

            {confidence && (
              <div className="rounded-lg border border-border bg-muted/30 p-3">
                <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                  Pipeline confidence
                </div>
                <div className="mt-1 flex items-baseline gap-3">
                  <span className="font-data text-2xl font-bold text-primary">
                    {Math.round(confidence.overall * 100)}%
                  </span>
                  <span className="text-xs uppercase text-muted-foreground">
                    {confidence.recommendation?.replace(/_/g, " ")}
                  </span>
                </div>
                {confidence.per_stage && Object.keys(confidence.per_stage).length > 0 && (
                  <div className="mt-2 space-y-1">
                    {Object.entries(confidence.per_stage).map(([stage, info]) => (
                      <div key={stage} className="flex items-center gap-2">
                        <span className="w-24 truncate text-[10px] text-muted-foreground">{stage.replace(/_/g, " ")}</span>
                        <div className="h-1 flex-1 rounded-full bg-border">
                          <div
                            className="h-1 rounded-full bg-primary"
                            style={{ width: `${Math.round((info.avg_confidence ?? 0) * 100)}%` }}
                          />
                        </div>
                        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                          {Math.round((info.avg_confidence ?? 0) * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {data.screening_evaluation && (
              <div className="rounded-lg border border-border bg-muted/30 p-3">
                <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                  Screening
                </div>
                <div className="mt-1 flex items-baseline gap-3">
                  <span className="font-data text-2xl font-bold text-primary">
                    {data.screening_evaluation.overall_score}
                  </span>
                  <span className="text-xs uppercase text-muted-foreground">
                    {data.screening_evaluation.verdict?.replace(/_/g, " ")}
                  </span>
                </div>
              </div>
            )}

            {data.profile?.skills && (
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-2">
                  Skills
                </div>
                <div className="flex flex-wrap gap-1">
                  {data.profile.skills.slice(0, 12).map((s: string) => (
                    <span key={s} className="rounded-full border border-border px-2 py-0.5 text-[11px]">
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <Link
              href={`/candidates/${applicationId}`}
              className="flex items-center gap-1.5 text-sm text-primary hover:underline"
            >
              Full profile <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
