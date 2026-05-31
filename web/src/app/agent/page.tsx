"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Circle,
  Clock,
  Database,
  Eye,
  Loader2,
  Radio,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  Terminal,
  XCircle,
  Zap,
  Cpu,
  Wifi,
  ArrowRight,
  TrendingUp,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BrandPanel } from "@/components/brand-panel";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

type ErrorSummary = {
  health: "healthy" | "degraded" | "unhealthy";
  last_hour_total: number;
  by_category: Record<string, number>;
  latest: ErrorEntry[];
};

type ErrorEntry = {
  ts: number;
  category: string;
  source: string;
  message: string;
  detail: string;
  brand_id: number | null;
};

type AgentStatus = {
  last_discovery: { workflow_id: string; created_at: string; duration_ms: number; status: string; error: string | null } | null;
  last_monitoring: { workflow_id: string; created_at: string; duration_ms: number; status: string; error: string | null } | null;
  collectors: { collector: string; total_runs: number; total_signals: number; last_run: string | null; errors: number }[];
  brands_pending_research: number;
  recent_discoveries: { id: number; name: string; domain: string; created_at: string; tier: string | null; score: number | null }[];
  error_summary: ErrorSummary | null;
};

type Trace = {
  id: string;
  workflow_id: string;
  brand_id: number | null;
  agent: string;
  total_cost_cents: number;
  duration_ms: number | null;
  status: string;
  error: string | null;
  created_at: string;
  brand_name: string | null;
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

export default function AgentPage() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [prevTraceIds, setPrevTraceIds] = useState<Set<string>>(new Set());
  const [newTraceIds, setNewTraceIds] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<ErrorEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedBrand, setSelectedBrand] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [prevDiscoveryIds, setPrevDiscoveryIds] = useState<Set<number>>(new Set());
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set());
  const logRef = useRef<HTMLDivElement>(null);
  const closePanel = useCallback(() => setSelectedBrand(null), []);

  async function load() {
    const [s, t, e] = await Promise.all([
      fetchApi<AgentStatus>("/traces/agent-status"),
      fetchApi<{ items: Trace[] }>("/traces?limit=50"),
      fetchApi<{ items: ErrorEntry[] }>("/traces/errors?limit=30"),
    ]);
    if (s) {
      const currentIds = new Set(s.recent_discoveries.map(d => d.id));
      const brandFlash = new Set<number>();
      currentIds.forEach(id => {
        if (!prevDiscoveryIds.has(id)) brandFlash.add(id);
      });
      if (brandFlash.size > 0) {
        setFlashIds(brandFlash);
        setTimeout(() => setFlashIds(new Set()), 3000);
      }
      setPrevDiscoveryIds(currentIds);
    }
    if (t?.items) {
      const currentTraceIds = new Set(t.items.map(x => x.id));
      const newOnes = new Set<string>();
      currentTraceIds.forEach(id => {
        if (!prevTraceIds.has(id)) newOnes.add(id);
      });
      if (newOnes.size > 0) {
        setNewTraceIds(newOnes);
        setTimeout(() => setNewTraceIds(new Set()), 2000);
      }
      setPrevTraceIds(currentTraceIds);
    }
    setStatus(s);
    setTraces(t?.items ?? []);
    setErrors(e?.items ?? []);
    setLoading(false);
  }

  useEffect(() => {
    load();
    const i = setInterval(load, 8_000);
    return () => clearInterval(i);
  }, []);

  useEffect(() => {
    const t = setInterval(() => setTick(v => v + 1), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [traces]);

  if (loading) {
    return (
      <main className="flex h-full items-center justify-center bg-[var(--surface)]">
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            <div className="h-12 w-12 rounded-2xl bg-blue-500/10 flex items-center justify-center">
              <Bot size={24} className="text-blue-400" />
            </div>
            <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-blue-500 agent-heartbeat" />
          </div>
          <span className="text-sm text-[var(--text-muted)]">Connecting to agent...</span>
        </div>
      </main>
    );
  }

  const s = status!;
  const isRunning = traces.some(t => t.status === "running");
  const runningTraces = traces.filter(t => t.status === "running");
  const totalSignals = s.collectors.reduce((sum, c) => sum + (c.total_signals ?? 0), 0);
  const totalRuns = s.collectors.reduce((sum, c) => sum + c.total_runs, 0);
  const activeCollectors = s.collectors.filter(c => {
    if (!c.last_run) return false;
    const diff = Date.now() - new Date(c.last_run).getTime();
    return diff < 300_000;
  });
  const agentHealthy = s.last_discovery?.status === "ok" || s.last_monitoring?.status === "ok";
  const lastActivity = traces[0]?.created_at;

  return (
    <main className="overflow-y-auto max-h-[calc(100vh-56px)] bg-[var(--surface)]">
      <div className="max-w-7xl mx-auto px-6 py-5 space-y-5">

        {/* ─── Agent Status Banner ─── */}
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn(
            "rounded-2xl border p-5 relative overflow-hidden",
            isRunning
              ? "border-blue-500/30 bg-gradient-to-r from-blue-500/[0.06] via-[var(--surface-elevated)] to-indigo-500/[0.06]"
              : agentHealthy
              ? "border-emerald-500/20 bg-gradient-to-r from-emerald-500/[0.04] via-[var(--surface-elevated)] to-cyan-500/[0.04]"
              : "border-[var(--border)] bg-[var(--surface-elevated)]"
          )}
        >
          {isRunning && <div className="absolute inset-0 agent-scanning-line" />}

          <div className="flex items-center justify-between relative z-10">
            <div className="flex items-center gap-4">
              <div className="relative">
                <div className={cn(
                  "h-14 w-14 rounded-2xl flex items-center justify-center",
                  isRunning ? "bg-blue-500/15" : agentHealthy ? "bg-emerald-500/10" : "bg-[var(--surface-overlay)]"
                )}>
                  <Bot size={28} className={isRunning ? "text-blue-400" : agentHealthy ? "text-emerald-400" : "text-[var(--text-muted)]"} />
                </div>
                <div className={cn(
                  "absolute -top-1 -right-1 h-4 w-4 rounded-full border-2 border-[var(--surface-elevated)] flex items-center justify-center",
                  isRunning ? "bg-blue-500 agent-heartbeat" : agentHealthy ? "bg-emerald-500 agent-heartbeat-slow" : "bg-[var(--text-subtle)]"
                )} />
              </div>
              <div>
                <div className="flex items-center gap-3">
                  <h1 className="text-xl font-bold text-[var(--text-primary)] tracking-tight">Lead Intelligence Agent</h1>
                  <span className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
                    isRunning
                      ? "bg-blue-500/15 text-blue-400 border border-blue-500/20"
                      : agentHealthy
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      : "bg-[var(--surface-overlay)] text-[var(--text-muted)] border border-[var(--border)]"
                  )}>
                    <span className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      isRunning ? "bg-blue-400 animate-pulse" : agentHealthy ? "bg-emerald-400 agent-heartbeat-slow" : "bg-[var(--text-subtle)]"
                    )} />
                    {isRunning ? "Working" : agentHealthy ? "Active" : "Idle"}
                  </span>
                </div>
                <p className="text-sm text-[var(--text-muted)] mt-0.5">
                  {isRunning ? (
                    <span className="text-blue-400">
                      Currently processing {runningTraces.length} task{runningTraces.length !== 1 ? "s" : ""}
                      {runningTraces[0]?.brand_name && <> — researching <strong>{runningTraces[0].brand_name}</strong></>}
                    </span>
                  ) : lastActivity ? (
                    <>Last activity {timeAgo(lastActivity)} · Monitoring {s.collectors.length} signal sources</>
                  ) : (
                    "Waiting for first run..."
                  )}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden md:flex items-center gap-4 text-sm">
                <Stat icon={Zap} label="Signals" value={totalSignals} />
                <Stat icon={Search} label="Brands" value={s.recent_discoveries.length} />
                <Stat icon={Cpu} label="Runs" value={totalRuns} />
              </div>
              <button
                onClick={load}
                className="flex items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)]/80 px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-all"
              >
                <RefreshCw size={14} />
                Refresh
              </button>
            </div>
          </div>

          {/* Running tasks progress */}
          {isRunning && runningTraces.length > 0 && (
            <div className="mt-4 pt-4 border-t border-blue-500/10 relative z-10">
              <div className="flex items-center gap-2 mb-2">
                <Loader2 size={12} className="text-blue-400 animate-spin" />
                <span className="text-xs font-semibold text-blue-400 uppercase tracking-wider">Active Tasks</span>
              </div>
              <div className="space-y-2">
                {runningTraces.slice(0, 3).map((t) => (
                  <div key={t.id} className="flex items-center gap-3 rounded-xl bg-blue-500/[0.06] border border-blue-500/10 px-3 py-2">
                    <div className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
                    <span className="text-sm text-[var(--text-primary)] font-medium capitalize">{t.agent.replace(/_/g, " ")}</span>
                    {t.brand_name && <span className="text-xs text-blue-400">→ {t.brand_name}</span>}
                    {t.duration_ms != null && t.duration_ms > 0 && (
                      <span className="ml-auto text-xs text-[var(--text-muted)] tabular-nums">{(t.duration_ms / 1000).toFixed(1)}s</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </motion.div>

        {/* ─── Health Cards ─── */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <HealthCard
            label="Discovery"
            lastRun={s.last_discovery?.created_at ?? null}
            status={s.last_discovery?.status ?? "never_run"}
            duration={s.last_discovery?.duration_ms ?? null}
            error={s.last_discovery?.error ?? null}
            icon={Search}
          />
          <HealthCard
            label="Monitoring"
            lastRun={s.last_monitoring?.created_at ?? null}
            status={s.last_monitoring?.status ?? "never_run"}
            duration={s.last_monitoring?.duration_ms ?? null}
            error={s.last_monitoring?.error ?? null}
            icon={Eye}
          />
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
            <div className="flex items-center gap-2 mb-2">
              <Database size={14} className="text-violet-400" />
              <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">Queue</span>
            </div>
            <div className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">{s.brands_pending_research}</div>
            <div className="text-[11px] text-[var(--text-subtle)] mt-0.5">brands awaiting research</div>
            {s.brands_pending_research > 0 && (
              <div className="mt-2 h-1 rounded-full bg-violet-500/20 overflow-hidden">
                <motion.div
                  className="h-full bg-violet-500 rounded-full"
                  animate={{ width: ["0%", "100%", "0%"] }}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                />
              </div>
            )}
          </div>
          <ErrorHealthCard summary={s.error_summary} />
        </div>

        {/* ─── Signal Sources (Collectors) ─── */}
        <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="flex items-center gap-2 text-xs uppercase tracking-widest text-[var(--text-muted)] font-semibold">
              <Wifi size={13} />
              Signal Sources
              {activeCollectors.length > 0 && (
                <span className="text-emerald-400 font-mono">{activeCollectors.length} active</span>
              )}
            </h2>
            <span className="text-[11px] text-[var(--text-subtle)] tabular-nums">{totalSignals} total signals</span>
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-5">
            {s.collectors.length === 0 && (
              <div className="col-span-full rounded-2xl border border-dashed border-[var(--border)] bg-[var(--surface-elevated)] py-8 text-center">
                <Radio size={24} className="mx-auto mb-2 text-[var(--text-subtle)] opacity-40" />
                <p className="text-sm text-[var(--text-subtle)]">No collectors configured</p>
              </div>
            )}
            {s.collectors.map((c, i) => {
              const recentlyActive = c.last_run && (Date.now() - new Date(c.last_run).getTime()) < 300_000;
              const errorRate = c.total_runs > 0 ? (c.errors / c.total_runs) * 100 : 0;
              return (
                <motion.div
                  key={c.collector}
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.1 + i * 0.03 }}
                  className={cn(
                    "rounded-2xl border p-3.5 transition-all relative overflow-hidden",
                    recentlyActive
                      ? "border-emerald-500/20 bg-emerald-500/[0.03] hover:border-emerald-500/30"
                      : "border-[var(--border)] bg-[var(--surface-elevated)] hover:border-[var(--border-strong)]"
                  )}
                >
                  {recentlyActive && <div className="absolute top-2 right-2 h-2 w-2 rounded-full bg-emerald-400 agent-heartbeat-slow" />}
                  <div className="flex items-center gap-2 mb-2">
                    <Radio size={12} className={recentlyActive ? "text-emerald-400" : "text-[var(--text-subtle)]"} />
                    <span className="text-xs font-semibold text-[var(--text-primary)] truncate">{c.collector.replace(/_/g, " ")}</span>
                  </div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-xl font-bold text-[var(--text-primary)] tabular-nums">{c.total_signals ?? 0}</span>
                    <span className="text-[10px] text-[var(--text-muted)]">signals</span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-2 text-[10px] text-[var(--text-muted)]">
                    <span>{c.total_runs} runs</span>
                    {c.errors > 0 && <span className="text-rose-400">{c.errors} err</span>}
                  </div>
                  {c.total_runs > 0 && (
                    <div className="mt-2 h-1 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
                      <div
                        className={cn("h-full rounded-full transition-all", errorRate > 20 ? "bg-rose-500" : errorRate > 5 ? "bg-amber-500" : "bg-emerald-500")}
                        style={{ width: `${Math.min(100 - errorRate, 100)}%` }}
                      />
                    </div>
                  )}
                  <div className="mt-1 text-[10px] text-[var(--text-subtle)]">{c.last_run ? timeAgo(c.last_run) : "never"}</div>
                </motion.div>
              );
            })}
          </div>
        </motion.section>

        {/* ─── Live Feed + Discoveries ─── */}
        <div className="grid gap-4 lg:grid-cols-5">

          {/* Live Activity Feed — 3 cols */}
          <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
            className="lg:col-span-3"
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="flex items-center gap-2 text-xs uppercase tracking-widest text-[var(--text-muted)] font-semibold">
                <Terminal size={13} />
                Live Feed
                {isRunning && <span className="inline-flex items-center gap-1 text-blue-400"><span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" /> streaming</span>}
              </h2>
              <span className="text-[10px] text-[var(--text-subtle)] tabular-nums">{traces.length} events</span>
            </div>
            <div
              ref={logRef}
              className="rounded-2xl border border-[var(--border)] bg-[#0c0c0f] overflow-hidden max-h-[480px] overflow-y-auto font-mono"
            >
              {traces.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <Terminal size={32} className="text-[var(--text-subtle)] opacity-30 mb-3" />
                  <p className="text-sm text-[var(--text-subtle)]">Waiting for agent activity...</p>
                  <div className="flex items-center gap-1.5 mt-2">
                    <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
                    <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse [animation-delay:200ms]" />
                    <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse [animation-delay:400ms]" />
                  </div>
                </div>
              ) : (
                <div className="divide-y divide-[#1a1a20]">
                  {[...traces].reverse().map((t) => {
                    const isNew = newTraceIds.has(t.id);
                    return (
                      <motion.div
                        key={t.id}
                        initial={isNew ? { backgroundColor: "rgba(59, 130, 246, 0.15)" } : {}}
                        animate={{ backgroundColor: "rgba(59, 130, 246, 0)" }}
                        transition={{ duration: 2 }}
                        className="flex items-start gap-3 px-4 py-2.5 hover:bg-white/[0.02] transition-colors group"
                      >
                        <span className="text-[10px] text-[#4a4a55] tabular-nums shrink-0 pt-0.5 w-14">
                          {formatLogTime(t.created_at)}
                        </span>
                        <FeedStatusIcon status={t.status} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className={cn(
                              "text-xs font-medium capitalize",
                              t.status === "ok" ? "text-emerald-400/90" :
                              t.status === "running" ? "text-blue-400" :
                              t.status === "error" ? "text-rose-400" : "text-[#8a8a95]"
                            )}>
                              {t.agent.replace(/_/g, " ")}
                            </span>
                            {t.brand_name && (
                              <>
                                <ArrowRight size={10} className="text-[#3a3a45]" />
                                <span className="text-xs text-[#7a7a85] truncate">{t.brand_name}</span>
                              </>
                            )}
                          </div>
                          {t.error && <div className="text-[10px] text-rose-400/80 truncate mt-0.5">{t.error}</div>}
                        </div>
                        <div className="text-right shrink-0">
                          {t.duration_ms != null && t.duration_ms > 0 && (
                            <span className="text-[10px] text-[#5a5a65] tabular-nums">{(t.duration_ms / 1000).toFixed(1)}s</span>
                          )}
                        </div>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.section>

          {/* Discoveries — 2 cols */}
          <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="lg:col-span-2"
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="flex items-center gap-2 text-xs uppercase tracking-widest text-[var(--text-muted)] font-semibold">
                <Sparkles size={13} />
                Discoveries
              </h2>
              <span className="text-[10px] text-[var(--text-subtle)] tabular-nums">{s.recent_discoveries.length} brands</span>
            </div>
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] overflow-hidden max-h-[480px] overflow-y-auto">
              {s.recent_discoveries.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <div className="relative mb-4">
                    <Search size={32} className="text-[var(--text-subtle)] opacity-30" />
                    <motion.div
                      className="absolute inset-0"
                      animate={{ rotate: 360 }}
                      transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
                    >
                      <div className="absolute -top-1 left-1/2 h-1.5 w-1.5 rounded-full bg-blue-400/50" />
                    </motion.div>
                  </div>
                  <p className="text-sm text-[var(--text-subtle)]">Agent is searching...</p>
                  <p className="text-xs text-[var(--text-subtle)] mt-1 opacity-60">New brands will appear here</p>
                </div>
              ) : (
                <div className="divide-y divide-[var(--border)]">
                  {s.recent_discoveries.map((b, i) => {
                    const isFlash = flashIds.has(b.id);
                    return (
                      <motion.div
                        key={b.id}
                        initial={isFlash ? { backgroundColor: "rgba(59, 130, 246, 0.12)", x: -8 } : { opacity: 0, x: -4 }}
                        animate={{ backgroundColor: "rgba(59, 130, 246, 0)", opacity: 1, x: 0 }}
                        transition={isFlash ? { duration: 2 } : { delay: i * 0.03 }}
                        onClick={() => setSelectedBrand(b.id)}
                        className={cn(
                          "flex items-center gap-3 px-4 py-3 cursor-pointer transition-all group",
                          "hover:bg-[var(--surface-overlay)]",
                          isFlash && "ring-1 ring-blue-500/20"
                        )}
                      >
                        <div className={cn(
                          "h-9 w-9 rounded-xl flex items-center justify-center shrink-0 text-xs font-bold",
                          b.tier === "hot" ? "bg-rose-500/15 text-rose-400" :
                          b.tier === "warm" ? "bg-amber-500/15 text-amber-400" :
                          "bg-blue-500/10 text-blue-400"
                        )}>
                          {b.score ?? "—"}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-[var(--text-primary)] truncate group-hover:text-blue-400 transition-colors">{b.name}</span>
                            {isFlash && (
                              <motion.span
                                initial={{ opacity: 1 }}
                                animate={{ opacity: 0 }}
                                transition={{ duration: 3 }}
                                className="text-[10px] font-bold text-blue-400 uppercase"
                              >
                                new
                              </motion.span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[11px] text-[var(--text-muted)] truncate">{b.domain}</span>
                            {b.tier && (
                              <span className={cn(
                                "rounded-full px-1.5 py-0.5 text-[9px] font-bold uppercase",
                                b.tier === "hot" ? "bg-rose-500/15 text-rose-400" :
                                b.tier === "warm" ? "bg-amber-500/15 text-amber-400" :
                                "bg-blue-500/10 text-blue-400"
                              )}>
                                {b.tier}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="text-[10px] text-[var(--text-subtle)] shrink-0">{timeAgo(b.created_at)}</span>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.section>
        </div>

        {/* ─── Error Log ─── */}
        {errors.length > 0 && (
          <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="pb-6">
            <h2 className="mb-3 flex items-center gap-2 text-xs uppercase tracking-widest text-[var(--text-muted)] font-semibold">
              <AlertTriangle size={13} />
              Recent Errors
              <span className="text-rose-400 font-mono">{errors.length}</span>
            </h2>
            <div className="rounded-2xl border border-rose-500/10 bg-[var(--surface-elevated)] overflow-hidden max-h-[280px] overflow-y-auto">
              {errors.map((e, i) => (
                <div key={i} className="flex items-start gap-3 px-4 py-3 border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--surface-overlay)] transition-colors">
                  <CategoryBadge category={e.category} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-[var(--text-primary)]">{e.message}</div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-[11px] text-[var(--text-muted)] font-medium">{e.source}</span>
                      <span className="text-[11px] text-[var(--text-subtle)]">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </motion.section>
        )}
      </div>

      <BrandPanel brandId={selectedBrand} onClose={closePanel} />
    </main>
  );
}

/* ─── Sub-components ─── */

function Stat({ icon: Icon, label, value }: { icon: React.ComponentType<{ size?: number; className?: string }>; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--surface)]/60">
      <Icon size={13} className="text-[var(--text-subtle)]" />
      <span className="text-xs text-[var(--text-muted)]">{label}</span>
      <span className="text-sm font-bold text-[var(--text-primary)] tabular-nums">{value}</span>
    </div>
  );
}

function FeedStatusIcon({ status }: { status: string }) {
  if (status === "running") {
    return <Loader2 size={12} className="text-blue-400 animate-spin shrink-0 mt-0.5" />;
  }
  if (status === "ok") {
    return <CheckCircle2 size={12} className="text-emerald-500/70 shrink-0 mt-0.5" />;
  }
  if (status === "error") {
    return <XCircle size={12} className="text-rose-400/70 shrink-0 mt-0.5" />;
  }
  return <Circle size={12} className="text-[#4a4a55] shrink-0 mt-0.5" />;
}

function ErrorHealthCard({ summary }: { summary: ErrorSummary | null }) {
  if (!summary) {
    return (
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
        <div className="flex items-center gap-2 mb-2">
          <Shield size={14} className="text-[var(--text-muted)]" />
          <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">Errors</span>
        </div>
        <div className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">0</div>
        <div className="text-[11px] text-[var(--text-subtle)] mt-0.5">all clear</div>
      </div>
    );
  }
  const healthStyles = {
    healthy: { border: "border-emerald-500/20", bg: "bg-emerald-500/[0.04]", color: "text-emerald-400" },
    degraded: { border: "border-amber-500/20", bg: "bg-amber-500/[0.04]", color: "text-amber-400" },
    unhealthy: { border: "border-rose-500/20", bg: "bg-rose-500/[0.04]", color: "text-rose-400" },
  };
  const hs = healthStyles[summary.health];

  return (
    <div className={cn("rounded-2xl border p-4 transition-all", hs.border, hs.bg)}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Shield size={14} className={hs.color} />
          <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">Errors</span>
        </div>
        <span className={cn("text-[10px] font-semibold uppercase", hs.color)}>{summary.health}</span>
      </div>
      <div className="text-2xl font-bold text-[var(--text-primary)] tabular-nums">{summary.last_hour_total}</div>
      <div className="text-[11px] text-[var(--text-subtle)] mt-0.5">last hour</div>
      {Object.entries(summary.by_category).length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {Object.entries(summary.by_category).map(([cat, count]) => (
            <span key={cat} className="rounded bg-[var(--surface-overlay)] px-1.5 py-0.5 text-[9px] font-medium text-[var(--text-muted)]">
              {cat}: {count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryBadge({ category }: { category: string }) {
  const colors: Record<string, string> = {
    rate_limit: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    captcha: "bg-orange-500/10 text-orange-400 border-orange-500/20",
    timeout: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    api_error: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    activity_error: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    config_error: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    internal: "bg-[var(--surface-overlay)] text-[var(--text-muted)] border-[var(--border)]",
  };
  return (
    <span className={cn("shrink-0 rounded-lg border px-2 py-0.5 text-[10px] font-semibold", colors[category] ?? colors.internal)}>
      {category.replace(/_/g, " ")}
    </span>
  );
}

function HealthCard({
  label, lastRun, status, duration, error, icon: Icon,
}: {
  label: string;
  lastRun: string | null;
  status: string;
  duration: number | null;
  error: string | null;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}) {
  const isHealthy = status === "ok";
  const isRunning = status === "running";
  const neverRan = status === "never_run";
  const styles = isRunning
    ? { border: "border-blue-500/20", bg: "bg-blue-500/[0.04]", color: "text-blue-400" }
    : neverRan
    ? { border: "border-[var(--border)]", bg: "bg-[var(--surface-elevated)]", color: "text-[var(--text-muted)]" }
    : isHealthy
    ? { border: "border-emerald-500/20", bg: "bg-emerald-500/[0.04]", color: "text-emerald-400" }
    : { border: "border-rose-500/20", bg: "bg-rose-500/[0.04]", color: "text-rose-400" };

  return (
    <div className={cn("rounded-2xl border p-4 transition-all", styles.border, styles.bg)}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Icon size={14} className={styles.color} />
          <span className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">{label}</span>
        </div>
        <span className={cn("flex items-center gap-1 text-[10px] font-semibold", styles.color)}>
          {isRunning ? (
            <><Loader2 size={10} className="animate-spin" /> running</>
          ) : neverRan ? (
            <><Clock size={10} /> waiting</>
          ) : isHealthy ? (
            <><CheckCircle2 size={10} /> ok</>
          ) : (
            <><XCircle size={10} /> error</>
          )}
        </span>
      </div>
      <div className="text-xs text-[var(--text-secondary)]">{lastRun ? timeAgo(lastRun) : "Never ran"}</div>
      {duration != null && duration > 0 && (
        <div className="text-[10px] text-[var(--text-subtle)] mt-0.5">{(duration / 1000).toFixed(1)}s</div>
      )}
      {error && <div className="mt-1.5 text-[10px] text-rose-400 truncate">{error}</div>}
    </div>
  );
}

function formatLogTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
