"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  DollarSign,
  Wallet,
  Clock,
  Users,
  TrendingUp,
  Activity,
  Radio,
  Flame,
  Sun,
  Eye,
  Zap,
  Target,
  Globe,
  Search,
  Mail,
  ArrowUpRight,
  BarChart3,
} from "lucide-react";
import { cn, tierColor } from "@/lib/utils";
import type { Brand } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

type DashboardData = {
  brands: Brand[];
  tierCounts: { hot: number; warm: number; watchlist: number };
  topLeads: Brand[];
  gapMap: { gap: string; count: number }[];
  signalsByType: { type: string; count: number }[];
  recentDossiers: { brand_id: number; brand_name: string; score: number; tier: string; generated_at: string }[];
  signalSources: { source: string; count: number; last_at: string }[];
  cpql: { total_cost_cents: number; qualified_count: number; cpql_cents: number } | null;
  todaySpend: { spent_cents: number; cap_cents: number };
  approvalsPending: number;
  pipelineFunnel: { stage: string; count: number }[];
  scoreDistribution: { bucket: string; count: number }[];
};

async function fetchApi<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

const SIGNAL_ICONS: Record<string, typeof Globe> = {
  web: Globe,
  search: Search,
  email: Mail,
  social: Radio,
};

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};
const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 300, damping: 24 } },
};

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboard().then((d) => { setData(d); setLoading(false); });
    const interval = setInterval(() => { loadDashboard().then(setData); }, 30_000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <main className="flex h-full items-center justify-center bg-[var(--surface)]">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center gap-3"
        >
          <div className="h-8 w-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-[var(--text-muted)]">Loading dashboard...</span>
        </motion.div>
      </main>
    );
  }

  const d = data!;
  const cpqlVal = d.cpql ? (d.cpql.cpql_cents / 100).toFixed(2) : "--";
  const budgetPct = d.todaySpend.cap_cents > 0
    ? Math.round((d.todaySpend.spent_cents / d.todaySpend.cap_cents) * 100)
    : 0;
  const maxGapCount = d.gapMap.length > 0 ? d.gapMap[0].count : 1;
  const totalLeads = d.tierCounts.hot + d.tierCounts.warm + d.tierCounts.watchlist;

  return (
    <main className="overflow-y-auto max-h-[calc(100vh-56px)] bg-[var(--surface)]">
      <div className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* Header */}
        <motion.header
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center justify-between"
        >
          <div>
            <h1 className="text-2xl font-bold text-[var(--text-primary)] tracking-tight">Dashboard</h1>
            <p className="text-sm text-[var(--text-muted)] mt-0.5">Real-time overview of discovery, scoring, and spend</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-400">
              <div className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Live
            </div>
          </div>
        </motion.header>

        {/* KPI Cards */}
        <motion.div variants={container} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
          <motion.div variants={item} className="min-w-0">
            <KpiCard
              label="CPQL"
              value={`$${cpqlVal}`}
              sub="cost per qualified lead"
              icon={DollarSign}
              iconColor="text-emerald-400"
              iconBg="bg-emerald-500/10"
            />
          </motion.div>
          <motion.div variants={item} className="min-w-0">
            <KpiCard
              label="Budget Used"
              value={`${budgetPct}%`}
              sub={`$${(d.todaySpend.spent_cents / 100).toFixed(2)} / $${(d.todaySpend.cap_cents / 100).toFixed(2)}`}
              alert={budgetPct > 80}
              icon={Wallet}
              iconColor="text-amber-400"
              iconBg="bg-amber-500/10"
              progress={budgetPct}
            />
          </motion.div>
          <motion.div variants={item} className="min-w-0">
            <KpiCard
              label="Pending Approvals"
              value={String(d.approvalsPending)}
              sub="awaiting review"
              alert={d.approvalsPending > 0}
              icon={Clock}
              iconColor="text-orange-400"
              iconBg="bg-orange-500/10"
              href="/approvals"
            />
          </motion.div>
          <motion.div variants={item} className="min-w-0">
            <KpiCard
              label="Qualified Leads"
              value={String(d.tierCounts.hot + d.tierCounts.warm)}
              sub={`${d.tierCounts.hot} hot · ${d.tierCounts.warm} warm · ${totalLeads} total`}
              icon={Users}
              iconColor="text-blue-400"
              iconBg="bg-blue-500/10"
              href="/pipeline"
            />
          </motion.div>
        </motion.div>

        {/* Pipeline Tiers */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
        >
          <SectionHeader icon={TrendingUp} label="Pipeline by Score Tier" />
          <div className="grid grid-cols-3 gap-4">
            <TierCard label="Hot" count={d.tierCounts.hot} icon={Flame} color="rose" total={totalLeads} />
            <TierCard label="Warm" count={d.tierCounts.warm} icon={Sun} color="amber" total={totalLeads} />
            <TierCard label="Watchlist" count={d.tierCounts.watchlist} icon={Eye} color="blue" total={totalLeads} />
          </div>
        </motion.section>

        {/* Pipeline Funnel + Score Distribution */}
        <div className="grid gap-4 lg:grid-cols-2">
          <motion.section
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 }}
          >
            <SectionHeader icon={BarChart3} label="Pipeline Funnel" />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] p-5">
              {d.pipelineFunnel.length === 0 ? (
                <EmptyState text="No pipeline data" />
              ) : (
                <FunnelChart data={d.pipelineFunnel} />
              )}
            </div>
          </motion.section>

          <motion.section
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 }}
          >
            <SectionHeader icon={TrendingUp} label="Score Distribution" />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] p-5">
              {d.scoreDistribution.length === 0 ? (
                <EmptyState text="No scores yet" />
              ) : (
                <DistributionChart data={d.scoreDistribution} />
              )}
            </div>
          </motion.section>
        </div>

        {/* Top Leads + Top Gaps */}
        <div className="grid gap-4 lg:grid-cols-2">
          <motion.section
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.25 }}
          >
            <SectionHeader icon={Target} label="Top Leads" count={d.topLeads.length} />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden">
              <div className="grid grid-cols-[1fr_auto_auto] gap-4 px-4 py-2.5 border-b border-[var(--border)] text-[11px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">
                <span>Brand</span>
                <span>Tier</span>
                <span className="w-12 text-right">Score</span>
              </div>
              {d.topLeads.length === 0 && <EmptyState text="No scored leads yet" />}
              {d.topLeads.map((b, i) => {
                const tc = tierColor(b.tier);
                return (
                  <motion.a
                    key={b.id}
                    href={`/brands/${b.id}`}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.3 + i * 0.03 }}
                    className="grid grid-cols-[1fr_auto_auto] gap-4 items-center px-4 py-3 hover:bg-[var(--surface-overlay)] transition-colors cursor-pointer border-b border-[var(--border)] last:border-b-0 group"
                  >
                    <div className="min-w-0">
                      <span className="text-sm font-medium text-[var(--text-primary)] truncate block group-hover:text-blue-400 transition-colors">{b.name}</span>
                      {b.diagnosis && (
                        <p className="text-[11px] text-[var(--text-muted)] truncate mt-0.5">{b.diagnosis}</p>
                      )}
                    </div>
                    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-semibold", tc.bg, tc.text)}>
                      {b.tier}
                    </span>
                    <span className="font-mono text-lg font-bold text-[var(--text-primary)] w-12 text-right">{b.score ?? "--"}</span>
                  </motion.a>
                );
              })}
            </div>
          </motion.section>

          <motion.section
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.25 }}
          >
            <SectionHeader icon={Zap} label="Top Service Gaps" count={d.gapMap.length} />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden">
              {d.gapMap.length === 0 && <EmptyState text="No competitor gaps detected yet" />}
              {d.gapMap.map((g, i) => {
                const barWidth = Math.round((g.count / maxGapCount) * 100);
                return (
                  <motion.div
                    key={g.gap}
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.3 + i * 0.03 }}
                    className="px-4 py-3.5 border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--surface-overlay)] transition-colors"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-[var(--text-primary)] capitalize">{g.gap}</span>
                      <span className="rounded-full bg-purple-500/10 text-purple-400 px-2.5 py-0.5 text-[11px] font-semibold tabular-nums">
                        {g.count} {g.count === 1 ? "lead" : "leads"}
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${barWidth}%` }}
                        transition={{ delay: 0.4 + i * 0.03, duration: 0.5 }}
                        className="h-full rounded-full bg-gradient-to-r from-purple-500 to-violet-500"
                      />
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </motion.section>
        </div>

        {/* Signal Activity + Signal Sources */}
        <div className="grid gap-4 lg:grid-cols-2">
          <motion.section
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 }}
          >
            <SectionHeader icon={Activity} label="Signal Activity" />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden">
              {d.signalsByType.length === 0 && <EmptyState text="No signals captured yet" />}
              {d.signalsByType.map((s, i) => (
                <motion.div
                  key={s.type}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.35 + i * 0.03 }}
                  className="flex items-center justify-between px-4 py-3.5 border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--surface-overlay)] transition-colors"
                >
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-500/10">
                      <Activity size={13} className="text-blue-400" />
                    </div>
                    <span className="text-sm font-medium text-[var(--text-primary)] capitalize">{s.type.replace(/_/g, " ")}</span>
                  </div>
                  <span className="font-mono text-base font-bold text-[var(--text-primary)] tabular-nums">{s.count}</span>
                </motion.div>
              ))}
            </div>
          </motion.section>

          <motion.section
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 }}
          >
            <SectionHeader icon={Radio} label="Signal Sources" />
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden">
              {d.signalSources.map((src, i) => {
                const SrcIcon = SIGNAL_ICONS[src.source.toLowerCase()] || Radio;
                return (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.35 + i * 0.03 }}
                    className="flex items-center justify-between px-4 py-3.5 border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--surface-overlay)] transition-colors"
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--surface-overlay)]">
                        <SrcIcon size={13} className="text-[var(--text-secondary)]" />
                      </div>
                      <span className="text-sm font-medium text-[var(--text-primary)]">{src.source}</span>
                    </div>
                    <div className="flex items-center gap-3 text-right">
                      <span className="font-mono text-base font-bold text-[var(--text-primary)] tabular-nums">{src.count}</span>
                      <span className="text-[11px] text-[var(--text-muted)] min-w-[50px] text-right">{timeAgo(src.last_at)}</span>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </motion.section>
        </div>

        {/* Recent Dossiers */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35 }}
          className="pb-6"
        >
          <SectionHeader icon={Activity} label="Recent Dossiers" count={d.recentDossiers.length} />
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden">
            {d.recentDossiers.length === 0 && <EmptyState text="No dossiers yet" />}
            {d.recentDossiers.map((ds, i) => {
              const tc = tierColor(ds.tier);
              return (
                <motion.a
                  key={i}
                  href={`/brands/${ds.brand_id}`}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.4 + i * 0.05 }}
                  className="flex items-center justify-between px-4 py-3.5 border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--surface-overlay)] transition-colors cursor-pointer group"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-[var(--text-primary)] group-hover:text-blue-400 transition-colors">{ds.brand_name || `Brand #${ds.brand_id}`}</span>
                    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-semibold", tc.bg, tc.text)}>
                      {ds.tier}
                    </span>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="font-mono text-base font-bold text-[var(--text-primary)] tabular-nums">{ds.score}</span>
                    <span className="text-[11px] text-[var(--text-muted)] min-w-[50px] text-right">{timeAgo(ds.generated_at)}</span>
                    <ArrowUpRight size={14} className="text-[var(--text-subtle)] group-hover:text-blue-400 transition-colors" />
                  </div>
                </motion.a>
              );
            })}
          </div>
        </motion.section>
      </div>
    </main>
  );
}

