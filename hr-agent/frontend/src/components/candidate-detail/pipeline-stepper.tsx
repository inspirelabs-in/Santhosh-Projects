"use client";

import type { StageViewEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

export function PipelineStepper({ stages }: { stages: StageViewEntry[] }) {
  const enabled = stages.filter((s) => s.is_enabled);

  if (enabled.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <div className="flex items-center gap-0 min-w-max py-2">
        {enabled.map((s, i) => {
          const isLast = i === enabled.length - 1;
          const isDone = s.processing_status === "processed" && s.verdict === "pass";
          const isFailed = s.verdict === "fail";
          // Borderline score parked for a human pass/reject call.
          const isReview = s.verdict === "needs_review";
          // Started but awaiting a decision (call placed / meeting booked).
          const isOnGoing = s.verdict === "on_going";
          // FE-6: don't fold processing into isCurrent (it made the dedicated
          // processing branch unreachable); keep them distinct and order processing
          // before the plain-current spinner.
          const isCurrent = s.is_current;
          const isProcessing = s.processing_status === "processing";
          const isPending = s.processing_status === "unprocessed" && s.verdict === "pending";

          let icon: React.ReactNode;
          let dotColor: string;
          let lineColor: string;

          if (isFailed) {
            icon = <XCircle className="h-4 w-4 text-destructive" />;
            dotColor = "bg-destructive border-destructive";
            lineColor = "bg-destructive/40";
          } else if (isReview) {
            icon = <AlertCircle className="h-4 w-4 text-amber-500" />;
            dotColor = "bg-amber-500 border-amber-500";
            lineColor = "bg-amber-300 dark:bg-amber-700";
          } else if (isDone) {
            icon = <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
            dotColor = "bg-emerald-500 border-emerald-500";
            lineColor = "bg-emerald-300 dark:bg-emerald-700";
          } else if (isOnGoing) {
            icon = <Loader2 className="h-4 w-4 animate-spin text-blue-500" />;
            dotColor = "bg-blue-500 border-blue-500";
            lineColor = "bg-blue-300 dark:bg-blue-700";
          } else if (isProcessing) {
            icon = <Loader2 className="h-4 w-4 animate-spin text-amber-500" />;
            dotColor = "bg-amber-500 border-amber-500";
            lineColor = "bg-amber-300 dark:bg-amber-700";
          } else if (isCurrent) {
            icon = <Loader2 className="h-4 w-4 animate-spin text-primary" />;
            dotColor = "bg-primary border-primary";
            lineColor = "bg-primary/30";
          } else {
            icon = <Circle className="h-4 w-4 text-muted-foreground/40" />;
            dotColor = "bg-muted-foreground/20 border-muted-foreground/30";
            lineColor = "bg-muted-foreground/20";
          }

          return (
            <div key={s.stage_key} className="flex items-center gap-0">
              <div className="flex flex-col items-center gap-1">
                <div
                  className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-full border-2 transition-colors",
                    dotColor,
                    isCurrent && "ring-2 ring-primary/30 ring-offset-2 ring-offset-background",
                  )}
                >
                  {icon}
                </div>
                <div className="flex flex-col items-center gap-0">
                  <span
                    className={cn(
                      "whitespace-nowrap text-[10px] font-medium leading-tight",
                      (isCurrent || isOnGoing) && "text-foreground font-semibold",
                      isDone && "text-muted-foreground",
                      isPending && "text-muted-foreground/50",
                      isReview && "text-amber-600 dark:text-amber-400 font-semibold",
                      isFailed && "text-destructive",
                    )}
                  >
                    {s.label}
                  </span>
                  {s.verdict && s.verdict !== "pending" && (
                    <span
                      className={cn(
                        "whitespace-nowrap text-[9px] font-mono",
                        isFailed
                          ? "text-destructive"
                          : isReview
                          ? "text-amber-600 dark:text-amber-400"
                          : isOnGoing
                          ? "text-blue-600 dark:text-blue-400"
                          : "text-emerald-600 dark:text-emerald-400",
                      )}
                    >
                      {isReview ? "needs review" : isOnGoing ? "in progress" : s.verdict}
                    </span>
                  )}
                </div>
              </div>
              {!isLast && (
                <div className={cn("mx-1 h-0.5 w-6 rounded-full", lineColor)} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
