"use client";

import { useMemo } from "react";
import Link from "next/link";
import { STAGE_LABELS, STAGE_ORDER, type Stage } from "@/components/status-tag";
import { Avatar } from "@/components/ui/avatar";
import { LazyConfidenceBadge } from "@/components/lazy-confidence-badge";
import { cn } from "@/lib/utils";

interface Candidate {
  application_id: string;
  candidate_id: string;
  name: string | null;
  email: string | null;
  role_title: string | null;
  current_stage: Stage;
  screening_score: number | null;
  updated_at: string;
}

const KANBAN_STAGES: Stage[] = [
  ...STAGE_ORDER,
  "needs_hr_review" as Stage,
  "hired" as Stage,
  "rejected" as Stage,
];

const COL_COLORS: Record<string, string> = {
  intake: "border-t-blue-400",
  parse: "border-t-sky-400",
  fit_score: "border-t-cyan-400",
  screening: "border-t-teal-400",
  voice_screen: "border-t-emerald-400",
  assignment: "border-t-yellow-400",
  tech_interview: "border-t-orange-400",
  ceo_interview: "border-t-purple-400",
  offer: "border-t-primary",
  hired: "border-t-green-500",
  rejected: "border-t-destructive",
  needs_hr_review: "border-t-warning",
};

export function KanbanView({
  data,
  onQuickView,
}: {
  data: Candidate[];
  onQuickView?: (id: string) => void;
}) {
  const columns = useMemo(() => {
    const map = new Map<string, Candidate[]>();
    for (const s of KANBAN_STAGES) map.set(s, []);
    for (const c of data) {
      const list = map.get(c.current_stage);
      if (list) list.push(c);
      else {
        if (!map.has(c.current_stage)) map.set(c.current_stage, []);
        map.get(c.current_stage)!.push(c);
      }
    }
    return Array.from(map.entries()).filter(([, items]) => items.length > 0);
  }, [data]);

  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {columns.map(([stage, items]) => (
        <div
          key={stage}
          className={cn(
            "flex w-64 shrink-0 flex-col rounded-lg border border-border border-t-2 bg-muted/30",
            COL_COLORS[stage] ?? "border-t-border",
          )}
        >
          <div className="flex items-center justify-between px-3 py-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              {STAGE_LABELS[stage as Stage] ?? stage.replace(/_/g, " ")}
            </span>
            <span className="font-data text-[11px] font-bold tabular-nums text-muted-foreground">
              {items.length}
            </span>
          </div>
          <div className="scrollbar-slim flex-1 space-y-1.5 overflow-y-auto px-2 pb-2" style={{ maxHeight: "calc(100vh - 280px)" }}>
            {items.map((c) => (
              <button
                key={c.application_id}
                type="button"
                onClick={() => onQuickView?.(c.application_id)}
                className="w-full rounded-md border border-border bg-card p-2.5 text-left shadow-sm transition hover:shadow-card"
              >
                <div className="flex items-center gap-2">
                  <Avatar name={c.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-semibold">
                        {c.name ?? c.email ?? "Unnamed"}
                      </span>
                      <LazyConfidenceBadge applicationId={c.application_id} />
                    </div>
                    <div className="truncate text-[11px] text-muted-foreground">
                      {c.role_title ?? "No role"}
                    </div>
                  </div>
                </div>
                {c.screening_score != null && (
                  <div className="mt-1.5 flex items-center gap-1">
                    <div className="h-1 flex-1 rounded-full bg-border">
                      <div
                        className="h-1 rounded-full bg-primary"
                        style={{ width: `${Math.min(c.screening_score * 10, 100)}%` }}
                      />
                    </div>
                    <span className="font-data text-[10px] text-muted-foreground">
                      {c.screening_score}
                    </span>
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
