"use client";

import { useEffect, useState, useCallback, useRef, DragEvent } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  Filter,
  GripVertical,
  Activity,
  Globe,
  LayoutList,
  CheckSquare,
  Download,
  Zap,
  X,
} from "lucide-react";
import { cn, tierColor, formatINR } from "@/lib/utils";
import { BrandPanel } from "@/components/brand-panel";
import type { Brand } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
        ...(init?.headers as Record<string, string> ?? {}),
      },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// --- Pipeline stages ---

type Stage = {
  key: string;
  label: string;
  color: string;        // tailwind color token (e.g. "neutral", "blue")
  dotClass: string;      // dot / accent color class
  bgClass: string;       // column header bg
  borderClass: string;   // top border accent
};

const STAGES: Stage[] = [
  { key: "new",        label: "New",        color: "neutral", dotClass: "bg-neutral-400", bgClass: "bg-neutral-500/10", borderClass: "border-t-neutral-500" },
  { key: "qualified",  label: "Qualified",  color: "blue",    dotClass: "bg-blue-400",    bgClass: "bg-blue-500/10",    borderClass: "border-t-blue-500" },
  { key: "contacted",  label: "Contacted",  color: "sky",     dotClass: "bg-sky-400",     bgClass: "bg-sky-500/10",     borderClass: "border-t-sky-500" },
  { key: "responded",  label: "Responded",  color: "indigo",  dotClass: "bg-indigo-400",  bgClass: "bg-indigo-500/10",  borderClass: "border-t-indigo-500" },
  { key: "meeting",    label: "Meeting",    color: "amber",   dotClass: "bg-amber-400",   bgClass: "bg-amber-500/10",   borderClass: "border-t-amber-500" },
  { key: "proposal",   label: "Proposal",   color: "purple",  dotClass: "bg-purple-400",  bgClass: "bg-purple-500/10",  borderClass: "border-t-purple-500" },
  { key: "won",        label: "Won",        color: "emerald", dotClass: "bg-emerald-400", bgClass: "bg-emerald-500/10", borderClass: "border-t-emerald-500" },
  { key: "lost",       label: "Lost",       color: "rose",    dotClass: "bg-rose-400",    bgClass: "bg-rose-500/10",    borderClass: "border-t-rose-500" },
];

const STAGE_KEYS = STAGES.map((s) => s.key);

function stageOf(brand: Brand): string {
  const s = (brand.status ?? "").toLowerCase().replace(/\s+/g, "_");
  if (STAGE_KEYS.includes(s)) return s;
  return "new";
}

// --- Page component ---

