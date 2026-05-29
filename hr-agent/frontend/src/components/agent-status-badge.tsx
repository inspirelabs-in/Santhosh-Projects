"use client";

import useSWR from "swr";
import { Bot, Loader2 } from "lucide-react";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

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
      <div className="flex items-center justify-center px-2 py-2" title={isActive ? `Agent active — ${recentAgentActions.length} recent actions` : "Agent idle"}>
        <div className={cn(
          "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
          isActive ? "bg-primary/10" : "bg-muted/50",
        )}>
          {isActive ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          ) : (
            <Bot className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <div className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
        isActive ? "bg-primary/10 agent-pulse" : "bg-muted/50",
      )}>
        {isActive ? (
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
        ) : (
          <Bot className="h-3 w-3 text-muted-foreground" />
        )}
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
      {isActive && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
        </span>
      )}
    </div>
  );
}