function SectionHeader({ icon: Icon, label, count }: { icon: React.ComponentType<{ size?: number; className?: string }>; label: string; count?: number }) {
  return (
    <h2 className="mb-3 flex items-center gap-2 text-xs uppercase tracking-widest text-[var(--text-muted)] font-semibold">
      <Icon size={13} />
      {label}
      {count != null && count > 0 && (
        <span className="text-[var(--text-subtle)] font-mono">{count}</span>
      )}
    </h2>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="text-sm text-[var(--text-subtle)] p-6 text-center">{text}</p>;
}

function KpiCard({
  label,
  value,
  sub,
  alert,
  icon: Icon,
  iconColor,
  iconBg,
  progress,
  href,
}: {
  label: string;
  value: string;
  sub: string;
  alert?: boolean;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  iconColor: string;
  iconBg: string;
  progress?: number;
  href?: string;
}) {
  const Wrapper = href ? "a" : "div";
  return (
    <Wrapper
      {...(href ? { href } : {})}
      className={cn(
        "rounded-2xl border p-4 transition-all group min-w-0 overflow-hidden",
        alert
          ? "border-amber-700/40 bg-amber-950/15 hover:border-amber-700/60"
          : "border-[var(--border)] bg-[var(--surface-elevated)] hover:border-[var(--border-strong)] hover:shadow-sm",
        href && "cursor-pointer"
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <div className={cn("flex h-9 w-9 items-center justify-center rounded-xl shrink-0", iconBg)}>
          <Icon size={16} className={iconColor} />
        </div>
        {href && (
          <ArrowUpRight size={14} className="text-[var(--text-subtle)] group-hover:text-[var(--text-secondary)] transition-colors shrink-0" />
        )}
      </div>
      <div className={cn("text-2xl font-bold tabular-nums tracking-tight truncate", alert ? "text-amber-400" : "text-[var(--text-primary)]")}>{value}</div>
      <div className="text-xs text-[var(--text-muted)] mt-1 font-medium truncate">{label}</div>
      <div className="text-[11px] text-[var(--text-subtle)] mt-0.5 truncate">{sub}</div>
      {progress != null && (
        <div className="mt-3 h-2 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${Math.min(progress, 100)}%` }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className={cn("h-full rounded-full transition-all", progress > 80 ? "bg-gradient-to-r from-amber-500 to-orange-500" : "bg-gradient-to-r from-blue-500 to-indigo-500")}
          />
        </div>
      )}
    </Wrapper>
  );
}

function TierCard({ label, count, icon: Icon, color, total }: { label: string; count: number; icon: React.ComponentType<{ size?: number; className?: string }>; color: "rose" | "amber" | "blue"; total: number }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  const styles = {
    rose: { border: "border-rose-500/20", bg: "bg-rose-500/[0.06]", text: "text-rose-400", bar: "bg-gradient-to-r from-rose-500 to-pink-500", hover: "hover:border-rose-500/30 hover:bg-rose-500/10" },
    amber: { border: "border-amber-500/20", bg: "bg-amber-500/[0.06]", text: "text-amber-400", bar: "bg-gradient-to-r from-amber-500 to-orange-500", hover: "hover:border-amber-500/30 hover:bg-amber-500/10" },
    blue: { border: "border-blue-500/20", bg: "bg-blue-500/[0.06]", text: "text-blue-400", bar: "bg-gradient-to-r from-blue-500 to-indigo-500", hover: "hover:border-blue-500/30 hover:bg-blue-500/10" },
  };
  const s = styles[color];
  return (
    <a href="/pipeline" className={cn("rounded-2xl border p-5 transition-all cursor-pointer", s.border, s.bg, s.hover)}>
      <div className="flex items-center justify-between mb-3">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl", `${color === "rose" ? "bg-rose-500/15" : color === "amber" ? "bg-amber-500/15" : "bg-blue-500/15"}`)}>
          <Icon size={18} className={s.text} />
        </div>
        <span className={cn("text-xs font-semibold tabular-nums", s.text)}>{pct}%</span>
      </div>
      <div className={cn("text-3xl font-bold tabular-nums", s.text)}>{count}</div>
      <div className="text-xs text-[var(--text-muted)] mt-1 font-medium">{label}</div>
      <div className="mt-3 h-1.5 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay: 0.3 }}
          className={cn("h-full rounded-full", s.bar)}
        />
      </div>
    </a>
  );
}

const FUNNEL_COLORS = [
  "from-blue-500 to-blue-400",
  "from-indigo-500 to-indigo-400",
  "from-violet-500 to-violet-400",
  "from-purple-500 to-purple-400",
  "from-fuchsia-500 to-fuchsia-400",
  "from-pink-500 to-pink-400",
  "from-rose-500 to-rose-400",
  "from-emerald-500 to-emerald-400",
];

function FunnelChart({ data }: { data: { stage: string; count: number }[] }) {
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="space-y-3">
      {data.map((d, i) => {
        const pct = Math.max((d.count / max) * 100, 8);
        return (
          <div key={d.stage} className="flex items-center gap-3">
            <span className="w-20 text-xs font-medium text-[var(--text-secondary)] capitalize truncate">{d.stage}</span>
            <div className="flex-1 h-8 rounded-lg bg-[var(--surface-sunken)] overflow-hidden relative">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ delay: 0.3 + i * 0.05, duration: 0.5 }}
                className={cn("h-full rounded-lg bg-gradient-to-r flex items-center justify-end pr-3", FUNNEL_COLORS[i % FUNNEL_COLORS.length])}
              >
                <span className="text-[11px] font-bold text-white drop-shadow-sm">{d.count}</span>
              </motion.div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DistributionChart({ data }: { data: { bucket: string; count: number }[] }) {
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="flex items-end gap-3 h-36">
      {data.map((d, i) => {
        const pct = Math.max((d.count / max) * 100, 6);
        const color =
          d.bucket === "80-100" ? "from-emerald-500 to-emerald-400" :
          d.bucket === "60-79" ? "from-blue-500 to-blue-400" :
          d.bucket === "40-59" ? "from-amber-500 to-amber-400" :
          d.bucket === "20-39" ? "from-orange-500 to-orange-400" :
          "from-rose-500 to-rose-400";
        return (
          <div key={d.bucket} className="flex-1 flex flex-col items-center gap-1.5">
            <span className="text-[11px] font-mono font-bold text-[var(--text-secondary)] tabular-nums">{d.count}</span>
            <div className="w-full rounded-lg overflow-hidden bg-[var(--surface-sunken)] relative" style={{ height: "100%" }}>
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: `${pct}%` }}
                transition={{ delay: 0.3 + i * 0.05, duration: 0.5 }}
                className={cn("absolute bottom-0 w-full rounded-lg bg-gradient-to-t", color)}
              />
            </div>
            <span className="text-[10px] font-medium text-[var(--text-muted)]">{d.bucket}</span>
          </div>
        );
      })}
    </div>
  );
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

async function loadDashboard(): Promise<DashboardData> {
  const [brandsRes, signals, approvals, analytics, budget] = await Promise.all([
    fetchApi<{ items: Brand[] }>("/brands?limit=200"),
    fetchApi<{ items: { type: string; source: string; observed_at: string }[] }>("/signals?limit=500"),
    fetchApi<{ items: unknown[] }>("/approvals?status=pending"),
    fetchApi<{ cpql_cents: number; total_cost_cents: number; qualified_count: number }>("/analytics/cpql"),
    fetchApi<{ spent_cents: number; cap_cents: number }>("/analytics/budget"),
  ]);

  const brands = brandsRes?.items ?? [];

  const tierCounts = { hot: 0, warm: 0, watchlist: 0 };
  for (const b of brands) {
    const t = (b.tier ?? "").toLowerCase();
    if (t === "hot") tierCounts.hot++;
    else if (t === "warm") tierCounts.warm++;
    else if (t === "watchlist" || t === "park") tierCounts.watchlist++;
  }

  const topLeads = brands
    .filter((b) => b.score != null && b.score > 0)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 8);

  const gapCounts = new Map<string, number>();
  for (const b of brands) {
    if (!b.gap_summary) continue;
    for (const gap of b.gap_summary.split(", ")) {
      const g = gap.trim().toLowerCase();
      if (g) gapCounts.set(g, (gapCounts.get(g) ?? 0) + 1);
    }
  }
  const gapMap = Array.from(gapCounts.entries())
    .map(([gap, count]) => ({ gap, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);

  const typeCounts = new Map<string, number>();
  const sourceMap = new Map<string, { count: number; last_at: string }>();
  for (const sig of (signals?.items ?? [])) {
    typeCounts.set(sig.type, (typeCounts.get(sig.type) ?? 0) + 1);
    const existing = sourceMap.get(sig.source);
    if (existing) {
      existing.count++;
      if (sig.observed_at > existing.last_at) existing.last_at = sig.observed_at;
    } else {
      sourceMap.set(sig.source, { count: 1, last_at: sig.observed_at });
    }
  }
  const signalsByType = Array.from(typeCounts.entries())
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count);

  const recentDossiers: DashboardData["recentDossiers"] = brands
    .filter((b) => b.score != null && b.tier)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 5)
    .map((b) => ({
      brand_id: b.id,
      brand_name: b.name,
      score: b.score ?? 0,
      tier: b.tier ?? "unknown",
      generated_at: b.created_at,
    }));

  const stageCounts = new Map<string, number>();
  const STAGE_ORDER = ["new", "qualified", "contacted", "responded", "meeting", "proposal", "won", "lost"];
  for (const b of brands) {
    const s = (b.status ?? "new").toLowerCase();
    stageCounts.set(s, (stageCounts.get(s) ?? 0) + 1);
  }
  const pipelineFunnel = STAGE_ORDER
    .filter((s) => stageCounts.has(s))
    .map((s) => ({ stage: s, count: stageCounts.get(s)! }));

  const scoreBuckets = { "0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0 };
  for (const b of brands) {
    const s = b.score ?? 0;
    if (s >= 80) scoreBuckets["80-100"]++;
    else if (s >= 60) scoreBuckets["60-79"]++;
    else if (s >= 40) scoreBuckets["40-59"]++;
    else if (s >= 20) scoreBuckets["20-39"]++;
    else if (s > 0) scoreBuckets["0-19"]++;
  }
  const scoreDistribution = Object.entries(scoreBuckets).map(([bucket, count]) => ({ bucket, count }));

  return {
    brands,
    tierCounts,
    topLeads,
    gapMap,
    signalsByType,
    recentDossiers,
    signalSources: Array.from(sourceMap.entries()).map(([source, d]) => ({ source, ...d })),
    cpql: analytics ?? null,
    todaySpend: budget ?? { spent_cents: 0, cap_cents: 500 },
    approvalsPending: approvals?.items?.length ?? 0,
    pipelineFunnel,
    scoreDistribution,
  };
}
