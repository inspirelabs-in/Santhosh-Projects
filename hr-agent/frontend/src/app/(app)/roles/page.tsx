"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import {
  Banknote,
  Briefcase,
  Calendar,
  ChevronRight,
  MapPin,
  Plus,
  Search,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { swrFetcher } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Role } from "@/lib/types";

/* ── Status pill ─────────────────────────────────────────────────────── */

const STATUS_STYLE: Record<string, { bg: string; text: string; dot: string }> = {
  open:      { bg: "bg-emerald-50",  text: "text-emerald-700",  dot: "bg-emerald-500" },
  paused:    { bg: "bg-amber-50",    text: "text-amber-700",    dot: "bg-amber-400" },
  draft:     { bg: "bg-blue-50",     text: "text-blue-700",     dot: "bg-blue-400" },
  filled:    { bg: "bg-slate-100",   text: "text-slate-500",    dot: "bg-slate-400" },
  cancelled: { bg: "bg-red-50",      text: "text-red-600",      dot: "bg-red-400" },
};

function StatusPill({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? { bg: "bg-muted", text: "text-muted-foreground", dot: "bg-muted-foreground" };
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold", s.bg, s.text)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
      {status}
    </span>
  );
}

/* ── Candidate stage mini-strip ──────────────────────────────────────── */

const STAGE_COLOR: Record<string, string> = {
  rejected:        "bg-red-400",
  needs_hr_review: "bg-amber-400",
  hired:           "bg-emerald-600",
  offer:           "bg-emerald-400",
  assignment_sent: "bg-blue-400",
  voice_screen:    "bg-violet-400",
  voice_screen_scheduled: "bg-violet-400",
  tech_interview:  "bg-indigo-400",
  screening:       "bg-sky-400",
  intake:          "bg-slate-300",
};

const STAGE_LABEL: Record<string, string> = {
  rejected:        "Rejected",
  needs_hr_review: "HR Review",
  hired:           "Hired",
  offer:           "Offer",
  assignment_sent: "Assignment",
  voice_screen:    "Voice",
  voice_screen_scheduled: "Voice",
  tech_interview:  "Interview",
  screening:       "Screening",
  intake:          "Intake",
};

interface CandidateRow {
  application_id: string;
  role_title: string | null;
  current_stage: string;
  fit_score: number | null;
  fit_tier: string | null;
}

interface RoleStats {
  total: number;
  active: number;
  rejected: number;
  stages: Record<string, number>;
  topStages: Array<{ stage: string; count: number }>;
}

function buildStatsMap(candidates: CandidateRow[]): Map<string, RoleStats> {
  const map = new Map<string, RoleStats>();
  for (const c of candidates) {
    const key = c.role_title ?? "__unknown__";
    if (!map.has(key)) map.set(key, { total: 0, active: 0, rejected: 0, stages: {}, topStages: [] });
    const s = map.get(key)!;
    s.total++;
    if (c.current_stage === "rejected") s.rejected++;
    else s.active++;
    s.stages[c.current_stage] = (s.stages[c.current_stage] ?? 0) + 1;
  }
  // Compute topStages after building
  for (const [, s] of map) {
    s.topStages = Object.entries(s.stages)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([stage, count]) => ({ stage, count }));
  }
  return map;
}

