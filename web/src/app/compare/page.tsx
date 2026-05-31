"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  GitCompare,
  Search,
  Loader2,
  Trophy,
  Building2,
  Target,
  Users,
  AlertTriangle,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

type BrandDossier = {
  brand: { id: number; name: string; domain: string | null; status: string };
  dossier: {
    research?: Record<string, unknown>;
    competitor?: Record<string, unknown>;
    opportunity?: Record<string, unknown>;
    score?: Record<string, unknown>;
  } | null;
};

async function fetchApi<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch { return null; }
}

export default function ComparePage() {
  const [brand1, setBrand1] = useState("");
  const [brand2, setBrand2] = useState("");
  const [data, setData] = useState<[BrandDossier | null, BrandDossier | null]>([null, null]);
  const [loading, setLoading] = useState(false);
  const [availableBrands, setAvailableBrands] = useState<{ id: number; name: string; domain: string | null }[]>([]);

  useEffect(() => {
    fetchApi<{ items: { id: number; name: string; domain: string | null }[] }>("/brands?limit=50")
      .then((r) => setAvailableBrands(r?.items ?? []));
  }, []);

  async function loadBrand(query: string): Promise<BrandDossier | null> {
    const brands = await fetchApi<{ items: { id: number; name: string; domain: string | null; status: string }[] }>(
      `/brands?q=${encodeURIComponent(query)}&limit=1`
    );
    if (!brands?.items?.[0]) return null;
    const b = brands.items[0];
    const versions = await fetchApi<{ items: { id: number }[] }>(`/dossiers/by-brand/${b.id}`);
    let dossier = null;
    if (versions?.items?.[0]) {
      const full = await fetchApi<{ data: Record<string, unknown> }>(`/dossiers/${versions.items[0].id}`);
      dossier = full?.data ?? null;
    }
    return { brand: b, dossier: dossier as BrandDossier["dossier"] };
  }

  async function compare() {
    if (!brand1.trim() || !brand2.trim()) return;
    setLoading(true);
    const [d1, d2] = await Promise.all([loadBrand(brand1), loadBrand(brand2)]);
    setData([d1, d2]);
    setLoading(false);
  }

  return (
    <main className="min-h-screen bg-neutral-950 p-6">
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-6"
      >
        <div className="flex items-center gap-2">
          <GitCompare size={20} className="text-blue-500" />
          <div>
            <h1 className="text-lg font-semibold text-neutral-100">Competitor Comparison</h1>
            <p className="text-xs text-neutral-500">Side-by-side brand intelligence</p>
          </div>
        </div>
      </motion.header>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="mb-6 flex gap-3"
      >
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            value={brand1}
            onChange={(e) => setBrand1(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && compare()}
            placeholder="Brand 1 (name or domain)"
            className="w-full rounded-lg border border-neutral-800 bg-neutral-900 pl-9 pr-3 py-2 text-sm text-neutral-100 outline-none focus:border-blue-600/50 focus:ring-1 focus:ring-blue-600/20 transition-all"
          />
        </div>
        <span className="self-center text-neutral-500 text-xs font-medium">VS</span>
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            value={brand2}
            onChange={(e) => setBrand2(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && compare()}
            placeholder="Brand 2 (name or domain)"
            className="w-full rounded-lg border border-neutral-800 bg-neutral-900 pl-9 pr-3 py-2 text-sm text-neutral-100 outline-none focus:border-blue-600/50 focus:ring-1 focus:ring-blue-600/20 transition-all"
          />
        </div>
        <motion.button
          whileTap={{ scale: 0.95 }}
          onClick={compare}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 transition-colors"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <GitCompare size={14} />}
          {loading ? "Loading..." : "Compare"}
        </motion.button>
      </motion.div>

      {availableBrands.length > 0 && !data[0] && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="mb-6"
        >
          <p className="text-xs text-neutral-500 mb-2">Available brands — click to fill:</p>
          <div className="flex flex-wrap gap-1.5">
            {availableBrands.map((b) => (
              <button
                key={b.id}
                onClick={() => {
                  const q = b.domain || b.name;
                  if (!brand1) setBrand1(q);
                  else if (!brand2) setBrand2(q);
                }}
                className="rounded-md bg-neutral-800 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-700 hover:text-neutral-100 transition-colors"
              >
                {b.name} {b.domain && <span className="text-neutral-500">({b.domain})</span>}
              </button>
            ))}
          </div>
        </motion.div>
      )}

      <AnimatePresence mode="wait">
        {data[0] && data[1] && (
          <motion.div
            key="results"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="grid grid-cols-2 gap-4"
          >
            {data.map((d, idx) => (
              <motion.div
                key={idx}
                initial={{ opacity: 0, x: idx === 0 ? -12 : 12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: idx * 0.1 }}
                className="rounded-lg border border-neutral-800 bg-neutral-900 p-4"
              >
                <h2 className="mb-3 text-base font-semibold text-neutral-100">
                  {d!.brand.name}
                  {d!.brand.domain && <span className="ml-2 text-xs text-neutral-500">{d!.brand.domain}</span>}
                </h2>

                {d!.dossier ? (
                  <>
                    <ScoreSection score={d!.dossier.score} />
                    <CompanySection research={d!.dossier.research} />
                    <OpportunitySection opportunity={d!.dossier.opportunity} />
                    <CompetitorSection competitor={d!.dossier.competitor} />
                  </>
                ) : (
                  <div className="flex flex-col items-center py-8 text-neutral-500">
                    <AlertTriangle size={24} className="mb-2 text-neutral-600" />
                    <p className="text-sm">No dossier available. Trigger research first.</p>
                  </div>
                )}
              </motion.div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {(data[0] && !data[1]) && (
        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-1.5 text-sm text-amber-400">
          <AlertTriangle size={14} /> Brand 2 not found.
        </motion.p>
      )}
      {(!data[0] && data[1]) && (
        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-1.5 text-sm text-amber-400">
          <AlertTriangle size={14} /> Brand 1 not found.
        </motion.p>
      )}
      {!loading && !data[0] && !data[1] && brand1 && brand2 && (
        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-sm text-neutral-500">
          Neither brand found.
        </motion.p>
      )}
    </main>
  );
}

function ScoreSection({ score }: { score?: Record<string, unknown> }) {
  if (!score) return null;
  const total = Number(score.total ?? 0);
  const tier = String(score.tier ?? "unknown");
  const confidence = Number(score.confidence ?? 0);
  const why = (score.why as string[]) ?? [];

  return (
    <div className="mb-4">
      <h3 className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-neutral-500">
        <Trophy size={12} />
        Score
      </h3>
      <div className="mt-1 flex items-center gap-3">
        <span className="text-3xl font-bold text-neutral-100 tabular-nums">{total}</span>
        <span className={`rounded-full px-2 py-0.5 text-xs ${tierColor(tier)}`}>{tier}</span>
        <span className="text-xs text-neutral-500">{Math.round(confidence * 100)}% confidence</span>
      </div>
      {why.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-neutral-400">
          {why.map((w, i) => <li key={i} className="flex items-start gap-1"><span className="text-neutral-600 mt-0.5">-</span> {w}</li>)}
        </ul>
      )}
    </div>
  );
}

function CompanySection({ research }: { research?: Record<string, unknown> }) {
  if (!research) return null;
  const company = research.company as Record<string, unknown> ?? {};
  const positioning = research.positioning as Record<string, unknown> ?? {};
  const digital = research.digital_footprint as Record<string, unknown> ?? {};

  return (
    <div className="mb-4">
      <h3 className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-neutral-500">
        <Building2 size={12} />
        Company
      </h3>
      <div className="mt-1 space-y-1 text-sm text-neutral-300">
        {company.revenue_band ? <div>Revenue: {String(company.revenue_band)}</div> : null}
        {company.employees_est ? <div>Employees: ~{String(company.employees_est)}</div> : null}
        {positioning.category ? <div>Category: {String(positioning.category)}</div> : null}
        {digital.channels_active ? <div>Channels: {(digital.channels_active as string[]).join(", ")}</div> : null}
      </div>
    </div>
  );
}

function OpportunitySection({ opportunity }: { opportunity?: Record<string, unknown> }) {
  if (!opportunity) return null;
  const services = (opportunity.services_recommended as Record<string, unknown>[]) ?? [];

  return (
    <div className="mb-4">
      <h3 className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-neutral-500">
        <Target size={12} />
        Opportunities
      </h3>
      <div className="mt-1 space-y-1">
        {services.map((s, i) => (
          <div key={i} className="rounded-lg bg-neutral-800 px-2.5 py-1.5 text-xs">
            <span className="text-blue-400">{String(s.service)}</span>
            <span className="ml-2 text-neutral-400">{String(s.rationale ?? "").slice(0, 100)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CompetitorSection({ competitor }: { competitor?: Record<string, unknown> }) {
  if (!competitor) return null;
  const competitors = (competitor.competitors as Record<string, unknown>[]) ?? [];
  const gapMap = competitor.gap_map as Record<string, string> ?? {};

  return (
    <div className="mb-4">
      <h3 className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-neutral-500">
        <Users size={12} />
        Competitors
      </h3>
      <div className="mt-1 space-y-1 text-xs text-neutral-400">
        {competitors.map((c, i) => (
          <div key={i}>{String(c.name)} {c.domain ? `(${c.domain})` : ""}</div>
        ))}
      </div>
      {Object.keys(gapMap).length > 0 && (
        <div className="mt-2">
          <div className="text-xs text-neutral-500">Gaps:</div>
          {Object.entries(gapMap).map(([service, rationale]) => (
            <div key={service} className="text-xs"><span className="text-amber-400">{service}</span>: {rationale}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function tierColor(tier: string): string {
  switch (tier?.toLowerCase()) {
    case "hot": return "bg-rose-600/20 text-rose-400";
    case "warm": return "bg-amber-600/20 text-amber-400";
    case "watchlist": return "bg-blue-600/20 text-blue-400";
    default: return "bg-neutral-700/20 text-neutral-400";
  }
}
