"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Flame,
  ThermometerSun,
  Eye,
  Send,
  Trophy,
  XCircle,
  ChevronRight,
  GripVertical,
  Activity,
  Clock,
  Globe,
  TrendingUp,
  AlertTriangle,
  Search,
  ArrowUpDown,
} from "lucide-react";
import { cn, tierColor } from "@/lib/utils";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

type PipelineBrand = {
  id: number;
  name: string;
  domain: string | null;
  status: string;
  tier?: string | null;
  score?: number | null;
  score_headline?: string | null;
  diagnosis?: string | null;
  gap_summary?: string | null;
};

type Trace = {
  id: string;
  workflow_id: string;
  brand_id: number | null;
  agent: string;
  brand_name?: string;
  total_cost_cents: number;
  duration_ms: number | null;
  status: string;
  created_at: string;
};

const TIER_GROUPS = [
  { key: "hot", label: "Hot", color: "text-rose-400", bg: "bg-rose-500/10", ring: "ring-rose-500/30", icon: Flame, borderColor: "border-l-rose-500", dotColor: "bg-rose-400" },
  { key: "warm", label: "Warm", color: "text-amber-400", bg: "bg-amber-500/10", ring: "ring-amber-500/30", icon: ThermometerSun, borderColor: "border-l-amber-500", dotColor: "bg-amber-400" },
  { key: "watchlist", label: "Watchlist", color: "text-blue-400", bg: "bg-blue-500/10", ring: "ring-blue-500/30", icon: Eye, borderColor: "border-l-blue-500", dotColor: "bg-blue-400" },
] as const;

const STATUS_GROUPS = [
  { key: "contacted", label: "Sent", color: "text-sky-400", bg: "bg-sky-500/10", icon: Send, borderColor: "border-l-sky-500", dotColor: "bg-sky-400" },
  { key: "partner", label: "Won", color: "text-emerald-400", bg: "bg-emerald-500/10", icon: Trophy, borderColor: "border-l-emerald-500", dotColor: "bg-emerald-400" },
  { key: "rejected", label: "Lost", color: "text-neutral-500", bg: "bg-neutral-500/10", icon: XCircle, borderColor: "border-l-neutral-500", dotColor: "bg-neutral-500" },
] as const;

type Tab = "leads" | "activity";
type SortKey = "score" | "name" | "date";