export default function RolesListPage() {
  const { data: roles, isLoading, mutate } = useSWR<Role[]>(
    "/dashboard/roles",
    swrFetcher,
    { refreshInterval: 30_000 },
  );
  const { data: candidateData } = useSWR<{ items: CandidateRow[]; total: number }>(
    "/dashboard/v1/candidates?limit=500",
    swrFetcher,
  );

  const [q, setQ] = useState("");

  const statsMap = buildStatsMap(candidateData?.items ?? []);

  const filtered = q.trim()
    ? (roles ?? []).filter((r) => r.title.toLowerCase().includes(q.toLowerCase()))
    : (roles ?? []);

  return (
    <>
      <Topbar title="Roles" subtitle="Job descriptions, scoring rubrics, and interviewer panels" />

      <div className="flex-1 overflow-auto px-8 py-6 pb-24">
        {/* ── Toolbar ── */}
        <div className="mb-6 flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search roles…"
              className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary/60 focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Link href="/roles/new">
              <Button size="sm">
                <Plus className="mr-1.5 h-3.5 w-3.5" /> New role
              </Button>
            </Link>
          </div>
        </div>

        {/* ── Cards ── */}
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-28 animate-pulse rounded-2xl border bg-muted/30" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-2xl border border-dashed py-20 text-center">
            <Briefcase className="mx-auto mb-3 h-8 w-8 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">
              {q ? "No roles match your search." : "No roles yet. Create one to start accepting applications."}
            </p>
            {!q && (
              <Link href="/roles/new" className="mt-3 inline-block">
                <Button size="sm" variant="outline"><Plus className="mr-1.5 h-3.5 w-3.5" /> Create role</Button>
              </Link>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {filtered.map((r) => {
              const stats = statsMap.get(r.title);
              return (
                <Link key={r.id} href={`/roles/${r.id}`} className="group block">
                  <div className="rounded-2xl border border-border/60 bg-card px-5 py-4 shadow-sm transition-all hover:border-primary/30 hover:shadow-md hover:-translate-y-px">
                    {/* Top row: title + status + candidate count */}
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2.5">
                          <span className="truncate text-[15px] font-bold text-foreground transition-colors group-hover:text-primary">
                            {r.title}
                          </span>
                          <StatusPill status={r.status} />
                        </div>

                        {/* Meta chips row */}
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          {r.location && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">
                              <MapPin className="h-2.5 w-2.5 shrink-0" />{r.location}
                            </span>
                          )}
                          {r.remote_policy && (
                            <span className="rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[11px] capitalize text-muted-foreground">
                              {r.remote_policy}
                            </span>
                          )}
                          {(r.ctc_min_lpa != null || r.ctc_max_lpa != null) && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">
                              <Banknote className="h-2.5 w-2.5 shrink-0" />
                              <span className="font-semibold text-foreground">{r.ctc_min_lpa ?? "?"} – {r.ctc_max_lpa ?? "?"}</span> LPA
                            </span>
                          )}
                          {r.max_notice_days != null && (
                            <span className="rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">
                              <span className="font-semibold text-foreground">{r.max_notice_days}d</span> notice
                            </span>
                          )}
                          <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">
                            <Calendar className="h-2.5 w-2.5 shrink-0" />{fmtDate(r.created_at)}
                          </span>
                        </div>
                      </div>

                      {/* Right side: candidate count + arrow */}
                      <div className="flex shrink-0 items-start gap-3">
                        {stats && stats.total > 0 ? (
                          <div className="text-right">
                            <div className="flex items-center justify-end gap-1.5">
                              <span className="text-2xl font-bold tabular-nums text-foreground">{stats.total}</span>
                              <span className="text-xs text-muted-foreground">applicants</span>
                            </div>
                            <div className="mt-1 flex items-center justify-end gap-1">
                              {stats.active > 0 && (
                                <span className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                                  {stats.active} active
                                </span>
                              )}
                              {stats.rejected > 0 && (
                                <span className="rounded-md border border-red-200 bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-600">
                                  {stats.rejected} out
                                </span>
                              )}
                            </div>
                          </div>
                        ) : (
                          <span className="rounded-md border border-dashed border-border px-2.5 py-1 text-[11px] text-muted-foreground/60">
                            No applicants yet
                          </span>
                        )}
                        <ChevronRight className="mt-1 h-4 w-4 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
                      </div>
                    </div>

                    {/* Stage breakdown strip — horizontal scroll, never wraps, card height stays fixed */}
                    {stats && stats.topStages.length > 0 && (
                      <div className="mt-3 flex items-center gap-2.5 border-t border-border/40 pt-3">
                        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/50">Pipeline</span>
                        <div className="flex gap-1.5 overflow-x-auto scrollbar-none">
                          {stats.topStages.map(({ stage, count }) => (
                            <span key={stage} className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/60 bg-background px-2.5 py-0.5 text-[11px]">
                              <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", STAGE_COLOR[stage] ?? "bg-slate-400")} />
                              <span className="text-muted-foreground">{STAGE_LABEL[stage] ?? stage.replace(/_/g, " ")}</span>
                              <span className="font-bold tabular-nums text-foreground">{count}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
