"use client";

import Link from "next/link";
import useSWR from "swr";
import { useState, useEffect, useRef } from "react";
import { Bell } from "lucide-react";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Notification {
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

const ACTION_LABEL: Record<string, string> = {
  intake_completed: "New application",
  screening_sent: "Screening sent",
  screening_submitted: "Screening submitted",
  screening_evaluated: "Screening evaluated",
  assignment_sent: "Assignment sent",
  assignment_submitted: "Assignment submitted",
  assignment_parsed: "Assignment reviewed",
  journey_report_generated: "Journey report ready",
  parked_no_role: "Parked — no role matched",
  hr_stage_override: "HR override",
  pipeline_error: "Pipeline error",
};

const ACTION_TONE: Record<string, string> = {
  pipeline_error: "text-destructive",
  parked_no_role: "text-warning",
  journey_report_generated: "text-success",
  assignment_submitted: "text-success",
};

const LAST_SEEN_KEY = "hiring-agent:last-seen-notification";
const CLEARED_KEY = "hiring-agent:cleared-up-to-notification";

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [lastSeenId, setLastSeenId] = useState<number>(() => {
    if (typeof window === "undefined") return 0;
    return Number(window.localStorage.getItem(LAST_SEEN_KEY) ?? 0);
  });
  const [clearedUpTo, setClearedUpTo] = useState<number>(() => {
    if (typeof window === "undefined") return 0;
    return Number(window.localStorage.getItem(CLEARED_KEY) ?? 0);
  });
  const panelRef = useRef<HTMLDivElement>(null);

  const { data } = useSWR<Notification[]>(
    "/dashboard/v1/notifications?limit=30",
    swrFetcher,
    { refreshInterval: 8000 },
  );

  const visible = (data ?? []).filter((n) => n.id > clearedUpTo);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const unreadCount = visible.filter((n) => n.id > lastSeenId).length;

  function markAllSeen() {
    const topId = data?.[0]?.id ?? 0;
    setLastSeenId(topId);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LAST_SEEN_KEY, String(topId));
    }
  }

  function clearAll() {
    const topId = data?.[0]?.id ?? 0;
    setClearedUpTo(topId);
    setLastSeenId(topId);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(CLEARED_KEY, String(topId));
      window.localStorage.setItem(LAST_SEEN_KEY, String(topId));
    }
  }

  return (
    <div className="relative" ref={panelRef}>
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          if (!open) markAllSeen();
        }}
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-full border border-border bg-card transition hover:bg-background",
          unreadCount > 0 && "border-primary/50",
        )}
        aria-label="Notifications"
      >
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 font-mono text-[10px] leading-none text-primary-foreground">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-11 z-[60] w-[380px] max-w-[90vw] overflow-hidden rounded-lg border border-border bg-background shadow-2xl ring-1 ring-black/5">
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h3 className="font-display text-lg">Notifications</h3>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                live
              </span>
              {visible.length > 0 && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    clearAll();
                  }}
                  className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground transition hover:border-destructive/50 hover:text-destructive"
                >
                  clear
                </button>
              )}
            </div>
          </div>
          <ul className="scrollbar-slim max-h-[60vh] divide-y divide-border overflow-y-auto">
            {visible.length === 0 ? (
              <li className="p-8 text-center">
                <p className="font-display italic text-muted-foreground">
                  Nothing yet. Notifications appear when the agent moves applications.
                </p>
              </li>
            ) : (
              visible.map((n) => {
                const Component = n.application_id ? Link : "div";
                const props = n.application_id
                  ? { href: `/candidates/${n.application_id}`, onClick: () => setOpen(false) }
                  : {};
                return (
                  <li key={n.id}>
                    <Component
                      {...(props as any)}
                      className="flex gap-3 p-3 transition hover:bg-card"
                    >
                      <span
                        className={cn(
                          "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                          n.id > lastSeenId ? "bg-primary" : "bg-muted-foreground/40",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <span
                            className={cn(
                              "font-display text-sm leading-tight",
                              ACTION_TONE[n.action],
                            )}
                          >
                            {ACTION_LABEL[n.action] ?? n.action}
                          </span>
                          <time className="font-mono text-[10px] text-muted-foreground">
                            {relTime(n.created_at)}
                          </time>
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                          {n.candidate_name ?? "—"}
                          {n.role_title ? ` · ${n.role_title}` : ""}
                        </div>
                      </div>
                    </Component>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function relTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const s = Math.max(0, Math.floor((now - then) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}
