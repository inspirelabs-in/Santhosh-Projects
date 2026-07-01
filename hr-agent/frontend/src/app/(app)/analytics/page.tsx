"use client";

import useSWR from "swr";
import { Topbar } from "@/components/layout/topbar";
import { swrFetcher } from "@/lib/api";
import { SkeletonLines } from "@/components/skeleton";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  AreaChart,
  Area,
} from "recharts";

interface Stats {
  total_candidates: number;
  total_roles: number;
  active_roles: number;
  stage_counts: Record<string, number>;
  recent_per_day?: Record<string, number>;
  source_counts?: Record<string, number>;
}

const STAGE_ORDER = [
  "intake",
  "screening",
  "voice_screen",
  "assignment",
  "tech_interview",
  "ceo_interview",
  "hr_review",
  "hired",
  "rejected",
];

const STAGE_LABEL: Record<string, string> = {
  intake:        "Intake",
  screening:     "Screening",
  voice_screen:  "Voice",
  assignment:    "Assignment",
  tech_interview:"Technical",
  ceo_interview: "CEO",
  hr_review:     "HR",
  hired:         "Hired",
  rejected:      "Rejected",
};

function barColor(stageKey: string): string {
  if (stageKey === "rejected") return "hsl(0 78% 57%)";
  if (stageKey === "hired")    return "hsl(85 100% 33%)";
  return "hsl(208 86% 54%)";
}

export default function AnalyticsPage() {
  const { data, isLoading } = useSWR<Stats>(
    "/dashboard/v1/analytics/overview",
    swrFetcher,
    { refreshInterval: 30000 },
  );

  const funnelData = STAGE_ORDER.map((key) => ({
    key,
    stage: STAGE_LABEL[key] ?? key,
    count: data?.stage_counts?.[key] ?? 0,
  }));

  const trendData = Object.entries(data?.recent_per_day ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({
      date: new Date(date).toLocaleDateString("en-IN", {
        timeZone: "Asia/Kolkata",
        month: "short",
        day: "numeric",
      }),
      count,
    }));

  const rejected = data?.stage_counts?.["rejected"] ?? 0;
  const hired    = data?.stage_counts?.["hired"]    ?? 0;

  return (
    <>
      <Topbar title="Analytics" subtitle="Pipeline health at a glance" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6 pb-24 page-enter">
          {isLoading ? (
            <SkeletonLines lines={8} />
          ) : !data ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
              <p className="text-sm">No analytics data available yet.</p>
            </div>
          ) : (
            <>
              {/* KPI cards */}
              <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                <KpiCard label="Total candidates" value={data.total_candidates} />
                <KpiCard label="Active roles"     value={data.active_roles} />
                <KpiCard label="Total roles"      value={data.total_roles} />
                <KpiCard label="Hired"            value={hired}    accent="green" />
                <KpiCard label="Rejected"         value={rejected} accent="red" />
              </div>

              {/* Pipeline funnel */}
              <div className="mt-6 rounded-xl border border-border bg-card p-5 shadow-card">
                <h2 className="text-[15px] font-bold tracking-tight">Pipeline funnel</h2>
                <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                  Candidates at each stage — including rejections
                </p>
                <div className="mt-5 h-64">
                  <ResponsiveContainer width="100%" height="100%" debounce={1}>
                    <BarChart data={funnelData} margin={{ left: 0, right: 8, bottom: 0 }}>
                      <XAxis
                        dataKey="stage"
                        tick={{ fontSize: 10, fill: "hsl(218 25% 45%)" }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: "hsl(218 25% 45%)" }}
                        tickLine={false}
                        axisLine={false}
                        allowDecimals={false}
                        width={28}
                      />
                      <Tooltip
                        contentStyle={{
                          borderRadius: 8,
                          border: "1px solid hsl(218 18% 88%)",
                          fontSize: 12,
                        }}
                        cursor={{ fill: "hsl(218 18% 96%)" }}
                      />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                        {funnelData.map((entry) => (
                          <Cell key={entry.key} fill={barColor(entry.key)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-3 flex items-center gap-4 font-mono text-[10px] text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-blue-500" /> Active stages
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" /> Hired
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-destructive" /> Rejected
                  </span>
                </div>
              </div>

              {/* Daily applications trend */}
              {trendData.length > 0 && (
                <div className="mt-6 rounded-xl border border-border bg-card p-5 shadow-card">
                  <h2 className="text-[15px] font-bold tracking-tight">Daily applications</h2>
                  <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                    New candidates per day
                  </p>
                  <div className="mt-5 h-52">
                    <ResponsiveContainer width="100%" height="100%" debounce={1}>
                      <AreaChart data={trendData}>
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 10, fill: "hsl(218 25% 45%)" }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: "hsl(218 25% 45%)" }}
                          tickLine={false}
                          axisLine={false}
                          allowDecimals={false}
                          width={28}
                        />
                        <Tooltip
                          contentStyle={{
                            borderRadius: 8,
                            border: "1px solid hsl(218 18% 88%)",
                            fontSize: 12,
                          }}
                        />
                        <Area
                          type="monotone"
                          dataKey="count"
                          fill="hsl(208 86% 54% / 0.12)"
                          stroke="hsl(208 86% 54%)"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function KpiCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "green" | "red";
}) {
  const valClass =
    accent === "green" ? "text-emerald-600" :
    accent === "red"   ? "text-destructive"  :
    "text-foreground";

  return (
    <div className="rounded-xl border border-border bg-card px-4 py-4 shadow-card">
      <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1.5 font-data text-3xl font-bold tabular-nums leading-none ${valClass}`}>
        {value.toLocaleString()}
      </div>
    </div>
  );
}
