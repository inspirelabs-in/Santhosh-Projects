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
  PieChart,
  Pie,
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
  "intake", "screening", "voice_screen",
  "assignment", "tech_interview", "ceo_interview", "hr_review", "hired",
];

const COLORS = [
  "hsl(85 100% 33%)",    // primary green
  "hsl(208 86% 54%)",    // bright blue
  "hsl(33 87% 59%)",     // warning
  "hsl(64 73% 53%)",     // accent
  "hsl(216 45% 45%)",    // blue dark
  "hsl(0 78% 57%)",      // destructive
  "hsl(218 25% 35%)",    // muted fg
  "hsl(85 70% 50%)",     // light green
];

export default function AnalyticsPage() {
  const { data, isLoading } = useSWR<Stats>(
    "/dashboard/v1/analytics/overview",
    swrFetcher,
    { refreshInterval: 30000 },
  );

  const funnelData = STAGE_ORDER.map((s) => ({
    stage: s.replace(/_/g, " "),
    count: data?.stage_counts?.[s] ?? 0,
  }));

  const sourceData = Object.entries(data?.source_counts ?? {}).map(([name, value]) => ({
    name,
    value,
  }));

  const trendData = Object.entries(data?.recent_per_day ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({
      date: new Date(date).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" }),
      count,
    }));

  return (
    <>
      <Topbar title="Analytics" subtitle="Pipeline health at a glance" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6 page-enter">
          {isLoading ? (
            <SkeletonLines lines={8} />
          ) : !data ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
              <p className="text-sm">No analytics data available yet.</p>
            </div>
          ) : (
            <>
              {/* KPI cards */}
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <KpiCard label="Total candidates" value={data?.total_candidates ?? 0} />
                <KpiCard label="Active roles" value={data?.active_roles ?? 0} />
                <KpiCard label="Total roles" value={data?.total_roles ?? 0} />
                <KpiCard
                  label="Hired"
                  value={data?.stage_counts?.["hired"] ?? 0}
                  accent
                />
              </div>

              {/* Pipeline funnel */}
              <div className="mt-8 rounded-xl border border-border bg-card p-5 shadow-card">
                <h2 className="text-base font-bold tracking-tight">Pipeline funnel</h2>
                <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                  Candidates at each stage
                </p>
                <div className="mt-4 h-64">
                  <ResponsiveContainer width="100%" height="100%" minWidth={200} minHeight={200}>
                    <BarChart data={funnelData} margin={{ left: 0, right: 16 }}>
                      <XAxis
                        dataKey="stage"
                        tick={{ fontSize: 10, fill: "hsl(218 25% 35%)" }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: "hsl(218 25% 35%)" }}
                        tickLine={false}
                        axisLine={false}
                        allowDecimals={false}
                      />
                      <Tooltip
                        contentStyle={{
                          borderRadius: 8,
                          border: "1px solid hsl(218 18% 88%)",
                          fontSize: 12,
                        }}
                      />
                      <Bar dataKey="count" fill="hsl(85 100% 33%)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
                {/* Source mix */}
                {sourceData.length > 0 && (
                  <div className="rounded-xl border border-border bg-card p-5 shadow-card">
                    <h2 className="text-base font-bold tracking-tight">Source mix</h2>
                    <div className="mt-4 h-52">
                      <ResponsiveContainer width="100%" height="100%" minWidth={200} minHeight={180}>
                        <PieChart>
                          <Pie
                            data={sourceData}
                            dataKey="value"
                            nameKey="name"
                            cx="50%"
                            cy="50%"
                            outerRadius={80}
                            label={(props: any) =>
                              `${props.name ?? ""} ${((props.percent ?? 0) * 100).toFixed(0)}%`
                            }
                            labelLine={false}
                          >
                            {sourceData.map((_, i) => (
                              <Cell key={i} fill={COLORS[i % COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}

                {/* Daily trend */}
                {trendData.length > 0 && (
                  <div className="rounded-xl border border-border bg-card p-5 shadow-card">
                    <h2 className="text-base font-bold tracking-tight">Daily applications</h2>
                    <div className="mt-4 h-52">
                      <ResponsiveContainer width="100%" height="100%" minWidth={200} minHeight={180}>
                        <AreaChart data={trendData}>
                          <XAxis
                            dataKey="date"
                            tick={{ fontSize: 10, fill: "hsl(218 25% 35%)" }}
                            tickLine={false}
                            axisLine={false}
                          />
                          <YAxis
                            tick={{ fontSize: 10, fill: "hsl(218 25% 35%)" }}
                            tickLine={false}
                            axisLine={false}
                            allowDecimals={false}
                          />
                          <Tooltip />
                          <Area
                            type="monotone"
                            dataKey="count"
                            fill="hsl(208 86% 54% / 0.15)"
                            stroke="hsl(208 86% 54%)"
                            strokeWidth={2}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function KpiCard({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="card-lift rounded-xl border border-border bg-card p-4 shadow-card">
      <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1 font-data text-3xl font-bold tabular-nums ${accent ? "text-primary" : ""}`}>
        {value.toLocaleString()}
      </div>
    </div>
  );
}
