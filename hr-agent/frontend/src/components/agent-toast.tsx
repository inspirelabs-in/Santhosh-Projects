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
  candidate_name: string | null;
  role_title: string | null;
  created_at: string;
}

const ACTION_META: Record<
  string,
  { icon: typeof Bot; verb: string; color: string }
> = {
  intake_completed: { icon: UserCheck, verb: "Processed new application", color: "text-primary" },
  screening_sent: { icon: Mail, verb: "Sent screening questions", color: "text-blue-500" },
  screening_evaluated: { icon: Brain, verb: "Evaluated screening", color: "text-violet-500" },
  assignment_sent: { icon: FileSearch, verb: "Sent assignment", color: "text-amber-500" },
  assignment_parsed: { icon: Brain, verb: "Reviewed submission", color: "text-violet-500" },
  journey_report_generated: { icon: Sparkles, verb: "Generated report", color: "text-emerald-500" },
  voice_screen_dispatched: { icon: Phone, verb: "Started voice screen", color: "text-cyan-500" },
  voice_screen_evaluated: { icon: Brain, verb: "Analyzed voice screen", color: "text-violet-500" },
  voice_confirmation_dispatched: { icon: Phone, verb: "Confirmation call", color: "text-cyan-500" },
  voice_status_update_dispatched: { icon: Phone, verb: "Status update call", color: "text-cyan-500" },
  voice_meeting_schedule_dispatched: { icon: Phone, verb: "Meeting schedule call", color: "text-cyan-500" },
  voice_joining_details_dispatched: { icon: Phone, verb: "Joining details call", color: "text-emerald-500" },
  fit_score_computed: { icon: Zap, verb: "Computed fit score", color: "text-amber-500" },
};

interface Toast {
  id: number;
  event: AgentEvent;
  exiting: boolean;
}

export function AgentToastStack() {
  const { data } = useSWR<AgentEvent[]>(
    "/dashboard/v1/notifications?limit=10",
    swrFetcher,
    { refreshInterval: 5000 },
  );

  const seenRef = useRef<Set<number>>(new Set());
  const initializedRef = useRef(false);
  const mountedAt = useRef(new Date().toISOString());
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    if (!data) return;
    const agentEvents = data.filter(
      (e) => e.actor === "agent" && ACTION_META[e.action],
    );

    // First load: mark all existing events as seen without showing toasts
    if (!initializedRef.current) {
      initializedRef.current = true;
      for (const e of agentEvents) {
        seenRef.current.add(e.id);
      }
      return;
    }

    const fresh: AgentEvent[] = [];
    for (const e of agentEvents) {
      if (!seenRef.current.has(e.id) && e.created_at > mountedAt.current) {
        seenRef.current.add(e.id);
        fresh.push(e);
      } else {
        seenRef.current.add(e.id);
      }
    }

    if (fresh.length === 0) return;

    const newToasts = fresh.slice(0, 3).map((e) => ({
      id: e.id,
      event: e,
      exiting: false,
    }));

    setToasts((prev) => [...newToasts, ...prev].slice(0, 5));

    for (const t of newToasts) {
      setTimeout(() => {
        setToasts((prev) =>
          prev.map((p) => (p.id === t.id ? { ...p, exiting: true } : p)),
        );
      }, 4000);
      setTimeout(() => {
        setToasts((prev) => prev.filter((p) => p.id !== t.id));
      }, 4300);
    }
  }, [data]);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col-reverse gap-2 pointer-events-none">
      {toasts.map((t) => {
        const meta = ACTION_META[t.event.action]!;
        const Icon = meta.icon;
        const inner = (
          <div
            className={cn(
              "pointer-events-auto flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-lg",
              t.exiting ? "toast-exit" : "toast-enter",
            )}
          >
            <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10", meta.color)}>
              <Icon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="text-xs font-medium text-foreground">
                {meta.verb}
              </div>
              <div className="truncate text-[11px] text-muted-foreground">
                {t.event.candidate_name ?? "Candidate"}
                {t.event.role_title ? ` · ${t.event.role_title}` : ""}
              </div>
            </div>
            <Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
          </div>
        );

        return t.event.application_id ? (
          <Link key={t.id} href={`/candidates/${t.event.application_id}`}>
            {inner}
          </Link>
        ) : (
          <div key={t.id}>{inner}</div>
        );
      })}
    </div>
  );
}