export function PipelineRail({ onSelectBrand, selectedBrandId }: { onSelectBrand?: (id: number) => void; selectedBrandId?: number | null } = {}) {
  const [tab, setTab] = useState<Tab>("leads");
  const [tierBrands, setTierBrands] = useState<Record<string, PipelineBrand[]>>({});
  const [statusBrands, setStatusBrands] = useState<Record<string, PipelineBrand[]>>({});
  const [traces, setTraces] = useState<Trace[]>([]);
  const [expanded, setExpanded] = useState<string | null>("hot");
  const [dragBrand, setDragBrand] = useState<PipelineBrand | null>(null);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<SortKey>("score");

  useEffect(() => {
    loadAll();
    const interval = setInterval(loadAll, 10_000);
    return () => clearInterval(interval);
  }, []);

  async function loadAll() {
    await Promise.all([loadLeads(), loadTraces()]);
  }

  async function loadLeads() {
    const headers = { "X-API-Key": API_KEY };
    try {
      let items: PipelineBrand[] = [];
      let offset = 0;
      const pageSize = 500;
      while (true) {
        const res = await fetch(
          `${API_BASE}/brands?limit=${pageSize}&offset=${offset}&has_dossier=true`,
          { headers },
        );
        if (!res.ok) break;
        const data = await res.json();
        const page: PipelineBrand[] = data.items ?? [];
        items = items.concat(page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }

      const byTier: Record<string, PipelineBrand[]> = {};
      const byStatus: Record<string, PipelineBrand[]> = {};

      for (const b of items) {
        if (["contacted", "partner", "rejected"].includes(b.status)) {
          (byStatus[b.status] ??= []).push(b);
        } else {
          let t = b.tier ?? "watchlist";
          if (t === "park") t = "watchlist";
          (byTier[t] ??= []).push(b);
        }
      }
      setTierBrands(byTier);
      setStatusBrands(byStatus);
    } catch { /* polling, ignore */ }
  }

  async function loadTraces() {
    try {
      const res = await fetch(`${API_BASE}/traces?limit=25`, {
        headers: { "X-API-Key": API_KEY },
      });
      if (!res.ok) return;
      const data = await res.json();
      setTraces(data.items ?? []);
    } catch { /* polling */ }
  }

  async function moveBrand(brandId: number, newStatus: string) {
    try {
      await fetch(`${API_BASE}/brands/${brandId}/status`, {
        method: "PATCH",
        headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      await loadLeads();
    } catch { await loadLeads(); }
  }

  const totalLeads = Object.values(tierBrands).reduce((a, b) => a + b.length, 0)
    + Object.values(statusBrands).reduce((a, b) => a + b.length, 0);

  const filterFn = (b: PipelineBrand) => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return b.name.toLowerCase().includes(q) || (b.domain ?? "").toLowerCase().includes(q);
  };

  const sortFn = (a: PipelineBrand, b: PipelineBrand) => {
    if (sort === "score") return (b.score ?? 0) - (a.score ?? 0);
    if (sort === "name") return a.name.localeCompare(b.name);
    return 0;
  };

  return (
    <div className="flex flex-col h-full w-full bg-[var(--surface-elevated)] overflow-hidden">
      {/* Header */}
      <div className="px-3 pt-3 pb-2">
        <div className="flex items-center gap-1 mb-3 bg-[var(--surface)]/50 rounded-xl p-1">
          <button
            onClick={() => setTab("leads")}
            className={cn(
              "flex-1 text-[11px] font-semibold uppercase tracking-[0.08em] px-3 py-1.5 rounded-lg transition-all text-center",
              tab === "leads"
                ? "text-[var(--text-primary)] bg-[var(--surface-elevated)] shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            )}
          >
            Leads {totalLeads > 0 && <span className="ml-1 tabular-nums">{totalLeads}</span>}
          </button>
          <button
            onClick={() => setTab("activity")}
            className={cn(
              "flex-1 text-[11px] font-semibold uppercase tracking-[0.08em] px-3 py-1.5 rounded-lg transition-all flex items-center justify-center gap-1.5",
              tab === "activity"
                ? "text-[var(--text-primary)] bg-[var(--surface-elevated)] shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            )}
          >
            <Activity size={11} />
            Activity
          </button>
        </div>

        {tab === "leads" && (
          <div className="flex items-center gap-1.5">
            <div className="flex-1 relative">
              <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-subtle)]" />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter leads..."
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] pl-7 pr-2 py-1.5 text-[11px] text-[var(--text-primary)] placeholder:text-[var(--text-subtle)] focus:outline-none focus:border-blue-500/40 focus:ring-1 focus:ring-blue-500/15 transition-all"
              />
            </div>
            <button
              onClick={() => setSort(sort === "score" ? "name" : sort === "name" ? "date" : "score")}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[10px] text-[var(--text-muted)] hover:border-[var(--border-strong)] transition-colors"
              title={`Sort by ${sort}`}
            >
              <ArrowUpDown size={10} />
              {sort === "score" ? "Score" : sort === "name" ? "A-Z" : "Date"}
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {tab === "leads" ? (
          <LeadsView
            tierBrands={tierBrands}
            statusBrands={statusBrands}
            expanded={expanded}
            setExpanded={setExpanded}
            dragBrand={dragBrand}
            setDragBrand={setDragBrand}
            moveBrand={moveBrand}
            filterFn={filterFn}
            sortFn={sortFn}
            onSelectBrand={onSelectBrand}
            selectedBrandId={selectedBrandId}
          />
        ) : (
          <ActivityView traces={traces} />
        )}
      </div>
    </div>
  );
}

function LeadsView({
  tierBrands, statusBrands, expanded, setExpanded, dragBrand, setDragBrand, moveBrand, filterFn, sortFn, onSelectBrand, selectedBrandId,
}: {
  tierBrands: Record<string, PipelineBrand[]>;
  statusBrands: Record<string, PipelineBrand[]>;
  expanded: string | null;
  setExpanded: (v: string | null) => void;
  dragBrand: PipelineBrand | null;
  setDragBrand: (v: PipelineBrand | null) => void;
  moveBrand: (id: number, status: string) => void;
  filterFn: (b: PipelineBrand) => boolean;
  sortFn: (a: PipelineBrand, b: PipelineBrand) => number;
  onSelectBrand?: (id: number) => void;
  selectedBrandId?: number | null;
}) {
  return (
    <ul className="space-y-0.5">
      {TIER_GROUPS.map((g) => {
        const Icon = g.icon;
        const allItems = tierBrands[g.key] ?? [];
        const items = allItems.filter(filterFn).sort(sortFn);
        const isExpanded = expanded === g.key;
        return (
          <li key={g.key}>
            <button
              onClick={() => setExpanded(isExpanded ? null : g.key)}
              className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 hover:bg-[var(--surface-overlay)] transition-all group"
            >
              <div className={cn("flex items-center justify-center w-8 h-8 rounded-xl", g.bg)}>
                <Icon size={14} className={g.color} />
              </div>
              <span className="flex-1 text-left text-xs font-semibold text-[var(--text-primary)]">{g.label}</span>
              <span className={cn(
                "text-[11px] tabular-nums font-bold rounded-full px-2.5 py-0.5",
                items.length > 0 ? `${g.bg} ${g.color}` : "text-[var(--text-subtle)]"
              )}>
                {items.length}
              </span>
              <motion.div animate={{ rotate: isExpanded ? 90 : 0 }} transition={{ duration: 0.15 }}>
                <ChevronRight size={13} className="text-[var(--text-subtle)] group-hover:text-[var(--text-muted)]" />
              </motion.div>
            </button>

            <AnimatePresence>
              {isExpanded && (
                <motion.ul
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="ml-2 mt-0.5 space-y-1.5 overflow-hidden pl-2"
                >
                  {items.map((b) => (
                    <LeadCard key={b.id} brand={b} group={g} setDragBrand={setDragBrand} onSelectBrand={onSelectBrand} isSelected={selectedBrandId === b.id} />
                  ))}
                  {items.length === 0 && (
                    <li className="px-3 py-4 text-[11px] text-[var(--text-subtle)] italic text-center">No leads yet</li>
                  )}
                </motion.ul>
              )}
            </AnimatePresence>
          </li>
        );
      })}

      <li className="py-2.5">
        <div className="border-t border-[var(--border)] mx-2" />
        <span className="block text-[10px] uppercase tracking-[0.15em] text-[var(--text-subtle)] px-2.5 pt-2.5 font-bold">
          Outreach
        </span>
      </li>

      {STATUS_GROUPS.map((g) => {
        const Icon = g.icon;
        const items = statusBrands[g.key] ?? [];
        const isExpanded = expanded === g.key;
        return (
          <li key={g.key}>
            <button
              onClick={() => setExpanded(isExpanded ? null : g.key)}
              onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("ring-1", "ring-blue-500/50"); }}
              onDragLeave={(e) => { e.currentTarget.classList.remove("ring-1", "ring-blue-500/50"); }}
              onDrop={(e) => {
                e.preventDefault();
                e.currentTarget.classList.remove("ring-1", "ring-blue-500/50");
                if (dragBrand) moveBrand(dragBrand.id, g.key);
                setDragBrand(null);
              }}
              className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 hover:bg-[var(--surface-overlay)] transition-all group"
            >
              <div className={cn("flex items-center justify-center w-8 h-8 rounded-xl", g.bg)}>
                <Icon size={14} className={g.color} />
              </div>
              <span className="flex-1 text-left text-xs font-semibold text-[var(--text-primary)]">{g.label}</span>
              <span className={cn(
                "text-[11px] tabular-nums font-bold",
                items.length > 0 ? g.color : "text-[var(--text-subtle)]"
              )}>
                {items.length}
              </span>
              <motion.div animate={{ rotate: isExpanded ? 90 : 0 }} transition={{ duration: 0.15 }}>
                <ChevronRight size={13} className="text-[var(--text-subtle)]" />
              </motion.div>
            </button>

            <AnimatePresence>
              {isExpanded && (
                <motion.ul
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="ml-2 mt-0.5 space-y-1.5 overflow-hidden pl-2"
                >
                  {items.map((b) => (
                    <LeadCard key={b.id} brand={b} group={g} setDragBrand={setDragBrand} onSelectBrand={onSelectBrand} isSelected={selectedBrandId === b.id} />
                  ))}
                  {items.length === 0 && (
                    <li className="px-3 py-4 text-[11px] text-[var(--text-subtle)] italic text-center">None</li>
                  )}
                </motion.ul>
              )}
            </AnimatePresence>
          </li>
        );
      })}
    </ul>
  );
}

function LeadCard({
  brand: b, group, setDragBrand, onSelectBrand, isSelected,
}: {
  brand: PipelineBrand;
  group: { color: string; ring?: string; borderColor: string };
  setDragBrand: (v: PipelineBrand | null) => void;
  onSelectBrand?: (id: number) => void;
  isSelected?: boolean;
}) {
  return (
    <li
      draggable
      onDragStart={() => setDragBrand(b)}
      onDragEnd={() => setDragBrand(null)}
      onClick={(e) => { e.preventDefault(); onSelectBrand?.(b.id); }}
      className={cn(
        "rounded-xl bg-[var(--surface)] border border-[var(--border)] border-l-2 px-3 py-2.5",
        "cursor-pointer active:cursor-grabbing",
        "hover:border-[var(--border-strong)] hover:shadow-sm transition-all",
        group.borderColor,
        group.ring ? `hover:ring-1 ${group.ring}` : "",
        isSelected && "ring-2 ring-blue-500/40 border-blue-500/50 bg-blue-500/5",
      )}
    >
      <div className="block">
        <div className="flex items-center gap-2 mb-1.5">
          <GripVertical size={10} className="text-[var(--text-subtle)] shrink-0 opacity-40" />
          <span className="text-xs font-semibold text-[var(--text-primary)] truncate flex-1">
            {b.name}
          </span>
          {b.score != null && (
            <span className={cn("text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded-lg", tierColor(b.tier).bg, tierColor(b.tier).text)}>
              {b.score}
            </span>
          )}
        </div>

        {b.domain && (
          <div className="flex items-center gap-1.5 mb-1.5 pl-5">
            <Globe size={10} className="text-[var(--text-subtle)] shrink-0" />
            <span className="text-[11px] text-[var(--text-muted)] truncate">{b.domain}</span>
          </div>
        )}

        {b.diagnosis && (
          <div className="flex items-start gap-1.5 pl-5">
            <AlertTriangle size={10} className="text-amber-500/70 shrink-0 mt-0.5" />
            <span className="text-[11px] text-[var(--text-muted)] leading-relaxed line-clamp-2">
              {b.diagnosis}
            </span>
          </div>
        )}

        {!b.diagnosis && b.gap_summary && (
          <div className="flex items-start gap-1.5 pl-5">
            <TrendingUp size={10} className="text-blue-500/70 shrink-0 mt-0.5" />
            <span className="text-[11px] text-[var(--text-muted)] leading-relaxed line-clamp-2">
              {b.gap_summary}
            </span>
          </div>
        )}
      </div>
    </li>
  );
}

function ActivityView({ traces }: { traces: Trace[] }) {
  if (traces.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--surface-overlay)] mb-3">
          <Activity size={20} className="opacity-40" />
        </div>
        <span className="text-xs">No agent activity yet</span>
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {traces.map((t) => (
        <li
          key={t.id}
          className="rounded-xl bg-[var(--surface)] border border-[var(--border)] p-3 hover:border-[var(--border-strong)] transition-all"
        >
          <div className="flex items-center justify-between mb-1.5">
            <div className="flex items-center gap-2 min-w-0">
              <div className={cn(
                "w-2 h-2 rounded-full shrink-0",
                t.status === "ok" ? "bg-emerald-500" : t.status === "error" ? "bg-red-500" : t.status === "running" ? "bg-blue-500 animate-pulse" : "bg-amber-500"
              )} />
              <span className="text-[12px] font-bold text-[var(--text-primary)] truncate">
                {formatAgent(t.agent)}
              </span>
            </div>
            {t.duration_ms != null && (
              <span className="text-[11px] tabular-nums text-[var(--text-muted)] flex items-center gap-1 shrink-0 ml-2">
                <Clock size={10} className="text-[var(--text-subtle)]" />
                {formatDuration(t.duration_ms)}
              </span>
            )}
          </div>

          {t.brand_name && (
            <div className="text-[12px] text-[var(--text-secondary)] font-medium truncate mb-1.5 pl-4">
              {t.brand_name}
            </div>
          )}

          <div className="flex items-center gap-2 pl-4">
            <span className="text-[10px] text-[var(--text-subtle)] shrink-0">
              {formatTime(t.created_at)}
            </span>
            <span className="text-[10px] text-[var(--text-subtle)] truncate opacity-40 font-mono" title={t.workflow_id}>
              {t.workflow_id.slice(0, 20)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function formatAgent(agent: string): string {
  return agent.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    if (diffMs < 60_000) return "just now";
    if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
    if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
    return d.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}
