"use client";

import { Gauge } from "lucide-react";
import { cn } from "@/lib/utils";

export function ConfidenceBadge({
  value,
  className,
}: {
  value: number | null | undefined;
  className?: string;
}) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const color =
    pct >= 85
      ? "bg-success/15 text-success"
      : pct >= 60
        ? "bg-warning/15 text-warning"
        : "bg-destructive/15 text-destructive";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold tabular-nums",
        color,
        className,
      )}
      title={`Pipeline confidence: ${pct}%`}
    >
      <Gauge className="h-3 w-3" />
      {pct}%
    </span>
  );
}
