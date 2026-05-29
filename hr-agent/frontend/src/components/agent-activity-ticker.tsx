"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  Bot,
  Brain,
  FileSearch,
  Mail,
  Phone,
  Sparkles,
  UserCheck,
  Zap,
} from "lucide-react";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface AgentEvent {
  id: number;
  action: string;
  actor: string;
  application_id: string | null;
  candidate_id: string | null;
  candidate_name: string | null;
  role_title: string | null;
  details: any;
  created_at: string;
}

const ACTION_CONFIG: Record<
  string,
  { icon: typeof Bot; label: string; verb: string; color: string }
> = {
  intake_completed: {
    icon: UserCheck,
    label: "Intake",
    verb: "processed new application",
    color: "text-primary",
  },
  screening_sent: {
    icon: Mail,
    label: "Screening",
    verb: "sent screening questions",
    color: "text-blue-500",
  },
  screening_evaluated: {
    icon: Brain,
    label: "Evaluation",
    verb: "evaluated screening answers",
    color: "text-violet-500",
  },
  assignment_sent: {
    icon: FileSearch,
    label: "Assignment",
    verb: "sent technical assignment",
    color: "text-amber-500",
  },
  assignment_parsed: {
    icon: Brain,
    label: "Review",
    verb: "reviewed assignment submission",
    color: "text-violet-500",
  },
  journey_report_generated: {
    icon: Sparkles,
    label: "Report",
    verb: "generated journey report",
    color: "text-emerald-500",
  },
  voice_screen_dispatched: {
    icon: Phone,
    label: "Voice",
    verb: "initiated voice screening call",
    color: "text-cyan-500",
  },
  voice_screen_evaluated: {
    icon: Brain,
    label: "Analysis",
    verb: "analyzed voice screening",
    color: "text-violet-500",
  },
  fit_score_computed: {
    icon: Zap,
    label: "Scoring",
    verb: "computed fit score",
    color: "text-amber-500",
  },
  auto_rejected_fit: {
    icon: Zap,
    label: "Filter",
    verb: "auto-filtered low-fit candidate",
    color: "text-red-400",
  },
  parked_no_role: {
    icon: Bot,
    label: "Triage",
    verb: "parked for HR review",
    color: "text-muted-foreground",
  },
};

function relTime(iso: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function AgentActivityTicker({ limit = 8 }: { limit?: number }) {
  const { data } = useSWR<AgentEvent[]>(
    "/dashboard/v1/notifications?limit=30",
    swrFetcher,
    { refreshInterval: 5000 },
  );

  const [prevIds, setPrevIds] = useState<Set<number>>(new Set());
  const [newIds, setNewIds] = useState<Set<number>>(new Set());

  const events = (data ?? [])
    .filter((e) => e.actor === "agent" && ACTION_CONFIG[e.action])
    .slice(0, limit);

  useEffect(() => {
    if (!data) return;
    const currentIds = new Set(events.map((e) => e.id));
    const fresh = new Set<number>();
    for (const id of currentIds) {
      if (!prevIds.has(id)) fresh.add(id);
    }
    if (fresh.size > 0) {
      setNewIds(fresh);
      setTimeout(() => setNewIds(new Set()), 1500);
    }
    setPrevIds(currentIds);
  }, [data]);

  if (events.length === 0) {
    return (
      <div className="px-3 py-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Bot className="h-3.5 w-3.5 animate-pulse" />
          <span className="font-mono text-[10px] uppercase tracking-[0.15em]">
            Agent idle — waiting for applications
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
        </span>
        <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
          Agent activity
        </span>
      </div>
      <ul className="space-y-px">
        {events.map((e) => {
          const cfg = ACTION_CONFIG[e.action]!;
          const Icon = cfg.icon;
          const isNew = newIds.has(e.id);

          const inner = (
            <div
              className={cn(
                "group flex items-start gap-2.5 rounded-md px-3 py-1.5 transition-all",
                e.application_id
                  ? "hover:bg-accent/30 cursor-pointer"
                  : "",
                isNew && "agent-event-enter bg-primary/5",
              )}
            >
              <Icon
                className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", cfg.color)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-1">
                  <span className="truncate text-[11px] leading-tight text-foreground/90">
                    <span className={cn("font-medium", cfg.color)}>
                      {cfg.label}
                    </span>
                    {" · "}
                    {e.candidate_name ?? "candidate"}
                  </span>
                  <time className="shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground/70">
                    {relTime(e.created_at)}
                  </time>
                </div>
                <div className="truncate font-mono text-[10px] text-muted-foreground/60">
                  {cfg.verb}
                  {e.role_title ? ` · ${e.role_title}` : ""}
                </div>
              </div>
            </div>
          );

          return e.application_id ? (
            <li key={e.id}>
              <Link href={`/candidates/${e.application_id}`}>{inner}</Link>
            </li>
          ) : (
            <li key={e.id}>{inner}</li>
          );
        })}
      </ul>
    </div>
  );
}