export default function PipelinePage() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState<string>("all");
  const [panelBrandId, setPanelBrandId] = useState<number | null>(null);
  const [dragBrandId, setDragBrandId] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkAction, setBulkAction] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const clearSelection = () => { setSelected(new Set()); setBulkAction(false); };

  const handleBulkStatus = async (status: string) => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    await fetchApi("/brands/bulk/status", {
      method: "PATCH",
      body: JSON.stringify({ brand_ids: ids, status }),
    });
    setBrands((prev) => prev.map((b) => ids.includes(b.id) ? { ...b, status } : b));
    clearSelection();
  };

  const handleBulkEnrich = async () => {
    const ids = Array.from(selected).slice(0, 20);
    await fetchApi("/brands/bulk/enrich", {
      method: "POST",
      body: JSON.stringify({ brand_ids: ids }),
    });
    clearSelection();
  };

  const handleExportCsv = () => {
    const params = new URLSearchParams();
    if (tierFilter !== "all") params.set("tier", tierFilter);
    window.open(`${API_BASE}/brands/export/csv?${params}`, "_blank");
  };

  // Fetch brands
  const loadBrands = useCallback(async () => {
    const res = await fetchApi<{ items: Brand[] }>("/brands?limit=500");
    if (res?.items) setBrands(res.items);
  }, []);

  useEffect(() => {
    loadBrands().then(() => setLoading(false));
    const interval = setInterval(loadBrands, 15_000);
    return () => clearInterval(interval);
  }, [loadBrands]);

  // Filter brands
  const filtered = brands.filter((b) => {
    if (search) {
      const q = search.toLowerCase();
      if (
        !b.name.toLowerCase().includes(q) &&
        !(b.domain ?? "").toLowerCase().includes(q)
      )
        return false;
    }
    if (tierFilter !== "all" && (b.tier ?? "").toLowerCase() !== tierFilter)
      return false;
    return true;
  });

  // Group by stage
  const columns: Record<string, Brand[]> = {};
  for (const s of STAGES) columns[s.key] = [];
  for (const b of filtered) {
    const s = stageOf(b);
    columns[s].push(b);
  }

  // Drag handlers
  const onDragStart = (e: DragEvent, brandId: number) => {
    setDragBrandId(brandId);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(brandId));
  };

  const onDragOver = (e: DragEvent, stageKey: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDropTarget(stageKey);
  };

  const onDragLeave = () => {
    setDropTarget(null);
  };

  const onDrop = async (e: DragEvent, stageKey: string) => {
    e.preventDefault();
    setDropTarget(null);
    const id = parseInt(e.dataTransfer.getData("text/plain"), 10);
    if (isNaN(id)) return;

    const brand = brands.find((b) => b.id === id);
    if (!brand || stageOf(brand) === stageKey) {
      setDragBrandId(null);
      return;
    }

    // Optimistic update
    setBrands((prev) =>
      prev.map((b) => (b.id === id ? { ...b, status: stageKey } : b))
    );
    setDragBrandId(null);

    // Persist status change
    await fetchApi(`/brands/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status: stageKey }),
    });

    // Track lifecycle transition
    await fetchApi(`/lifecycle/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({
        from: brand.status,
        to: stageKey,
      }),
    });
  };

  const onDragEnd = () => {
    setDragBrandId(null);
    setDropTarget(null);
  };

  if (loading) {
    return (
      <main className="flex h-full items-center justify-center bg-[var(--surface)]">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex items-center gap-2 text-[var(--text-muted)]"
        >
          <Activity size={16} className="animate-pulse" />
          Loading pipeline...
        </motion.div>
      </main>
    );
  }

  return (
    <main className="flex flex-col h-[calc(100vh-56px)] bg-[var(--surface)]">
      {/* Header + Filters */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="shrink-0 border-b border-[var(--border)] bg-[var(--surface-elevated)] px-6 py-4"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <LayoutList size={18} className="text-blue-400" />
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              Pipeline
            </h1>
            <span className="rounded-full bg-[var(--surface-overlay)] border border-[var(--border)] px-2 py-0.5 text-[11px] font-mono text-[var(--text-muted)]">
              {filtered.length} leads
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { setBulkAction(!bulkAction); if (bulkAction) clearSelection(); }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                bulkAction
                  ? "border-blue-500 bg-blue-500/10 text-blue-400"
                  : "border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)]"
              )}
            >
              <CheckSquare size={13} />
              {bulkAction ? `${selected.size} selected` : "Select"}
            </button>
            <button
              onClick={handleExportCsv}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
            >
              <Download size={13} />
              CSV
            </button>
          </div>
        </div>

        {/* Bulk action bar */}
        <AnimatePresence>
          {bulkAction && selected.size > 0 && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="flex items-center gap-2 mb-3 overflow-hidden"
            >
              <span className="text-xs text-[var(--text-muted)]">{selected.size} selected:</span>
              <select
                onChange={(e) => { if (e.target.value) handleBulkStatus(e.target.value); e.target.value = ""; }}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs text-[var(--text-primary)] focus:outline-none"
                defaultValue=""
              >
                <option value="" disabled>Move to...</option>
                {STAGES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
              <button
                onClick={handleBulkEnrich}
                className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 transition-colors"
              >
                <Zap size={12} /> Enrich
              </button>
              <button onClick={clearSelection} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                <X size={14} />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex items-center gap-3">
          {/* Search */}
          <div className="relative flex-1 max-w-xs">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search leads..."
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] py-1.5 pl-9 pr-3 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500/30 transition-colors"
            />
          </div>

          {/* Tier filter */}
          <div className="relative">
            <Filter
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
            />
            <select
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
              className="appearance-none rounded-lg border border-[var(--border)] bg-[var(--surface)] py-1.5 pl-8 pr-8 text-xs text-[var(--text-primary)] focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500/30 transition-colors cursor-pointer"
            >
              <option value="all">All Tiers</option>
              <option value="hot">Hot</option>
              <option value="warm">Warm</option>
              <option value="watchlist">Watchlist</option>
              <option value="park">Park</option>
            </select>
          </div>
        </div>
      </motion.div>

      {/* Kanban Board */}
      <div
        ref={boardRef}
        className="flex-1 overflow-x-auto overflow-y-hidden"
      >
        <div className="flex gap-4 p-4 h-full min-w-max">
          {STAGES.map((stage, idx) => {
            const cards = columns[stage.key];
            const isOver = dropTarget === stage.key;

            return (
              <motion.div
                key={stage.key}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.04 }}
                className={cn(
                  "flex flex-col w-[280px] shrink-0 rounded-xl border border-t-2 bg-[var(--surface-elevated)] transition-all",
                  stage.borderClass,
                  isOver
                    ? "border-[var(--border-strong)] ring-2 ring-blue-500/20"
                    : "border-[var(--border)]"
                )}
                onDragOver={(e) => onDragOver(e, stage.key)}
                onDragLeave={onDragLeave}
                onDrop={(e) => onDrop(e, stage.key)}
              >
                {/* Column header */}
                <div
                  className={cn(
                    "flex items-center justify-between px-3 py-2.5 rounded-t-xl",
                    stage.bgClass
                  )}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className={cn("h-2 w-2 rounded-full", stage.dotClass)}
                    />
                    <span className="text-xs font-semibold text-[var(--text-primary)]">
                      {stage.label}
                    </span>
                    <span className="rounded-full bg-[var(--surface-overlay)] border border-[var(--border)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-muted)]">
                      {cards.length}
                    </span>
                  </div>
                  <ColumnValue brands={cards} />
                </div>

                {/* Cards */}
                <div className="flex-1 overflow-y-auto px-2 py-2 space-y-2 min-h-0">
                  {cards.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-24 rounded-lg border-2 border-dashed border-[var(--border)] text-[var(--text-muted)] gap-1">
                      <span className="text-xs">No leads</span>
                      <span className="text-[10px]">Drag cards here</span>
                    </div>
                  ) : (
                    cards.map((brand) => (
                      <BrandCard
                        key={brand.id}
                        brand={brand}
                        isDragging={dragBrandId === brand.id}
                        onDragStart={(e) => onDragStart(e, brand.id)}
                        onDragEnd={onDragEnd}
                        onClick={() => setPanelBrandId(brand.id)}
                        selectable={bulkAction}
                        isSelected={selected.has(brand.id)}
                        onSelect={() => toggleSelect(brand.id)}
                      />
                    ))
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>

      {/* Brand detail panel */}
      <BrandPanel
        brandId={panelBrandId}
        onClose={() => setPanelBrandId(null)}
      />
    </main>
  );
}

// --- Sub-components ---

function ColumnValue({ brands }: { brands: Brand[] }) {
  // Sum estimated deal values from scored brands
  const total = brands.reduce((sum, b) => sum + (b.score ?? 0) * 1000, 0);
  if (total === 0) return null;
  return (
    <span className="text-[10px] font-mono text-[var(--text-muted)]">
      {formatINR(total)}
    </span>
  );
}

function BrandCard({
  brand,
  isDragging,
  onDragStart,
  onDragEnd,
  onClick,
  selectable,
  isSelected,
  onSelect,
}: {
  brand: Brand;
  isDragging: boolean;
  onDragStart: (e: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
  onClick: () => void;
  selectable?: boolean;
  isSelected?: boolean;
  onSelect?: () => void;
}) {
  const tc = tierColor(brand.tier);

  return (
    <div
      draggable={!selectable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={selectable ? onSelect : onClick}
      className={cn(
        "group relative rounded-xl border bg-[var(--surface)] p-3 cursor-pointer transition-all hover:border-[var(--border-strong)] hover:shadow-sm",
        isDragging && "opacity-40 ring-2 ring-blue-500/30",
        isSelected ? "border-blue-500 bg-blue-500/5" : "border-[var(--border)]"
      )}
    >
      {/* Drag handle or checkbox */}
      <div className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity">
        {selectable ? (
          <div className={cn(
            "h-4 w-4 rounded border-2 flex items-center justify-center transition-colors",
            isSelected ? "border-blue-500 bg-blue-500" : "border-[var(--border-strong)]"
          )}>
            {isSelected && <CheckSquare size={10} className="text-white" />}
          </div>
        ) : (
          <GripVertical
            size={14}
            className="text-[var(--text-subtle)] cursor-grab active:cursor-grabbing"
          />
        )}
      </div>

      {/* Brand name */}
      <div className="text-sm font-medium text-[var(--text-primary)] truncate pr-6">
        {brand.name}
      </div>

      {/* Domain */}
      {brand.domain && (
        <div className="flex items-center gap-1 mt-1">
          <Globe size={11} className="text-[var(--text-subtle)]" />
          <span className="text-[11px] text-[var(--text-muted)] truncate">
            {brand.domain}
          </span>
        </div>
      )}

      {/* Tier badge + Score */}
      <div className="flex items-center justify-between mt-2.5">
        {brand.tier ? (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize",
              tc.bg,
              tc.text
            )}
          >
            {brand.tier}
          </span>
        ) : (
          <span className="rounded-full bg-neutral-500/10 px-2 py-0.5 text-[10px] text-neutral-500">
            unscored
          </span>
        )}

        {brand.score != null && (
          <span className="font-mono text-xs font-bold text-[var(--text-primary)]">
            {brand.score}
          </span>
        )}
      </div>
    </div>
  );
}
