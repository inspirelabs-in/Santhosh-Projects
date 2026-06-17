"use client";

import useSWR from "swr";
import { evidenceApi, type PipelineConfidenceResponse } from "@/lib/api/supervisor";
import { ConfidenceBadge } from "@/components/confidence-badge";

export function LazyConfidenceBadge({
  applicationId,
  className,
}: {
  applicationId: string;
  className?: string;
}) {
  const { data } = useSWR<PipelineConfidenceResponse>(
    `confidence:${applicationId}`,
    () => evidenceApi.confidence(applicationId),
    { shouldRetryOnError: false, revalidateOnFocus: false },
  );
  return <ConfidenceBadge value={data?.overall} className={className} />;
}
