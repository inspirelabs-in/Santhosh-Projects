"use client";

import { cn } from "@/lib/utils";
import { STAGE_LABELS, STAGE_ORDER, type Stage } from "./status-tag";

interface TimelineEvent {
  stage: Stage;
  at?: string | null;
  detail?: string | null;
}

interface Props {
  current: Stage;
  events?: TimelineEvent[];
  terminal?: "hired" | "rejected" | "needs_hr_review" | null;
}

export function JourneyTimeline({ current, events = [], terminal = null }: Props) {
  const currentIdx = STAGE_ORDER.indexOf(current);
  const eventMap = new Map(events.map((e) => [e.stage, e]));

  return (
    <ol className="relative space-y-6 pl-6">
      {/* vertical rail */}
      <span
        aria-hidden
        className="absolute left-[7px] top-1.5 bottom-1.5 w-px bg-border"
      />

      {STAGE_ORDER.map((stage, i) => {
        const state: "done" | "active" | "future" =
          currentIdx < 0
            ? "future"
            : i < currentIdx
            ? "done"
            : i === currentIdx
            ? "active"
            : "future";
        const ev = eventMap.get(stage);
        return (
          <li key={stage} className="relative">
            <span
              aria-hidden
              className={cn(
                "absolute -left-6 top-1.5 h-3.5 w-3.5 rounded-full border-2",
                state === "done" && "bg-foreground border-foreground",
                state === "active" &&
                  "bg-background border-primary ring-4 ring-primary/20 animate-pulse",
                state === "future" && "bg-background border-border",
              )}
            />
            <div className="flex items-baseline justify-between gap-3">
              <h3
                className={cn(
                  "font-display text-lg leading-tight",
                  state === "future" && "text-muted-foreground",
                )}
              >
                {STAGE_LABELS[stage]}
              </h3>
              {ev?.at && (
                <time className="font-mono text-[11px] text-muted-foreground">
                  {new Date(ev.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
                </time>
              )}
            </div>
            {ev?.detail && (
              <p className="mt-1 text-sm text-muted-foreground">{ev.detail}</p>
            )}
          </li>
        );
      })}

      {terminal && (
        <li className="relative">
          <span
            aria-hidden
            className={cn(
              "absolute -left-6 top-1.5 h-3.5 w-3.5 rounded-full border-2",
              terminal === "hired" && "bg-foreground border-foreground",
              terminal === "rejected" && "bg-destructive border-destructive",
              terminal === "needs_hr_review" && "bg-warning border-warning",
            )}
          />
          <h3 className="font-display text-lg leading-tight">
            {STAGE_LABELS[terminal as Stage] ?? terminal}
          </h3>
        </li>
      )}
    </ol>
  );
}
