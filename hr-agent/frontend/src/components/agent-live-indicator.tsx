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

export function AgentLiveIndicator() {
  const { data } = useSWR<AgentEvent[]>(
    "/dashboard/v1/notifications?limit=10",
    swrFetcher,
    { refreshInterval: 6000 },
  );

  const now = Date.now();
  const recentCount = (data ?? []).filter(
    (e) =>
      e.actor === "agent" &&
      now - new Date(e.created_at).getTime() < 3 * 60 * 1000,
  ).length;

  if (recentCount === 0) return null;

  return (
    <div className="flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/5 px-2.5 py-1 text-[10px] font-medium text-primary">
      <Loader2 className="h-3 w-3 animate-spin" />
      <span className="hidden sm:inline">Agent active</span>
      <span className="font-mono tabular-nums">{recentCount}</span>
    </div>
  );
}
