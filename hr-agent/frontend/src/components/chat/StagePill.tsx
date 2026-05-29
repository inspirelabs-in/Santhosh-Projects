"use client";

import { CheckCircle2, FileText, MessageCircle, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

const STAGE_META: Record<
  string,
  { label: string; icon: React.ComponentType<{ className?: string }>; tone: string }
> = {
  intake: { label: "Getting started", icon: Sparkles, tone: "text-muted-foreground" },
  screening: { label: "Screening", icon: MessageCircle, tone: "text-blue-600 dark:text-blue-400" },
  assignment: { label: "Assignment", icon: FileText, tone: "text-amber-600 dark:text-amber-400" },
  submitted: { label: "Submitted", icon: CheckCircle2, tone: "text-emerald-600 dark:text-emerald-400" },
  completed: { label: "Done", icon: CheckCircle2, tone: "text-emerald-600 dark:text-emerald-400" },
  rejected: { label: "Closed", icon: CheckCircle2, tone: "text-muted-foreground" },
};

export function StagePill({ stage, className }: { stage: string; className?: string }) {
  const meta = STAGE_META[stage] ?? STAGE_META.intake;
  const Icon = meta.icon;
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-background/80 px-2.5 py-1 text-xs font-medium",
        meta.tone,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{meta.label}</span>
    </div>
  );
}
