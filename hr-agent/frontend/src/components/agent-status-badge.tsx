"use client";

import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Minimal relevancy-scan glyph: 4 bars that rise/settle in sequence while the
 *  agent is evaluating applications, and sit flat + muted when idle. */
function RelevancyBars({ active, className }: { active: boolean; className?: string }) {
  return (
    <span className={cn("flex h-3.5 items-end gap-[2px]", className)} aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className={cn(
            "w-[2px] rounded-full",
            active ? "relv-bar-anim bg-primary" : "bg-muted-foreground/40",
          )}
          style={
            active
              ? { height: "100%", animationDelay: `${i * 140}ms` }
              : { height: "100%", transform: "scaleY(0.32)", transformOrigin: "bottom" }
          }
        />
      ))}
    </span>
  );
}

interface AgentEvent {
  id: number;
  action: string;
  actor: string;
  created_at: string;
}

const PROCESSING_ACTIONS = new Set([
  "intake_completed",
  "screening_sent",
  "screening_evaluated",
  "assignment_sent",
  "assignment_parsed",
  "voice_screen_dispatched",
  "voice_screen_evaluated",
  "fit_score_computed",
  "journey_report_generated",
]);

export function AgentStatusBadge({ collapsed = false }: { collapsed?: boolean }) {
  const { data } = useSWR<AgentEvent[]>(
    "/dashboard/v1/notifications?limit=30",
    swrFetcher,
    { refreshInterval: 6000 },
  );

  const now = Date.now();
  const recentAgentActions = (data ?? []).filter(
    (e) =>
      e.actor === "agent" &&
      PROCESSING_ACTIONS.has(e.action) &&
      now - new Date(e.created_at).getTime() < 5 * 60 * 1000,
  );

  const isActive = recentAgentActions.length > 0;

  if (collapsed) {
    return (
      <div className="flex items-center justify-center px-2 py-2" title={isActive ? `Agent active, ${recentAgentActions.length} recent actions` : "Agent idle"}>
        <div className={cn(
          "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
          isActive ? "bg-primary/10" : "bg-muted/50",
        )}>
          <RelevancyBars active={isActive} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <div className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
        isActive ? "bg-primary/10" : "bg-muted/50",
      )}>
        <RelevancyBars active={isActive} className="h-3" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] font-medium leading-tight">
          {isActive ? "Agent active" : "Agent idle"}
        </div>
        <div className="font-mono text-[8px] text-muted-foreground">
          {isActive
            ? `${recentAgentActions.length} action${recentAgentActions.length > 1 ? "s" : ""} in last 5m`
            : "Waiting for applications"}
        </div>
      </div>
    </div>
  );
}
