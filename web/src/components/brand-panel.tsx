"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Building2,
  Globe,
  Users,
  DollarSign,
  Target,
  Shield,
  TrendingUp,
  Mail,
  Award,
  AlertTriangle,
  Zap,
  BarChart3,
  Calendar,
  Landmark,
  Loader2,
  ExternalLink,
  ArrowLeft,
  Play,
  Radio,
  CheckCircle2,
  XCircle,
  Clock,
  Search,
  Brain,
  FileText,
  Phone,
  Linkedin,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { ScoreChart } from "./score-chart";
import { cn, formatINR, tierColor } from "@/lib/utils";
import type {
  Brand,
  DossierData,
  DossierResearch,
  DossierCompany,
  DossierPositioning,
  DigitalFootprint,
  FundingRound,
  DossierCompetitor,
  Competitor,
  DossierOpportunity,
  ServiceRecommendation,
  DossierScore,
  ScoreBreakdown,
  ServiceGap,
  DossierOutreach,
  SignalEvidence,
  CollectorFinding,
} from "@/lib/types";

function mergeSignalEvidence(live: SignalEvidence, dossier: SignalEvidence): SignalEvidence {
  const merged = { ...dossier };
  for (const [source, findings] of Object.entries(live)) {
    if (!merged[source] || findings.length > 0) {
      merged[source] = findings;
    }
  }
  return merged;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

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

async function postApi<T>(path: string, body: Record<string, unknown>): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

type Trace = {
  id: string;
  workflow_id: string;
  brand_id: number | null;
  agent: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  nodes_visited: string[];
  total_cost_cents: number;
  brand_name?: string;
};

type BrandPanelProps = {
  brandId: number | null;
  onClose: () => void;
};

type BrandDetailProps = {
  brandId: number;
  fullPage?: boolean;
};

const AGENT_ICONS: Record<string, typeof Search> = {
  research: Search,
  competitor: Shield,
  opportunity: Zap,
  score: Award,
  outreach: Mail,
  enrichment: Brain,
};

const STATUS_STYLES: Record<string, { color: string; icon: typeof Radio }> = {
  running: { color: "text-blue-400", icon: Radio },
  completed: { color: "text-emerald-400", icon: CheckCircle2 },
  failed: { color: "text-rose-400", icon: XCircle },
  pending: { color: "text-[var(--text-muted)]", icon: Clock },
};

type DossierTab = "overview" | "competitors" | "opportunity" | "score" | "people" | "timeline" | "outreach";

const TABS: { key: DossierTab; label: string; icon: typeof Globe }[] = [
  { key: "overview", label: "Overview", icon: Building2 },
  { key: "score", label: "Score", icon: Award },
  { key: "competitors", label: "Competitors", icon: Shield },
  { key: "opportunity", label: "Opportunity", icon: Zap },
  { key: "people", label: "People", icon: Users },
  { key: "timeline", label: "Timeline", icon: Clock },
  { key: "outreach", label: "Outreach", icon: Mail },
];

export function BrandDetail({ brandId, fullPage }: BrandDetailProps) {
  const [loading, setLoading] = useState(false);
  const [brand, setBrand] = useState<Brand | null>(null);
  const [dossier, setDossier] = useState<DossierData | null>(null);
  const [liveSignals, setLiveSignals] = useState<SignalEvidence>({});
  const [traces, setTraces] = useState<Trace[]>([]);
  const [generating, setGenerating] = useState(false);
  const [triggerStatus, setTriggerStatus] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadBrand = useCallback(async (id: number) => {
    const data = await fetchApi<{ brand: Brand; latest_dossier: { id?: number; data?: DossierData } | null }>(
      `/brands/${id}`
    );
    if (!data) return;
    setBrand(data.brand);
    if (data.latest_dossier?.id) {
      const fd = await fetchApi<{ data: DossierData }>(`/dossiers/${data.latest_dossier.id}`);
      setDossier(fd?.data ?? data.latest_dossier?.data ?? null);
    } else {
      setDossier(data.latest_dossier?.data ?? null);
    }
  }, []);

  const loadSignals = useCallback(async (id: number) => {
    const data = await fetchApi<SignalEvidence>(`/brands/${id}/signals`);
    if (data) setLiveSignals(data);
  }, []);

  const loadTraces = useCallback(async (id: number) => {
    const data = await fetchApi<{ items: Trace[] }>(`/traces?brand_id=${id}&limit=20`);
    if (data?.items) setTraces(data.items);
  }, []);

  useEffect(() => {
    setLoading(true);
    setBrand(null);
    setDossier(null);
    setLiveSignals({});
    setTraces([]);
    setTriggerStatus(null);
    setGenerating(false);

    (async () => {
      await Promise.all([loadBrand(brandId), loadSignals(brandId), loadTraces(brandId)]);
      setLoading(false);
    })();
  }, [brandId, loadBrand, loadSignals, loadTraces]);

  useEffect(() => {
    const hasRunning = generating || traces.some((t) => t.status === "running");
    const interval = hasRunning ? 4000 : 15000;
    pollRef.current = setInterval(async () => {
      await Promise.all([loadBrand(brandId), loadSignals(brandId), loadTraces(brandId)]);
      if (hasRunning) {
        const fresh = await fetchApi<{ items: Trace[] }>(`/traces?brand_id=${brandId}&limit=5`);
        if (fresh?.items && !fresh.items.some((t) => t.status === "running")) {
          setGenerating(false);
        }
      }
    }, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [brandId, generating, traces, loadBrand, loadSignals, loadTraces]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        loadBrand(brandId);
        loadSignals(brandId);
        loadTraces(brandId);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [brandId, loadBrand, loadSignals, loadTraces]);

  const triggerDossier = async () => {
    if (generating) return;
    setGenerating(true);
    setTriggerStatus("Starting dossier generation...");
    const result = await postApi<{ workflow_id: string; run_id: string }>("/workflows/dossier", {
      brand_id: brandId,
      reason: "Manual trigger from brand panel",
    });
    if (result) {
      setTriggerStatus(`Workflow started: ${result.workflow_id}`);
    } else {
      setTriggerStatus("Failed to trigger — check API logs");
      setGenerating(false);
    }
  };

  const hasRunningTrace = traces.some((t) => t.status === "running");
  const tc = tierColor(brand?.tier);

  return (
    <div className={cn("flex flex-col", fullPage ? "h-full" : "h-full")}>
      {/* Header */}
      <div className={cn(
        "flex items-center justify-between border-b border-[var(--border)] shrink-0",
        fullPage ? "px-6 py-4" : "px-5 py-4"
      )}>
        <div className="min-w-0 flex-1">
          {brand ? (
            <>
              <div className="flex items-center gap-2.5">
                {brand.domain && (
                  <img
                    src={`https://www.google.com/s2/favicons?domain=${brand.domain}&sz=32`}
                    alt=""
                    className="h-6 w-6 rounded shrink-0"
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                  />
                )}
                <h2 className={cn(
                  "font-semibold text-[var(--text-primary)] truncate",
                  fullPage ? "text-xl" : "text-base"
                )}>{brand.name}</h2>
                {brand.tier && (
                  <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide", tc.bg, tc.text)}>
                    {brand.tier}
                  </span>
                )}
                {hasRunningTrace && (
                  <span className="flex items-center gap-1 rounded-full bg-blue-600/20 px-2 py-0.5 text-[11px] text-blue-400">
                    <Radio size={10} className="animate-pulse" /> Processing
                  </span>
                )}
              </div>
              <p className="text-xs text-[var(--text-muted)] truncate mt-0.5">
                {brand.domain ? (
                  <a href={`https://${brand.domain}`} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:text-blue-300">{brand.domain}</a>
                ) : "—"} · <span className="capitalize">{brand.status}</span>
              </p>
            </>
          ) : (
            <h2 className="text-base font-semibold text-[var(--text-muted)]">Loading...</h2>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-3">
          {fullPage && (
            <a
              href="/"
              className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
            >
              <ArrowLeft size={12} />
              Back to workspace
            </a>
          )}
        </div>
      </div>

      {/* Body */}
      <div className={cn(
        "flex-1 overflow-y-auto space-y-4",
        fullPage ? "px-6 py-5 max-w-4xl mx-auto w-full" : "px-5 py-4"
      )}>
        {loading && (
          <div className="flex items-center justify-center py-20 text-[var(--text-muted)]">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading brand data...
          </div>
        )}

        {!loading && !dossier && brand && (
          <NoDossierView
            brand={brand}
            traces={traces}
            generating={generating}
            triggerStatus={triggerStatus}
            onGenerate={triggerDossier}
            signalEvidence={liveSignals}
          />
        )}

        {!loading && dossier && brand && (
          <>
            <DossierView data={dossier} brand={brand} liveSignals={liveSignals} />
            {traces.length > 0 && <ActivityFeed traces={traces} />}
          </>
        )}
      </div>
    </div>
  );
}

export function BrandPanel({ brandId, onClose }: BrandPanelProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <AnimatePresence>
      {brandId !== null && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-40 bg-black/50"
            onClick={onClose}
          />
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            className="fixed top-0 right-0 z-50 h-full w-full max-w-2xl border-l border-[var(--border)] bg-[var(--surface)] shadow-2xl flex flex-col"
          >
            <BrandDetail brandId={brandId} />
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function NoDossierView({
  brand,
  traces,
  generating,
  triggerStatus,
  onGenerate,
  signalEvidence,
}: {
  brand: Brand;
  traces: Trace[];
  generating: boolean;
  triggerStatus: string | null;
  onGenerate: () => void;
  signalEvidence: SignalEvidence;
}) {
  return (
    <>
      <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-6 text-center">
        {generating ? (
          <>
            <Loader2 size={32} className="mx-auto mb-3 text-blue-400 animate-spin" />
            <p className="text-sm text-blue-400 font-medium">Generating dossier...</p>
            <p className="text-xs text-[var(--text-muted)] mt-1">{triggerStatus}</p>
            <p className="text-[11px] text-[var(--text-subtle)] mt-2">Live activity shown below. Auto-refreshing every 4s.</p>
          </>
        ) : (
          <>
            <FileText size={32} className="mx-auto mb-3 text-[var(--text-subtle)]" />
            <p className="text-[var(--text-secondary)] text-sm font-medium">No dossier yet</p>
            <p className="text-xs text-[var(--text-muted)] mt-1 mb-5">
              Generate a full research dossier for <span className="text-[var(--text-primary)]">{brand.name}</span>
            </p>
            <button
              onClick={onGenerate}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-500 transition-colors shadow-sm shadow-blue-600/20"
            >
              <Play size={14} />
              Generate Dossier
            </button>
            <p className="text-[11px] text-[var(--text-subtle)] mt-3">
              Runs research → competitor analysis → scoring → outreach
            </p>
          </>
        )}
      </div>

      <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
        <h3 className="flex items-center gap-2 text-xs font-medium text-[var(--text-secondary)] mb-3">
          <Building2 size={14} className="text-[var(--text-muted)]" /> Known Info
        </h3>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div><span className="text-[var(--text-muted)]">Name:</span> <span className="text-[var(--text-primary)]">{brand.name}</span></div>
          <div><span className="text-[var(--text-muted)]">Domain:</span> <span className="text-[var(--text-primary)]">{brand.domain || "—"}</span></div>
          <div><span className="text-[var(--text-muted)]">Status:</span> <span className="text-[var(--text-primary)]">{brand.status}</span></div>
          <div><span className="text-[var(--text-muted)]">Tier:</span> <span className="text-[var(--text-primary)]">{brand.tier || "—"}</span></div>
          {brand.score != null && (
            <div><span className="text-[var(--text-muted)]">Score:</span> <span className="text-[var(--text-primary)]">{brand.score}</span></div>
          )}
          {brand.diagnosis && (
            <div className="col-span-2"><span className="text-[var(--text-muted)]">Diagnosis:</span> <span className="text-[var(--text-primary)]">{brand.diagnosis}</span></div>
          )}
        </div>
      </div>

      {Object.keys(signalEvidence).length > 0 && (
        <ProofPointsSection signalEvidence={signalEvidence} />
      )}

      {traces.length > 0 && <ActivityFeed traces={traces} />}
    </>
  );
}

function DossierView({ data, brand, liveSignals }: { data: DossierData; brand: Brand; liveSignals: SignalEvidence }) {
  const [tab, setTab] = useState<DossierTab>("overview");

  const research = data.research ?? ({} as DossierResearch);
  const company = research.company ?? ({} as DossierCompany);
  const positioning = research.positioning ?? ({} as DossierPositioning);
  const digital = research.digital_footprint ?? ({} as DigitalFootprint);
  const fundingHistory = research.funding_history ?? [];
  const competitorData = data.competitor ?? ({} as DossierCompetitor);
  const competitors = competitorData.competitors ?? [];
  const gaps = competitorData.gap_map ?? {};
  const opportunity = data.opportunity ?? ({} as DossierOpportunity);
  const services = opportunity.services_recommended ?? [];
  const score = data.score ?? ({} as DossierScore);
  const breakdown = score.breakdown ?? {};
  const outreach = data.outreach ?? ({} as DossierOutreach);
  const dossierEvidence = data.signal_evidence ?? ({} as SignalEvidence);
  const signalEvidence = mergeSignalEvidence(liveSignals, dossierEvidence);

  const tierStyles: Record<string, string> = {
    hot: "bg-rose-600/15 border-rose-500/30",
    warm: "bg-amber-600/15 border-amber-500/30",
    watchlist: "bg-blue-600/15 border-blue-500/30",
    park: "bg-[var(--surface-elevated)] border-[var(--border)]",
  };
  const tierBanner = tierStyles[score.tier?.toLowerCase() ?? ""] ?? tierStyles.park;
  const tc = tierColor(score.tier);

  return (
    <>
      {/* Score banner */}
      <div className={cn("flex items-center justify-between rounded-xl border px-5 py-4", tierBanner)}>
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--text-primary)]">
            {company.legal_name || company.brand_name || brand.name}
          </div>
          <div className="text-xs text-[var(--text-muted)] mt-0.5">
            {company.hq || "—"} · {company.funding_stage || "—"}
            {score.confidence != null && (
              <span className="ml-2 text-[var(--text-subtle)]">· {(score.confidence * 100).toFixed(0)}% confidence</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 mt-1.5 text-xs">
            {score.estimated_deal_value_inr != null && (
              <span>
                <span className="text-[var(--text-muted)]">Est. deal:</span>{" "}
                <span className="font-semibold text-emerald-400">{formatINR(score.estimated_deal_value_inr)}</span>
              </span>
            )}
            {score.predicted_conversion_probability != null && (
              <span>
                <span className="text-[var(--text-muted)]">Conversion:</span>{" "}
                <span className="font-semibold text-blue-400">{(score.predicted_conversion_probability * 100).toFixed(0)}%</span>
              </span>
            )}
          </div>
          {score.confidence_explanation && (
            <p className="text-[10px] text-[var(--text-subtle)] mt-1 line-clamp-2">{score.confidence_explanation}</p>
          )}
        </div>
        <div className="text-center shrink-0 ml-4">
          <div className={cn("text-3xl font-bold tabular-nums", tc.text)}>{score.total ?? "—"}</div>
          <div className={cn("text-[11px] font-semibold uppercase tracking-wider mt-0.5", tc.text)}>{score.tier ?? "—"}</div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl bg-[var(--surface-elevated)] border border-[var(--border)] p-1">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "flex-1 flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-all",
                active
                  ? "bg-[var(--surface-overlay)] text-[var(--text-primary)] shadow-sm"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--surface-overlay)]/50"
              )}
            >
              <Icon size={13} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.15 }}
          className="space-y-4"
        >
          {tab === "overview" && (
            <OverviewTab
              company={company}
              positioning={positioning}
              digital={digital}
              fundingHistory={fundingHistory}
              score={score}
              breakdown={breakdown}
              signalEvidence={signalEvidence}
            />
          )}
          {tab === "competitors" && (
            <CompetitorsTab competitors={competitors} gaps={gaps} />
          )}
          {tab === "score" && (
            <ScoreChart score={score} />
          )}
          {tab === "opportunity" && (
            <OpportunityTab opportunity={opportunity} services={services} score={score} signalEvidence={signalEvidence} />
          )}
          {tab === "people" && (
            <PeopleTab brandId={brand.id} />
          )}
          {tab === "timeline" && (
            <TimelineTab brandId={brand.id} />
          )}
          {tab === "outreach" && (
            <OutreachTab outreach={outreach} />
          )}
        </motion.div>
      </AnimatePresence>
    </>
  );
}

/* ── Tab: Overview ── */

function OverviewTab({
  company,
  positioning,
  digital,
  fundingHistory,
  score,
  breakdown,
  signalEvidence,
}: {
  company: DossierCompany;
  positioning: DossierPositioning;
  digital: DigitalFootprint;
  fundingHistory: FundingRound[];
  score: DossierScore;
  breakdown: Record<string, ScoreBreakdown>;
  signalEvidence: SignalEvidence;
}) {
  const [fundingOpen, setFundingOpen] = useState(false);

  return (
    <>
      {/* Quick stats */}
      <div className="grid grid-cols-4 gap-2">
        <MiniCard icon={Building2} label="HQ" value={company.hq || "—"} />
        <MiniCard icon={Users} label="Employees" value={company.employees_est ?? "—"} />
        <MiniCard icon={DollarSign} label="Revenue" value={company.revenue_band || "—"} />
        <MiniCard icon={TrendingUp} label="Founded" value={company.founded_year ?? "—"} />
      </div>

      {/* Positioning */}
      <Section icon={Target} label="Positioning" color="text-blue-400">
        <div className="grid gap-2 md:grid-cols-2 text-xs">
          <KV label="Category" value={positioning.category} />
          <KV label="Audience" value={positioning.audience} />
          <KV label="Price Band" value={positioning.price_band} />
          <KV label="Sub-category" value={positioning.sub_category} />
          <div className="md:col-span-2">
            <KV label="USP" value={positioning.USP_summary} />
          </div>
        </div>
        {(positioning.audience_segments ?? []).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {positioning.audience_segments!.map((seg, i) => (
              <span key={i} className="rounded-md bg-blue-500/10 px-2 py-1 text-[11px] text-blue-400">{seg}</span>
            ))}
          </div>
        )}
      </Section>

      {/* Digital footprint */}
      <Section icon={Globe} label="Digital Footprint" color="text-emerald-400">
        <div className="grid grid-cols-4 gap-2">
          {(
            [
              ["Web Perf", digital.web_perf_signal],
              ["SEO", digital.seo_signal],
              ["Social", digital.social_signal],
              ["Paid", digital.paid_signal],
              ["Email", digital.email_signal],
              ["Content", digital.content_maturity],
              ["Programmatic", digital.programmatic_signal],
            ] as [string, string | null | undefined][]
          ).map(([label, val]) => (
            <div key={label} className="flex items-center justify-between rounded-lg bg-[var(--surface-overlay)] px-3 py-2">
              <span className="text-[11px] text-[var(--text-secondary)]">{label}</span>
              <SignalBadge value={val} />
            </div>
          ))}
        </div>
        {(digital.channels_active ?? []).length > 0 && (
          <div className="mt-3">
            <p className="text-[11px] text-[var(--text-muted)] mb-1.5">Active channels</p>
            <div className="flex flex-wrap gap-1.5">
              {digital.channels_active!.map((ch, i) => (
                <span key={i} className="rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-400">{ch}</span>
              ))}
            </div>
          </div>
        )}
        {(digital.martech_stack ?? []).length > 0 && (
          <div className="mt-3">
            <p className="text-[11px] text-[var(--text-muted)] mb-1.5">Martech stack</p>
            <div className="flex flex-wrap gap-1.5">
              {digital.martech_stack!.map((t, i) => (
                <span key={i} className="rounded-md bg-[var(--surface-overlay)] border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--text-secondary)]">{t}</span>
              ))}
            </div>
          </div>
        )}
      </Section>

      {/* Proof Points — actual collector metrics */}
      {Object.keys(signalEvidence).length > 0 && (
        <ProofPointsSection signalEvidence={signalEvidence} />
      )}

      {/* Score breakdown */}
      {Object.keys(breakdown).length > 0 && (
        <Section icon={Award} label={`Score Breakdown ${score.total ?? 0}/100`} color="text-blue-400">
          <div className="grid gap-2 md:grid-cols-2">
            {Object.entries(breakdown).map(([factor, info]) => (
              <div key={factor} className="rounded-lg bg-[var(--surface-overlay)] px-3 py-2.5">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[11px] text-[var(--text-secondary)] capitalize">{factor.replace(/_/g, " ")}</span>
                  <span className="text-[11px] font-mono font-semibold text-[var(--text-primary)]">{((info.score ?? 0) * 100).toFixed(0)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${(info.score ?? 0) * 100}%` }} />
                </div>
                {info.evidence && (
                  <p className="text-[11px] text-[var(--text-muted)] mt-1.5 line-clamp-2">{info.evidence}</p>
                )}
              </div>
            ))}
          </div>
          {(score.why ?? []).length > 0 && (
            <div className="mt-3 space-y-1">
              {score.why!.map((w, i) => (
                <p key={i} className="text-[11px] text-[var(--text-secondary)] pl-3 border-l-2 border-blue-500/30">{w}</p>
              ))}
            </div>
          )}
          {(score.red_flags ?? []).length > 0 && (
            <div className="mt-3 space-y-1">
              <p className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-400">
                <AlertTriangle size={12} /> Red Flags
              </p>
              {score.red_flags!.map((f, i) => (
                <p key={i} className="text-[11px] text-amber-400/80 pl-3">{f}</p>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* Funding — collapsible */}
      {fundingHistory.length > 0 && (
        <Section icon={Landmark} label={`Funding (${fundingHistory.length} rounds)`} color="text-emerald-400">
          <button
            onClick={() => setFundingOpen(!fundingOpen)}
            className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors mb-2"
          >
            {fundingOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {fundingOpen ? "Collapse" : "Show funding timeline"}
          </button>
          <AnimatePresence>
            {fundingOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="relative">
                  <div className="absolute left-[7px] top-2 bottom-2 w-px bg-[var(--border)]" />
                  <div className="space-y-2">
                    {fundingHistory.map((round, i) => (
                      <div key={i} className="relative pl-6">
                        <div className="absolute left-0 top-1.5 w-[15px] h-[15px] rounded-full border-2 border-emerald-500 bg-[var(--surface)] z-10" />
                        <div className="rounded-lg bg-[var(--surface-overlay)] px-3 py-2.5">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="rounded-md bg-emerald-600/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-400">
                              {round.round || "?"}
                            </span>
                            <span className="text-sm font-bold text-[var(--text-primary)]">{round.amount || "Undisclosed"}</span>
                            {round.date && (
                              <span className="flex items-center gap-1 text-[11px] text-[var(--text-muted)] ml-auto">
                                <Calendar size={10} /> {round.date}
                              </span>
                            )}
                          </div>
                          {(round.investors ?? []).length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {round.investors!.map((inv, j) => (
                                <span key={j} className="rounded-md bg-[var(--surface-elevated)] border border-[var(--border)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]">{inv}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </Section>
      )}
    </>
  );
}

/* ── Tab: Competitors ── */

function CompetitorsTab({
  competitors,
  gaps,
}: {
  competitors: Competitor[];
  gaps: Record<string, string>;
}) {
  if (competitors.length === 0 && Object.keys(gaps).length === 0) {
    return <EmptyTab message="No competitor data available" />;
  }

  return (
    <>
      {competitors.length > 0 && (
        <Section icon={Shield} label={`Competitors (${competitors.length})`} color="text-amber-400">
          <div className="space-y-2">
            {competitors.map((c, i) => (
              <div key={i} className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)]">{c.name}</span>
                      {c.domain && (
                        <span className="text-[11px] text-[var(--text-muted)]">{c.domain}</span>
                      )}
                    </div>
                    {c.positioning && (
                      <p className="text-[11px] text-[var(--text-secondary)] mt-1">{c.positioning}</p>
                    )}
                  </div>
                  <ThreatBadge level={c.threat_level} />
                </div>
                {c.strengths && (
                  <p className="text-[11px] text-[var(--text-muted)] mt-2 pt-2 border-t border-[var(--border)]">{c.strengths}</p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {Object.keys(gaps).length > 0 && (
        <Section icon={BarChart3} label="Service Gaps" color="text-purple-400">
          <div className="space-y-2">
            {Object.entries(gaps).map(([service, reason]) => (
              <div key={service} className="flex items-start gap-3 rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
                <span className="shrink-0 rounded-md bg-purple-500/15 px-2 py-1 text-[11px] font-semibold text-purple-400">{service}</span>
                <span className="text-[11px] text-[var(--text-secondary)] leading-relaxed">{reason}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

/* ── Tab: Opportunity ── */

function OpportunityTab({
  opportunity,
  services,
  score,
  signalEvidence,
}: {
  opportunity: DossierOpportunity;
  services: ServiceRecommendation[];
  score: DossierScore;
  signalEvidence: SignalEvidence;
}) {
  const serviceGaps = score.service_gaps ?? {};
  const topServices = score.top_services ?? [];
  const sortedGaps = Object.entries(serviceGaps).sort(([, a], [, b]) => (b.score ?? 0) - (a.score ?? 0));
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({});

  const toggleEvidence = (name: string) => {
    setExpandedEvidence((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  return (
    <>
      {/* Top services banner */}
      {topServices.length > 0 && (
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/20 px-4 py-3">
          <p className="text-[11px] text-[var(--text-muted)] mb-2">Top Service Opportunities</p>
          <div className="flex flex-wrap gap-2">
            {topServices.map((svc, i) => (
              <span key={i} className="rounded-lg bg-emerald-600/15 border border-emerald-500/20 px-3 py-1.5 text-xs font-medium text-emerald-400">
                {svc.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Diagnosis */}
      <Section icon={Zap} label="Diagnosis" color="text-yellow-400">
        <p className="text-xs text-[var(--text-primary)] leading-relaxed">{opportunity.diagnosis || "—"}</p>
      </Section>

      {/* Per-service opportunity scores with evidence */}
      {sortedGaps.length > 0 && (
        <Section icon={BarChart3} label={`Service Opportunity Scores (${sortedGaps.length})`} color="text-purple-400">
          <div className="space-y-2">
            {sortedGaps.map(([name, gap]) => {
              const s = gap.score ?? 0;
              const barColor =
                s >= 70 ? "bg-emerald-500" :
                s >= 40 ? "bg-amber-500" :
                "bg-[var(--text-subtle)]";
              const priorityColor =
                gap.priority === "high" ? "text-rose-400 bg-rose-600/15 border-rose-500/20" :
                gap.priority === "medium" ? "text-amber-400 bg-amber-600/15 border-amber-500/20" :
                "text-[var(--text-muted)] bg-[var(--surface)] border-[var(--border)]";
              const evidence = getServiceEvidence(name, signalEvidence);
              const isExpanded = expandedEvidence[name] ?? false;
              return (
                <div key={name} className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-[var(--text-primary)] capitalize">{name.replace(/_/g, " ")}</span>
                    <div className="flex items-center gap-2">
                      {gap.priority && (
                        <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium", priorityColor)}>
                          {gap.priority}
                        </span>
                      )}
                      <span className="text-[11px] font-mono font-semibold text-[var(--text-primary)]">{s}/100</span>
                    </div>
                  </div>
                  <div className="h-1.5 rounded-full bg-[var(--surface-sunken)] overflow-hidden mb-1.5">
                    <div className={cn("h-full rounded-full transition-all", barColor)} style={{ width: `${s}%` }} />
                  </div>
                  {gap.evidence && (
                    <p className="text-[11px] text-[var(--text-muted)] line-clamp-2">{gap.evidence}</p>
                  )}
                  {/* Collector evidence toggle */}
                  {evidence.length > 0 && (
                    <div className="mt-2">
                      <button
                        onClick={() => toggleEvidence(name)}
                        className="flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300 transition-colors"
                      >
                        {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                        {isExpanded ? "Hide" : "Show"} collector evidence ({evidence.length})
                      </button>
                      <AnimatePresence>
                        {isExpanded && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2 }}
                            className="overflow-hidden"
                          >
                            <div className="mt-2 space-y-1.5">
                              {evidence.map((ev, i) => (
                                <EvidenceCard key={i} item={ev} />
                              ))}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Raw signal evidence for services NOT in service_gaps */}
      {Object.keys(signalEvidence).length > 0 && (
        <SignalEvidenceSection signalEvidence={signalEvidence} serviceGaps={serviceGaps} />
      )}

      {/* Services recommended */}
      {services.length > 0 && (
        <Section icon={Target} label={`Services Recommended (${services.length})`} color="text-emerald-400">
          <div className="space-y-2">
            {services.map((svc, i) => {
              const conf = typeof svc.confidence === "number" ? svc.confidence : parseFloat(svc.confidence ?? "0");
              const confPct = conf > 1 ? conf : conf * 100;
              return (
                <div key={i} className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{svc.service}</span>
                    <span className="text-[11px] text-emerald-400 shrink-0 ml-2">{svc.estimated_impact}</span>
                  </div>
                  <p className="text-[11px] text-[var(--text-secondary)] mt-1">{svc.rationale}</p>
                  {confPct > 0 && (
                    <div className="mt-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] text-[var(--text-muted)]">Confidence</span>
                        <span className="text-[11px] font-mono text-[var(--text-secondary)]">{confPct.toFixed(0)}%</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-[var(--surface-sunken)] overflow-hidden">
                        <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${confPct}%` }} />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Deal size + urgency */}
      <div className="grid gap-2 md:grid-cols-2">
        {opportunity.estimated_deal_size_inr != null && (
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/20 px-4 py-3">
            <p className="text-[11px] text-[var(--text-muted)] mb-1">Estimated Deal Size</p>
            <p className="text-lg font-bold text-emerald-400">{formatINR(opportunity.estimated_deal_size_inr)}</p>
          </div>
        )}
        {(opportunity.urgency_factors ?? []).length > 0 && (
          <div className="rounded-xl border border-amber-500/20 bg-amber-950/20 px-4 py-3">
            <p className="text-[11px] text-[var(--text-muted)] mb-2">Urgency Factors</p>
            <ul className="space-y-1">
              {opportunity.urgency_factors!.map((f, i) => (
                <li key={i} className="text-[11px] text-amber-400 flex items-start gap-1.5">
                  <AlertTriangle size={10} className="mt-0.5 shrink-0" /> {f}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Top weaknesses */}
      {(opportunity.top_3_weaknesses ?? []).length > 0 && (
        <Section icon={AlertTriangle} label="Top Weaknesses" color="text-rose-400">
          <ul className="space-y-1.5">
            {opportunity.top_3_weaknesses!.map((w, i) => (
              <li key={i} className="text-xs text-[var(--text-secondary)] pl-3 border-l-2 border-rose-500/30">{w}</li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

/* ── Service-to-collector mapping ── */

const SERVICE_COLLECTOR_MAP: Record<string, string[]> = {
  affiliate: ["affiliate_program"],
  influencer: ["social_presence"],
  content: ["content_blog"],
  seo: ["seo_rank_tracking", "tech_stack", "pagespeed"],
  social_media: ["social_presence", "youtube_social"],
  performance_marketing: ["google_ads_transparency", "meta_ad_library"],
  email_marketing: ["email_maturity"],
  web_design: ["pagespeed", "tech_stack"],
  aso: ["app_store", "ios_app_store"],
  programmatic: ["ads_txt", "google_ads_transparency"],
  branding: ["google_trends", "news_polling"],
  hiring: ["linkedin_jobs"],
  security: ["cert_transparency", "shodan_internetdb", "urlscan_search"],
  domain: ["rdap_whois"],
  funding: ["crunchbase_funding", "tracxn_funding", "tofler_company"],
};

const COLLECTOR_LABELS: Record<string, string> = {
  affiliate_program: "Affiliate Program Scanner",
  social_presence: "Social Presence Detector",
  content_blog: "Blog & Content Analyzer",
  seo_rank_tracking: "SEO Rank Tracker",
  tech_stack: "Tech Stack Detector",
  pagespeed: "PageSpeed Analyzer",
  google_ads_transparency: "Google Ads Transparency",
  meta_ad_library: "Meta Ad Library",
  email_maturity: "Email Infrastructure Check",
  youtube_social: "YouTube Social Analyzer",
  app_store: "Play Store Analyzer",
  ios_app_store: "iOS App Store Analyzer",
  ads_txt: "Ads.txt Scanner",
  linkedin_jobs: "LinkedIn Jobs Monitor",
  news_polling: "News Monitor",
  cert_transparency: "SSL Certificate Monitor",
  rdap_whois: "Domain WHOIS Lookup",
  shodan_internetdb: "Shodan Infrastructure Scan",
  ipinfo_geo: "IP Geolocation",
  urlscan_search: "URL Security Scanner",
  google_trends: "Google Trends Monitor",
  common_crawl: "Common Crawl Index",
  hn_discovery: "Hacker News Traction",
  open_food_facts: "Open Food Facts",
  crunchbase_funding: "Crunchbase Funding",
  tracxn_funding: "Tracxn Funding",
  tofler_company: "Tofler MCA Filings",
};

type EvidenceItem = {
  collector: string;
  collectorLabel: string;
  findings: CollectorFinding[];
};

function getServiceEvidence(serviceName: string, signalEvidence: SignalEvidence): EvidenceItem[] {
  const collectors = SERVICE_COLLECTOR_MAP[serviceName] ?? [];
  const items: EvidenceItem[] = [];
  for (const col of collectors) {
    const findings = signalEvidence[col];
    if (findings && findings.length > 0) {
      items.push({
        collector: col,
        collectorLabel: COLLECTOR_LABELS[col] ?? col,
        findings,
      });
    }
  }
  return items;
}

function EvidenceCard({ item }: { item: EvidenceItem }) {
  return (
    <div className="rounded-lg border border-blue-500/10 bg-blue-950/15 px-3 py-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <Search size={10} className="text-blue-400" />
        <span className="text-[11px] font-medium text-blue-400">{item.collectorLabel}</span>
        {item.findings[0]?.observed_at && (
          <span className="text-[10px] text-[var(--text-subtle)] ml-auto">
            {formatTimeAgo(item.findings[0].observed_at)}
          </span>
        )}
      </div>
      <div className="space-y-1">
        {item.findings.map((f, i) => (
          <div key={i}>
            {/* Main finding line */}
            <div className="flex items-center gap-2">
              <span className="text-[10px] rounded bg-[var(--surface-elevated)] px-1.5 py-0.5 text-[var(--text-muted)] font-mono">{f.type}</span>
              {f.value_text && (
                <span className="text-[11px] text-[var(--text-secondary)] truncate">{f.value_text}</span>
              )}
              {f.value_num != null && (
                <span className="text-[11px] font-mono text-[var(--text-primary)]">{f.value_num}</span>
              )}
            </div>
            {/* Payload details */}
            {f.payload && <PayloadDetails payload={f.payload} type={f.type} />}
          </div>
        ))}
      </div>
    </div>
  );
}

function PayloadDetails({ payload, type }: { payload: Record<string, unknown>; type: string }) {
  const items = extractPayloadHighlights(payload, type);
  if (items.length === 0) return null;

  return (
    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 pl-2 border-l border-blue-500/15">
      {items.map(([label, value, status], i) => (
        <div key={i} className="flex items-center gap-1">
          {status === "good" && <CheckCircle2 size={9} className="text-emerald-400" />}
          {status === "bad" && <XCircle size={9} className="text-rose-400" />}
          {status === "neutral" && <span className="w-[9px] h-[9px] rounded-full bg-[var(--text-subtle)] inline-block" />}
          <span className="text-[10px] text-[var(--text-muted)]">{label}:</span>
          <span className="text-[10px] text-[var(--text-secondary)]">{value}</span>
        </div>
      ))}
    </div>
  );
}

type PayloadHighlight = [label: string, value: string, status: "good" | "bad" | "neutral"];

function extractPayloadHighlights(payload: Record<string, unknown>, type: string): PayloadHighlight[] {
  const out: PayloadHighlight[] = [];

  // social.presence — profiles with handles, followers, verified status
  if (type.startsWith("social.presence")) {
    const profiles = payload.profiles as Record<string, Record<string, unknown>> | undefined;
    if (profiles) {
      for (const [platform, data] of Object.entries(profiles)) {
        const handle = data.handle as string | undefined;
        const followers = data.followers as number | undefined;
        const verified = data.verified as boolean | undefined;
        const label = platform.charAt(0).toUpperCase() + platform.slice(1);
        let val = followers != null ? `@${handle} (${formatFollowers(followers)})` : `@${handle ?? "—"}`;
        if (verified) val += " ✓";
        out.push([label, val, followers != null && followers >= 10000 ? "good" : "neutral"]);
      }
    }
    const count = payload.platform_count as number | undefined;
    if (count != null) out.push(["Platforms", String(count), count >= 3 ? "good" : "bad"]);
    const socialScore = payload.social_score as number | undefined;
    if (socialScore != null) out.push(["Social score", `${socialScore}/100`, socialScore >= 50 ? "good" : "bad"]);
    const influencer = payload.has_influencer_program;
    if (influencer != null) out.push(["Influencer program", influencer ? "Yes" : "Not found", influencer ? "good" : "bad"]);
  }

  // social.influencer_program
  if (type.startsWith("social.influencer_program")) {
    const pages = payload.influencer_pages as string[] | undefined;
    if (pages?.length) out.push(["Found at", pages.join(", "), "good"]);
    const signals = payload.influencer_signals as string[] | undefined;
    if (signals?.length) out.push(["Signals", signals.join(", "), "neutral"]);
  }

  // content.blog — actual fields: maturity, post_count_estimate, latest_post_date, has_rss_feed, blog_url, blog_path, categories, days_since_last_post
  if (type.startsWith("content.blog")) {
    const maturity = payload.maturity as string | undefined;
    if (maturity) out.push(["Maturity", maturity, maturity === "strong" || maturity === "active" ? "good" : "bad"]);
    const postCount = (payload.post_count_estimate ?? payload.post_count) as number | undefined;
    if (postCount != null) out.push(["Posts found", String(postCount), postCount > 10 ? "good" : "bad"]);
    const lastPost = (payload.latest_post_date ?? payload.last_post_date) as string | undefined;
    if (lastPost) out.push(["Last post", lastPost, "neutral"]);
    const daysSince = payload.days_since_last_post as number | undefined;
    if (daysSince != null) out.push(["Days since post", String(daysSince), daysSince < 30 ? "good" : "bad"]);
    const blogUrl = (payload.blog_url ?? payload.blog_path) as string | undefined;
    if (blogUrl) out.push(["Blog URL", blogUrl, "neutral"]);
    const hasRss = payload.has_rss_feed ?? payload.has_rss;
    if (hasRss != null) out.push(["RSS feed", hasRss ? "Yes" : "No", hasRss ? "good" : "neutral"]);
    const hasBlog = payload.has_blog;
    if (hasBlog != null) out.push(["Has blog", hasBlog ? "Yes" : "No", hasBlog ? "good" : "bad"]);
    const categories = payload.categories as string[] | undefined;
    if (categories?.length) out.push(["Categories", categories.slice(0, 5).join(", "), "neutral"]);
  }

  // affiliate.program — actual fields: has_program, networks, partners, commission_details, affiliate_pages, score
  if (type.startsWith("affiliate.program")) {
    const hasProgram = payload.has_program ?? payload.has_affiliate_page;
    if (hasProgram != null) out.push(["Affiliate program", hasProgram ? "Found" : "Not found", hasProgram ? "good" : "bad"]);
    const networks = (payload.networks ?? payload.networks_detected) as string[] | undefined;
    if (networks) out.push(["Networks", networks.length > 0 ? networks.join(", ") : "None detected", networks.length > 0 ? "good" : "bad"]);
    const pages = (payload.affiliate_pages ?? payload.affiliate_pages_found) as string[] | undefined;
    if (pages?.length) out.push(["Pages found", pages.join(", "), "neutral"]);
    const commission = (payload.commission_details ?? payload.commission_info) as string | undefined;
    if (commission) out.push(["Commission", commission, "neutral"]);
    const partners = payload.partners as string[] | undefined;
    if (partners?.length) out.push(["Partners", partners.slice(0, 5).join(", "), "neutral"]);
    const affScore = payload.score as number | undefined;
    if (affScore != null) out.push(["Affiliate score", `${affScore}/100`, affScore >= 50 ? "good" : "bad"]);
  }

  // email.maturity — actual fields: has_spf, has_dkim, has_dmarc, has_mx, mx_providers, spf_includes, dmarc_policy, email_providers, maturity_score, maturity_level
  if (type.startsWith("email.maturity") || type.startsWith("email_maturity")) {
    const spf = payload.has_spf ?? payload.spf_present ?? payload.spf;
    if (spf != null) out.push(["SPF", spf ? "Present" : "Missing", spf ? "good" : "bad"]);
    const dkim = payload.has_dkim ?? payload.dkim_present ?? payload.dkim;
    if (dkim != null) out.push(["DKIM", dkim ? "Present" : "Missing", dkim ? "good" : "bad"]);
    const dmarc = payload.has_dmarc ?? payload.dmarc_present ?? payload.dmarc;
    if (dmarc != null) out.push(["DMARC", dmarc ? "Present" : "Missing", dmarc ? "good" : "bad"]);
    const dmarcPolicy = payload.dmarc_policy as string | undefined;
    if (dmarcPolicy) out.push(["DMARC policy", dmarcPolicy, dmarcPolicy === "reject" ? "good" : dmarcPolicy === "none" ? "bad" : "neutral"]);
    const hasMx = payload.has_mx;
    if (hasMx != null) out.push(["MX records", hasMx ? "Present" : "Missing", hasMx ? "good" : "bad"]);
    const mxProviders = payload.mx_providers as string[] | undefined;
    if (mxProviders?.length) out.push(["MX providers", mxProviders.join(", "), "neutral"]);
    const emailProviders = payload.email_providers as string[] | undefined;
    if (emailProviders?.length) out.push(["Email providers", emailProviders.join(", "), "neutral"]);
    const esp = payload.esp_detected as string | undefined;
    if (esp) out.push(["ESP", esp, "neutral"]);
    const maturityScore = payload.maturity_score as number | undefined;
    if (maturityScore != null) out.push(["Email maturity", `${maturityScore}/100`, maturityScore >= 50 ? "good" : "bad"]);
    const maturityLevel = payload.maturity_level as string | undefined;
    if (maturityLevel) out.push(["Maturity level", maturityLevel, maturityLevel === "advanced" ? "good" : maturityLevel === "basic" ? "bad" : "neutral"]);
  }

  // tech.stack — actual fields: all_technologies, d2c_platforms, payment_gateways, analytics, categories, is_d2c, tech_count, score
  if (type.startsWith("tech_stack") || type.startsWith("tech.")) {
    const techs = (payload.all_technologies ?? payload.technologies) as string[] | undefined;
    if (techs?.length) out.push(["Stack", techs.slice(0, 8).join(", "), "neutral"]);
    const d2cPlatforms = payload.d2c_platforms as string[] | undefined;
    if (d2cPlatforms?.length) out.push(["D2C platforms", d2cPlatforms.join(", "), "good"]);
    const paymentGateways = payload.payment_gateways as string[] | undefined;
    if (paymentGateways?.length) out.push(["Payment gateways", paymentGateways.join(", "), "neutral"]);
    const analytics = payload.analytics as string[] | undefined;
    if (analytics?.length) out.push(["Analytics", analytics.join(", "), "neutral"]);
    const isD2c = payload.is_d2c;
    if (isD2c != null) out.push(["D2C brand", isD2c ? "Yes" : "No", isD2c ? "good" : "neutral"]);
    const techCount = payload.tech_count as number | undefined;
    if (techCount != null) out.push(["Tech count", String(techCount), "neutral"]);
    const platform = payload.platform as string | undefined;
    if (platform) out.push(["Platform", platform, "neutral"]);
    const cms = payload.cms as string | undefined;
    if (cms) out.push(["CMS", cms, "neutral"]);
    const techScore = payload.score as number | undefined;
    if (techScore != null) out.push(["Tech score", `${techScore}/100`, techScore >= 50 ? "good" : "bad"]);
  }

  // site.pagespeed — actual fields: performance_score, fcp_ms, lcp_ms, cls, tbt_ms, si_ms, strategy, level
  if (type.startsWith("pagespeed") || type.startsWith("perf.") || type.startsWith("site.pagespeed")) {
    const perfScore = (payload.performance_score ?? payload.mobile_score ?? payload.desktop_score) as number | undefined;
    const strategy = payload.strategy as string | undefined;
    if (perfScore != null) {
      const label = strategy ? `${strategy.charAt(0).toUpperCase() + strategy.slice(1)} score` : "Performance";
      out.push([label, `${perfScore}/100`, perfScore >= 50 ? "good" : "bad"]);
    }
    const fcp = (payload.fcp_ms ?? payload.fcp) as number | undefined;
    if (fcp != null) out.push(["FCP", `${fcp}ms`, fcp < 2000 ? "good" : "bad"]);
    const lcp = (payload.lcp_ms ?? payload.lcp) as number | undefined;
    if (lcp != null) out.push(["LCP", `${lcp}ms`, lcp < 2500 ? "good" : "bad"]);
    const cls = payload.cls as number | undefined;
    if (cls != null) out.push(["CLS", cls.toFixed(3), cls < 0.1 ? "good" : "bad"]);
    const tbt = payload.tbt_ms as number | undefined;
    if (tbt != null) out.push(["TBT", `${tbt}ms`, tbt < 300 ? "good" : "bad"]);
    const si = payload.si_ms as number | undefined;
    if (si != null) out.push(["Speed Index", `${si}ms`, si < 3400 ? "good" : "bad"]);
    const level = payload.level as string | undefined;
    if (level) out.push(["Level", level, level === "fast" ? "good" : level === "slow" ? "bad" : "neutral"]);
  }

  // app.google_play — actual fields: title, score, installs, installs_text, genre, developer, free, updated, developer_website
  if (type === "app.google_play") {
    const title = payload.title as string | undefined;
    if (title) out.push(["App", title, "neutral"]);
    const score = payload.score as number | undefined;
    if (score != null) out.push(["Rating", `${score.toFixed(1)}/5`, score >= 4.0 ? "good" : "bad"]);
    const installsText = payload.installs_text as string | undefined;
    const installs = payload.installs as number | undefined;
    if (installsText) out.push(["Installs", installsText, installs != null && installs > 100000 ? "good" : "neutral"]);
    else if (installs != null) out.push(["Installs", formatFollowers(installs), installs > 100000 ? "good" : "neutral"]);
    const genre = payload.genre as string | undefined;
    if (genre) out.push(["Genre", genre, "neutral"]);
    const developer = payload.developer as string | undefined;
    if (developer) out.push(["Developer", developer, "neutral"]);
    const free = payload.free as boolean | undefined;
    if (free != null) out.push(["Price", free ? "Free" : (payload.price as string) ?? "Paid", "neutral"]);
    const updated = payload.updated as string | undefined;
    if (updated) out.push(["Last updated", updated, "neutral"]);
  }

  // app.ios — actual fields: app_name, rating, reviews, genre, developer, price
  if (type === "app.ios") {
    const appName = payload.app_name as string | undefined;
    if (appName) out.push(["App", appName, "neutral"]);
    const rating = payload.rating as number | undefined;
    if (rating != null) out.push(["Rating", `${rating.toFixed(1)}/5`, rating >= 4.0 ? "good" : "bad"]);
    const reviews = payload.reviews as number | undefined;
    if (reviews != null) out.push(["Reviews", formatFollowers(reviews), reviews > 100 ? "good" : "neutral"]);
    const genre = payload.genre as string | undefined;
    if (genre) out.push(["Genre", genre, "neutral"]);
    const developer = payload.developer as string | undefined;
    if (developer) out.push(["Developer", developer, "neutral"]);
  }

  // programmatic.ads_txt — actual fields: has_ads_txt, total_entries, direct_count, reseller_count, ssps, maturity
  if (type.startsWith("programmatic.ads_txt") || type.startsWith("ads_txt") || type.startsWith("ads.")) {
    const present = payload.has_ads_txt ?? payload.ads_txt_present ?? payload.exists;
    if (present != null) out.push(["ads.txt", present ? "Present" : "Not found", present ? "good" : "bad"]);
    const totalEntries = (payload.total_entries ?? payload.ad_network_count) as number | undefined;
    if (totalEntries != null) out.push(["Total entries", String(totalEntries), totalEntries > 0 ? "good" : "bad"]);
    const directCount = payload.direct_count as number | undefined;
    if (directCount != null) out.push(["Direct", String(directCount), directCount > 0 ? "good" : "neutral"]);
    const resellerCount = payload.reseller_count as number | undefined;
    if (resellerCount != null) out.push(["Reseller", String(resellerCount), "neutral"]);
    const ssps = (payload.ssps ?? payload.networks) as string[] | undefined;
    if (ssps?.length) out.push(["SSPs", ssps.slice(0, 5).join(", "), "neutral"]);
    const adMaturity = payload.maturity as string | undefined;
    if (adMaturity) out.push(["Programmatic maturity", adMaturity, adMaturity === "advanced" ? "good" : adMaturity === "none" ? "bad" : "neutral"]);
  }

  // ad_spend.meta.active — actual fields: ad_count, country, platforms, earliest_start, latest_start, creative_captions
  if (type.startsWith("ad_spend.meta")) {
    const adCount = payload.ad_count as number | undefined;
    if (adCount != null) out.push(["Active ads", String(adCount), adCount > 0 ? "good" : "bad"]);
    const country = payload.country as string | undefined;
    if (country) out.push(["Country", country, "neutral"]);
    const platforms = payload.platforms as string[] | undefined;
    if (platforms?.length) out.push(["Platforms", platforms.join(", "), "neutral"]);
    const earliest = payload.earliest_start as string | undefined;
    if (earliest) out.push(["Earliest ad", earliest, "neutral"]);
    const latest = payload.latest_start as string | undefined;
    if (latest) out.push(["Latest ad", latest, "neutral"]);
    const captions = payload.creative_captions as string[] | undefined;
    if (captions?.length) out.push(["Sample copy", captions[0].length > 60 ? captions[0].slice(0, 57) + "..." : captions[0], "neutral"]);
  }

  // ad_spend.google.active — actual fields: appearances, sample_titles, region
  if (type.startsWith("ad_spend.google")) {
    const appearances = payload.appearances as number | undefined;
    if (appearances != null) out.push(["Ad appearances", String(appearances), appearances > 0 ? "good" : "bad"]);
    const titles = payload.sample_titles as string[] | undefined;
    if (titles?.length) out.push(["Sample titles", titles.slice(0, 2).join(" | "), "neutral"]);
    const region = payload.region as string | undefined;
    if (region) out.push(["Region", region, "neutral"]);
  }

  // social.youtube — actual fields: subscribers, total_views, video_count, channel_name, country, published_at
  if (type.startsWith("social.youtube") || type.startsWith("youtube")) {
    const channelName = payload.channel_name as string | undefined;
    if (channelName) out.push(["Channel", channelName, "neutral"]);
    const subs = payload.subscribers as number | undefined;
    if (subs != null) out.push(["Subscribers", formatFollowers(subs), subs >= 10000 ? "good" : "neutral"]);
    const views = payload.total_views as number | undefined;
    if (views != null) out.push(["Total views", formatFollowers(views), "neutral"]);
    const videos = payload.video_count as number | undefined;
    if (videos != null) out.push(["Videos", String(videos), videos > 20 ? "good" : "neutral"]);
    const country = payload.country as string | undefined;
    if (country) out.push(["Country", country, "neutral"]);
    const published = payload.published_at as string | undefined;
    if (published) out.push(["Channel since", published.slice(0, 10), "neutral"]);
  }

  // hiring.linkedin — actual fields: count, titles, earliest_posted, sample_url, location
  if (type.startsWith("hiring.")) {
    const count = payload.count as number | undefined;
    if (count != null) out.push(["Open positions", String(count), count > 0 ? "good" : "neutral"]);
    const titles = payload.titles as string[] | undefined;
    if (titles?.length) out.push(["Roles", titles.slice(0, 3).join(", "), "neutral"]);
    const location = payload.location as string | undefined;
    if (location) out.push(["Location", location, "neutral"]);
    const earliest = payload.earliest_posted as string | undefined;
    if (earliest) out.push(["Earliest posted", earliest, "neutral"]);
    const sampleUrl = payload.sample_url as string | undefined;
    if (sampleUrl) out.push(["Sample listing", sampleUrl.length > 50 ? sampleUrl.slice(0, 47) + "..." : sampleUrl, "neutral"]);
    if (!count && !titles?.length) out.push(["Signal", "Hiring for marketing roles", "good"]);
  }

  // news.* — actual fields: title, link, source, summary
  if (type.startsWith("news.")) {
    const title = payload.title as string | undefined;
    if (title) out.push(["Headline", title.length > 60 ? title.slice(0, 57) + "..." : title, "neutral"]);
    const source = payload.source as string | undefined;
    if (source) out.push(["Source", source, "neutral"]);
    const summary = payload.summary as string | undefined;
    if (summary) out.push(["Summary", summary.length > 80 ? summary.slice(0, 77) + "..." : summary, "neutral"]);
    const link = payload.link as string | undefined;
    if (link) out.push(["Link", link.length > 50 ? link.slice(0, 47) + "..." : link, "neutral"]);
  }

  // trends.rising — actual fields: query, rise_value, geo, timeframe, search_category
  if (type.startsWith("trends.")) {
    const query = payload.query as string | undefined;
    if (query) out.push(["Query", query, "neutral"]);
    const rise = payload.rise_value as number | undefined;
    if (rise != null) out.push(["Rise", `+${rise}%`, rise > 200 ? "good" : "neutral"]);
    const geo = payload.geo as string | undefined;
    if (geo) out.push(["Region", geo, "neutral"]);
    const timeframe = payload.timeframe as string | undefined;
    if (timeframe) out.push(["Timeframe", timeframe, "neutral"]);
  }

  // cert.new_domain — actual fields: domain, issuer, not_before, not_after
  if (type.startsWith("cert.")) {
    const domain = payload.domain as string | undefined;
    if (domain) out.push(["Domain", domain, "neutral"]);
    const issuer = payload.issuer as string | undefined;
    if (issuer) out.push(["Issuer", issuer, "neutral"]);
    const notBefore = payload.not_before as string | undefined;
    if (notBefore) out.push(["Valid from", notBefore, "neutral"]);
    const notAfter = payload.not_after as string | undefined;
    if (notAfter) out.push(["Expires", notAfter, "neutral"]);
  }

  // domain.registration — actual fields: domain, created, expires, registrar, nameservers, platform_hint, age_days, age_label
  if (type.startsWith("domain.registration")) {
    const registrar = payload.registrar as string | undefined;
    if (registrar) out.push(["Registrar", registrar, "neutral"]);
    const created = payload.created as string | undefined;
    if (created) out.push(["Registered", created, "neutral"]);
    const expires = payload.expires as string | undefined;
    if (expires) out.push(["Expires", expires, "neutral"]);
    const ageLabel = payload.age_label as string | undefined;
    if (ageLabel) out.push(["Domain age", ageLabel, "neutral"]);
    const platformHint = payload.platform_hint as string | undefined;
    if (platformHint) out.push(["Platform hint", platformHint, "neutral"]);
    const nameservers = payload.nameservers as string[] | undefined;
    if (nameservers?.length) out.push(["Nameservers", nameservers.slice(0, 2).join(", "), "neutral"]);
  }

  // infra.hosting (shodan) — actual fields: ip, ports, tags, hostnames, cdn, cloud, has_https, maturity_score
  if (type.startsWith("infra.hosting")) {
    const ip = payload.ip as string | undefined;
    if (ip) out.push(["IP", ip, "neutral"]);
    const ports = payload.ports as number[] | undefined;
    if (ports?.length) out.push(["Open ports", ports.join(", "), ports.length > 5 ? "bad" : "neutral"]);
    const cdn = payload.cdn as string | undefined;
    if (cdn) out.push(["CDN", cdn, "good"]);
    const cloud = payload.cloud as string | undefined;
    if (cloud) out.push(["Cloud", cloud, "neutral"]);
    const hasHttps = payload.has_https;
    if (hasHttps != null) out.push(["HTTPS", hasHttps ? "Yes" : "No", hasHttps ? "good" : "bad"]);
    const tags = payload.tags as string[] | undefined;
    if (tags?.length) out.push(["Tags", tags.slice(0, 5).join(", "), "neutral"]);
    const hostnames = payload.hostnames as string[] | undefined;
    if (hostnames?.length) out.push(["Hostnames", hostnames.slice(0, 3).join(", "), "neutral"]);
    const matScore = payload.maturity_score as number | undefined;
    if (matScore != null) out.push(["Infra maturity", `${matScore}/100`, matScore >= 50 ? "good" : "bad"]);
  }

  // infra.geo (ipinfo) — actual fields: ip, country, city, org, asn_name, is_india_hosted
  if (type.startsWith("infra.geo")) {
    const ip = payload.ip as string | undefined;
    if (ip) out.push(["IP", ip, "neutral"]);
    const country = payload.country as string | undefined;
    if (country) out.push(["Country", country, "neutral"]);
    const city = payload.city as string | undefined;
    if (city) out.push(["City", city, "neutral"]);
    const org = payload.org as string | undefined;
    if (org) out.push(["Org", org, "neutral"]);
    const asnName = payload.asn_name as string | undefined;
    if (asnName) out.push(["ASN", asnName, "neutral"]);
    const isIndia = payload.is_india_hosted;
    if (isIndia != null) out.push(["India hosted", isIndia ? "Yes" : "No", "neutral"]);
  }

  // scan.site_found (urlscan) — actual fields: server, title, tags, has_ecommerce_tag, country
  if (type.startsWith("scan.site_found")) {
    const server = payload.server as string | undefined;
    if (server) out.push(["Server", server, "neutral"]);
    const title = payload.title as string | undefined;
    if (title) out.push(["Page title", title.length > 50 ? title.slice(0, 47) + "..." : title, "neutral"]);
    const tags = payload.tags as string[] | undefined;
    if (tags?.length) out.push(["Tags", tags.slice(0, 5).join(", "), "neutral"]);
    const ecom = payload.has_ecommerce_tag;
    if (ecom != null) out.push(["E-commerce", ecom ? "Detected" : "Not detected", ecom ? "good" : "neutral"]);
    const country = payload.country as string | undefined;
    if (country) out.push(["Country", country, "neutral"]);
  }

  // social.hn_mention (HN) — actual fields: title, points, url, is_show_hn, num_comments, created_at
  if (type.startsWith("social.hn_mention")) {
    const title = payload.title as string | undefined;
    if (title) out.push(["HN Post", title.length > 50 ? title.slice(0, 47) + "..." : title, "neutral"]);
    const points = payload.points as number | undefined;
    if (points != null) out.push(["Points", String(points), points > 50 ? "good" : "neutral"]);
    const numComments = payload.num_comments as number | undefined;
    if (numComments != null) out.push(["Comments", String(numComments), numComments > 20 ? "good" : "neutral"]);
    const isShowHn = payload.is_show_hn;
    if (isShowHn) out.push(["Type", "Show HN", "good"]);
  }

  // crawl.store_found (Common Crawl)
  if (type.startsWith("crawl.")) {
    const sourcePattern = payload.source_pattern as string | undefined;
    if (sourcePattern) out.push(["Pattern", sourcePattern, "neutral"]);
    const index = payload.index as string | undefined;
    if (index) out.push(["Crawl index", index, "neutral"]);
  }

  // product.food_brand (Open Food Facts)
  if (type.startsWith("product.food_brand")) {
    const productName = payload.product_name as string | undefined;
    if (productName) out.push(["Product", productName, "neutral"]);
    const categories = payload.categories as string | undefined;
    if (categories) out.push(["Categories", categories.length > 50 ? categories.slice(0, 47) + "..." : categories, "neutral"]);
    const nutriscore = payload.nutriscore as string | undefined;
    if (nutriscore) out.push(["Nutri-Score", nutriscore.toUpperCase(), "neutral"]);
    const stores = payload.stores as string | undefined;
    if (stores) out.push(["Stores", stores, "neutral"]);
  }

  // seo.rank — actual fields: avg_rank, keywords_found, keywords_tracked, rankings
  if (type.startsWith("seo.")) {
    const avgRank = payload.avg_rank as number | undefined;
    if (avgRank != null) out.push(["Avg position", `#${avgRank.toFixed(1)}`, avgRank <= 10 ? "good" : avgRank <= 30 ? "neutral" : "bad"]);
    const kwFound = payload.keywords_found as number | undefined;
    if (kwFound != null) out.push(["Keywords ranking", String(kwFound), kwFound >= 5 ? "good" : "bad"]);
    const kwTracked = payload.keywords_tracked as number | undefined;
    if (kwTracked != null) out.push(["Keywords tracked", String(kwTracked), "neutral"]);
    const rankings = payload.rankings as Record<string, unknown> | undefined;
    if (rankings) {
      const topKw = Object.entries(rankings).slice(0, 3);
      for (const [kw, rank] of topKw) {
        const r = typeof rank === "number" ? rank : (rank as Record<string, unknown>)?.rank as number | undefined;
        if (r != null) out.push([`"${kw}"`, `#${r}`, r <= 10 ? "good" : r <= 30 ? "neutral" : "bad"]);
      }
    }
  }

  // funding.round — from crunchbase_funding / tracxn_funding
  if (type.startsWith("funding.round")) {
    const round = payload.round as string | undefined;
    if (round) out.push(["Round", round, "good"]);
    const amount = payload.amount as string | undefined;
    if (amount) out.push(["Amount", amount, "good"]);
    const date = payload.date as string | undefined;
    if (date) out.push(["Date", date, "neutral"]);
    const investors = payload.investors as string[] | undefined;
    if (investors?.length) out.push(["Investors", investors.slice(0, 4).join(", "), "neutral"]);
    const cbUrl = payload.crunchbase_url as string | undefined;
    if (cbUrl) out.push(["Source", "Crunchbase", "neutral"]);
    const txUrl = payload.tracxn_url as string | undefined;
    if (txUrl) out.push(["Source", "Tracxn", "neutral"]);
  }

  // funding.total
  if (type.startsWith("funding.total")) {
    const total = payload.total_funding as string | undefined;
    if (total) out.push(["Total funding", total, "good"]);
    const rounds = payload.rounds_found as number | undefined;
    if (rounds != null) out.push(["Rounds found", String(rounds), rounds >= 2 ? "good" : "neutral"]);
  }

  // funding.profile — Crunchbase/Tracxn profile found but no structured data
  if (type.startsWith("funding.profile")) {
    const snippetCount = payload.snippet_count as number | undefined;
    if (snippetCount != null) out.push(["Snippets found", String(snippetCount), "neutral"]);
    out.push(["Status", "Profile found", "neutral"]);
  }

  // revenue.estimate — from tracxn_funding
  if (type.startsWith("revenue.estimate")) {
    out.push(["Revenue estimate", "detected", "good"]);
  }

  // company.employees — from tracxn_funding or tofler_company
  if (type.startsWith("company.employees")) {
    out.push(["Employee data", "detected", "neutral"]);
  }

  // company.revenue_filing — from tofler_company (MCA filings)
  if (type.startsWith("company.revenue_filing")) {
    out.push(["Revenue (MCA filing)", "detected", "good"]);
    const cin = payload.cin as string | undefined;
    if (cin) out.push(["CIN", cin, "neutral"]);
  }

  // company.capital — authorized/paid-up capital
  if (type.startsWith("company.capital")) {
    out.push(["Capital", "detected", "neutral"]);
  }

  // company.incorporated — founding date from MCA
  if (type.startsWith("company.incorporated")) {
    out.push(["Incorporation", "detected", "neutral"]);
  }

  // company.directors — from MCA filings
  if (type.startsWith("company.directors")) {
    const directors = payload.directors as string[] | undefined;
    if (directors?.length) out.push(["Directors", directors.slice(0, 3).join(", "), "good"]);
  }

  // company.mca_profile — found on Tofler but no structured data
  if (type.startsWith("company.mca_profile")) {
    const cin = payload.cin as string | undefined;
    if (cin) out.push(["CIN", cin, "neutral"]);
    out.push(["MCA Profile", "Found", "neutral"]);
  }

  return out;
}

function formatFollowers(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/* ── Proof Points — real collector metrics for Overview tab ── */

type ProofPoint = {
  label: string;
  value: string;
  status: "good" | "bad" | "neutral";
  source: string;
  observedAt: string | null;
};

function extractProofPoints(signalEvidence: SignalEvidence): ProofPoint[] {
  const points: ProofPoint[] = [];

  const social = signalEvidence["social_presence"];
  if (social) {
    for (const f of social) {
      if (f.type === "social.presence" && f.payload) {
        const profiles = f.payload.profiles as Record<string, Record<string, unknown>> | undefined;
        if (profiles) {
          for (const [platform, data] of Object.entries(profiles)) {
            const followers = data.followers as number | undefined;
            if (followers != null) {
              points.push({
                label: `${platform.charAt(0).toUpperCase() + platform.slice(1)} followers`,
                value: formatFollowers(followers),
                status: followers >= 10000 ? "good" : followers >= 1000 ? "neutral" : "bad",
                source: "Social Presence Detector",
                observedAt: f.observed_at,
              });
            }
          }
        }
        const count = f.payload.platform_count as number | undefined;
        if (count != null) {
          points.push({
            label: "Social platforms active",
            value: String(count),
            status: count >= 3 ? "good" : "bad",
            source: "Social Presence Detector",
            observedAt: f.observed_at,
          });
        }
        const influencer = f.payload.has_influencer_program;
        if (influencer != null) {
          points.push({
            label: "Influencer program",
            value: influencer ? "Detected" : "Not found",
            status: influencer ? "good" : "bad",
            source: "Social Presence Detector",
            observedAt: f.observed_at,
          });
        }
      }
    }
  }

  const pagespeed = signalEvidence["pagespeed"];
  if (pagespeed) {
    for (const f of pagespeed) {
      if (f.payload) {
        const perfScore = f.payload.performance_score as number | undefined;
        const strategy = f.payload.strategy as string | undefined;
        if (perfScore != null) {
          const label = strategy ? `PageSpeed ${strategy.charAt(0).toUpperCase() + strategy.slice(1)}` : "PageSpeed";
          points.push({ label, value: `${perfScore}/100`, status: perfScore >= 50 ? "good" : "bad", source: "PageSpeed Analyzer", observedAt: f.observed_at });
        }
        const lcp = (f.payload.lcp_ms ?? f.payload.lcp) as number | undefined;
        if (lcp != null) points.push({ label: "LCP", value: `${lcp}ms`, status: lcp < 2500 ? "good" : "bad", source: "PageSpeed Analyzer", observedAt: f.observed_at });
        const fcp = (f.payload.fcp_ms ?? f.payload.fcp) as number | undefined;
        if (fcp != null) points.push({ label: "FCP", value: `${fcp}ms`, status: fcp < 2000 ? "good" : "bad", source: "PageSpeed Analyzer", observedAt: f.observed_at });
        const cls = f.payload.cls as number | undefined;
        if (cls != null) points.push({ label: "CLS", value: cls.toFixed(3), status: cls < 0.1 ? "good" : "bad", source: "PageSpeed Analyzer", observedAt: f.observed_at });
        const tbt = f.payload.tbt_ms as number | undefined;
        if (tbt != null) points.push({ label: "TBT", value: `${tbt}ms`, status: tbt < 300 ? "good" : "bad", source: "PageSpeed Analyzer", observedAt: f.observed_at });
      }
    }
  }

  const email = signalEvidence["email_maturity"];
  if (email) {
    for (const f of email) {
      if (f.payload) {
        const spf = f.payload.has_spf ?? f.payload.spf_present ?? f.payload.spf;
        if (spf != null) points.push({ label: "SPF", value: spf ? "Present" : "Missing", status: spf ? "good" : "bad", source: "Email Infrastructure", observedAt: f.observed_at });
        const dkim = f.payload.has_dkim ?? f.payload.dkim_present ?? f.payload.dkim;
        if (dkim != null) points.push({ label: "DKIM", value: dkim ? "Present" : "Missing", status: dkim ? "good" : "bad", source: "Email Infrastructure", observedAt: f.observed_at });
        const dmarc = f.payload.has_dmarc ?? f.payload.dmarc_present ?? f.payload.dmarc;
        if (dmarc != null) points.push({ label: "DMARC", value: dmarc ? "Present" : "Missing", status: dmarc ? "good" : "bad", source: "Email Infrastructure", observedAt: f.observed_at });
        const dmarcPolicy = f.payload.dmarc_policy as string | undefined;
        if (dmarcPolicy) points.push({ label: "DMARC policy", value: dmarcPolicy, status: dmarcPolicy === "reject" ? "good" : dmarcPolicy === "none" ? "bad" : "neutral", source: "Email Infrastructure", observedAt: f.observed_at });
        const emailProviders = f.payload.email_providers as string[] | undefined;
        if (emailProviders?.length) points.push({ label: "Email providers", value: emailProviders.join(", "), status: "neutral", source: "Email Infrastructure", observedAt: f.observed_at });
        const esp = f.payload.esp_detected as string | undefined;
        if (esp) points.push({ label: "ESP", value: esp, status: "neutral", source: "Email Infrastructure", observedAt: f.observed_at });
        const maturityScore = f.payload.maturity_score as number | undefined;
        if (maturityScore != null) points.push({ label: "Email maturity score", value: `${maturityScore}/100`, status: maturityScore >= 50 ? "good" : "bad", source: "Email Infrastructure", observedAt: f.observed_at });
      }
    }
  }

  const seo = signalEvidence["seo_rank_tracking"];
  if (seo) {
    for (const f of seo) {
      if (f.payload) {
        const avgRank = f.payload.avg_rank as number | undefined;
        if (avgRank != null) points.push({ label: "Avg SEO rank", value: `#${avgRank.toFixed(1)}`, status: avgRank <= 10 ? "good" : avgRank <= 30 ? "neutral" : "bad", source: "SEO Rank Tracker", observedAt: f.observed_at });
        const kwFound = f.payload.keywords_found as number | undefined;
        const kwTracked = f.payload.keywords_tracked as number | undefined;
        if (kwFound != null) points.push({ label: "Keywords ranking", value: kwTracked ? `${kwFound}/${kwTracked}` : String(kwFound), status: kwFound >= 5 ? "good" : "bad", source: "SEO Rank Tracker", observedAt: f.observed_at });
      }
    }
  }

  const blog = signalEvidence["content_blog"];
  if (blog) {
    for (const f of blog) {
      if (f.payload) {
        const maturity = f.payload.maturity as string | undefined;
        if (maturity) points.push({ label: "Content maturity", value: maturity, status: maturity === "strong" || maturity === "active" ? "good" : "bad", source: "Blog Analyzer", observedAt: f.observed_at });
        const postCount = (f.payload.post_count_estimate ?? f.payload.post_count) as number | undefined;
        if (postCount != null) points.push({ label: "Blog posts found", value: String(postCount), status: postCount > 10 ? "good" : "bad", source: "Blog Analyzer", observedAt: f.observed_at });
        const lastPost = (f.payload.latest_post_date ?? f.payload.last_post_date) as string | undefined;
        if (lastPost) points.push({ label: "Last blog post", value: lastPost, status: "neutral", source: "Blog Analyzer", observedAt: f.observed_at });
        const daysSince = f.payload.days_since_last_post as number | undefined;
        if (daysSince != null) points.push({ label: "Days since post", value: String(daysSince), status: daysSince < 30 ? "good" : "bad", source: "Blog Analyzer", observedAt: f.observed_at });
        const hasRss = f.payload.has_rss_feed ?? f.payload.has_rss;
        if (hasRss != null) points.push({ label: "RSS feed", value: hasRss ? "Yes" : "No", status: hasRss ? "good" : "neutral", source: "Blog Analyzer", observedAt: f.observed_at });
      }
    }
  }

  const affiliate = signalEvidence["affiliate_program"];
  if (affiliate) {
    for (const f of affiliate) {
      if (f.payload) {
        const hasProgram = f.payload.has_program ?? f.payload.has_affiliate_page;
        if (hasProgram != null) points.push({ label: "Affiliate program", value: hasProgram ? "Found" : "Not found", status: hasProgram ? "good" : "bad", source: "Affiliate Scanner", observedAt: f.observed_at });
        const networks = (f.payload.networks ?? f.payload.networks_detected) as string[] | undefined;
        if (networks?.length) points.push({ label: "Affiliate networks", value: networks.join(", "), status: "good", source: "Affiliate Scanner", observedAt: f.observed_at });
        const partners = f.payload.partners as string[] | undefined;
        if (partners?.length) points.push({ label: "Affiliate partners", value: partners.slice(0, 3).join(", "), status: "neutral", source: "Affiliate Scanner", observedAt: f.observed_at });
      }
    }
  }

  const youtube = signalEvidence["youtube_social"];
  if (youtube) {
    for (const f of youtube) {
      if (f.payload) {
        const subs = f.payload.subscribers as number | undefined;
        if (subs != null) points.push({ label: "YouTube subscribers", value: formatFollowers(subs), status: subs >= 10000 ? "good" : "neutral", source: "YouTube Analyzer", observedAt: f.observed_at });
        const views = f.payload.total_views as number | undefined;
        if (views != null) points.push({ label: "YouTube total views", value: formatFollowers(views), status: "neutral", source: "YouTube Analyzer", observedAt: f.observed_at });
        const videos = f.payload.video_count as number | undefined;
        if (videos != null) points.push({ label: "YouTube videos", value: String(videos), status: videos > 20 ? "good" : "neutral", source: "YouTube Analyzer", observedAt: f.observed_at });
      }
    }
  }

  const techStack = signalEvidence["tech_stack"];
  if (techStack) {
    for (const f of techStack) {
      if (f.payload) {
        const isD2c = f.payload.is_d2c;
        if (isD2c != null) points.push({ label: "D2C brand", value: isD2c ? "Confirmed" : "Non-D2C", status: isD2c ? "good" : "neutral", source: "Tech Stack Detector", observedAt: f.observed_at });
        const d2cPlatforms = f.payload.d2c_platforms as string[] | undefined;
        if (d2cPlatforms?.length) points.push({ label: "D2C platforms", value: d2cPlatforms.join(", "), status: "good", source: "Tech Stack Detector", observedAt: f.observed_at });
        const paymentGateways = f.payload.payment_gateways as string[] | undefined;
        if (paymentGateways?.length) points.push({ label: "Payment gateways", value: paymentGateways.join(", "), status: "neutral", source: "Tech Stack Detector", observedAt: f.observed_at });
        const analytics = f.payload.analytics as string[] | undefined;
        if (analytics?.length) points.push({ label: "Analytics tools", value: analytics.join(", "), status: "neutral", source: "Tech Stack Detector", observedAt: f.observed_at });
        const techCount = f.payload.tech_count as number | undefined;
        if (techCount != null) points.push({ label: "Technologies detected", value: String(techCount), status: "neutral", source: "Tech Stack Detector", observedAt: f.observed_at });
      }
    }
  }

  const ads = signalEvidence["meta_ad_library"];
  if (ads) {
    for (const f of ads) {
      if (f.payload) {
        const adCount = f.payload.ad_count as number | undefined;
        if (adCount != null) points.push({ label: "Meta active ads", value: String(adCount), status: adCount > 0 ? "good" : "bad", source: "Meta Ad Library", observedAt: f.observed_at });
        const platforms = f.payload.platforms as string[] | undefined;
        if (platforms?.length) points.push({ label: "Meta ad platforms", value: platforms.join(", "), status: "neutral", source: "Meta Ad Library", observedAt: f.observed_at });
        const country = f.payload.country as string | undefined;
        if (country) points.push({ label: "Meta ads country", value: country, status: "neutral", source: "Meta Ad Library", observedAt: f.observed_at });
      }
    }
  }

  const googleAds = signalEvidence["google_ads_transparency"];
  if (googleAds) {
    for (const f of googleAds) {
      if (f.payload) {
        const appearances = f.payload.appearances as number | undefined;
        if (appearances != null) points.push({ label: "Google ad appearances", value: String(appearances), status: appearances > 0 ? "good" : "bad", source: "Google Ads Transparency", observedAt: f.observed_at });
        const titles = f.payload.sample_titles as string[] | undefined;
        if (titles?.length) points.push({ label: "Google ad samples", value: titles[0], status: "neutral", source: "Google Ads Transparency", observedAt: f.observed_at });
        const region = f.payload.region as string | undefined;
        if (region) points.push({ label: "Google ads region", value: region, status: "neutral", source: "Google Ads Transparency", observedAt: f.observed_at });
      }
    }
  }

  const appStore = signalEvidence["app_store"];
  if (appStore) {
    for (const f of appStore) {
      if (f.payload) {
        const title = f.payload.title as string | undefined;
        if (title) points.push({ label: "Play Store app", value: title, status: "neutral", source: "Play Store Analyzer", observedAt: f.observed_at });
        const score = f.payload.score as number | undefined;
        if (score != null) points.push({ label: "Play Store rating", value: `${score.toFixed(1)}/5`, status: score >= 4.0 ? "good" : "bad", source: "Play Store Analyzer", observedAt: f.observed_at });
        const installsText = f.payload.installs_text as string | undefined;
        const installs = f.payload.installs as number | undefined;
        if (installsText) points.push({ label: "Play Store installs", value: installsText, status: installs != null && installs > 100000 ? "good" : "neutral", source: "Play Store Analyzer", observedAt: f.observed_at });
        else if (installs != null) points.push({ label: "Play Store installs", value: formatFollowers(installs), status: installs > 100000 ? "good" : "neutral", source: "Play Store Analyzer", observedAt: f.observed_at });
        const genre = f.payload.genre as string | undefined;
        if (genre) points.push({ label: "App genre", value: genre, status: "neutral", source: "Play Store Analyzer", observedAt: f.observed_at });
      }
    }
  }

  const iosApp = signalEvidence["ios_app_store"];
  if (iosApp) {
    for (const f of iosApp) {
      if (f.payload) {
        const appName = f.payload.app_name as string | undefined;
        if (appName) points.push({ label: "iOS app", value: appName, status: "neutral", source: "iOS App Store", observedAt: f.observed_at });
        const rating = f.payload.rating as number | undefined;
        if (rating != null) points.push({ label: "iOS App rating", value: `${rating.toFixed(1)}/5`, status: rating >= 4.0 ? "good" : "bad", source: "iOS App Store", observedAt: f.observed_at });
        const reviews = f.payload.reviews as number | undefined;
        if (reviews != null) points.push({ label: "iOS reviews", value: formatFollowers(reviews), status: reviews > 100 ? "good" : "neutral", source: "iOS App Store", observedAt: f.observed_at });
      }
    }
  }

  const jobs = signalEvidence["linkedin_jobs"];
  if (jobs) {
    for (const f of jobs) {
      if (f.payload) {
        const count = f.payload.count as number | undefined;
        if (count != null) points.push({ label: "Open job postings", value: String(count), status: count > 0 ? "good" : "neutral", source: "LinkedIn Jobs", observedAt: f.observed_at });
        const titles = f.payload.titles as string[] | undefined;
        if (titles?.length) points.push({ label: "Hiring for", value: titles.slice(0, 3).join(", "), status: "neutral", source: "LinkedIn Jobs", observedAt: f.observed_at });
      }
    }
  }

  const news = signalEvidence["news_polling"];
  if (news) {
    const newsCount = news.length;
    if (newsCount > 0) {
      points.push({ label: "Recent news mentions", value: String(newsCount), status: newsCount >= 3 ? "good" : "neutral", source: "News Monitor", observedAt: news[0].observed_at });
      for (const f of news.slice(0, 2)) {
        if (f.payload) {
          const title = f.payload.title as string | undefined;
          const source = f.payload.source as string | undefined;
          if (title) points.push({ label: source ? `News (${source})` : "News", value: title.length > 80 ? title.slice(0, 77) + "..." : title, status: "neutral", source: "News Monitor", observedAt: f.observed_at });
        }
      }
    }
  }

  const trends = signalEvidence["google_trends"];
  if (trends) {
    for (const f of trends.slice(0, 3)) {
      if (f.payload) {
        const query = f.payload.query as string | undefined;
        const rise = f.payload.rise_value as number | undefined;
        if (query && rise != null) points.push({ label: "Trending query", value: `"${query}" +${rise}%`, status: rise > 200 ? "good" : "neutral", source: "Google Trends", observedAt: f.observed_at });
      }
    }
  }

  const adsTxt = signalEvidence["ads_txt"];
  if (adsTxt) {
    for (const f of adsTxt) {
      if (f.payload) {
        const present = f.payload.has_ads_txt ?? f.payload.ads_txt_present ?? f.payload.exists;
        if (present != null) points.push({ label: "ads.txt", value: present ? "Present" : "Not found", status: present ? "good" : "bad", source: "Ads.txt Scanner", observedAt: f.observed_at });
        const totalEntries = (f.payload.total_entries ?? f.payload.ad_network_count) as number | undefined;
        if (totalEntries != null && totalEntries > 0) points.push({ label: "Ads.txt entries", value: String(totalEntries), status: "good", source: "Ads.txt Scanner", observedAt: f.observed_at });
        const directCount = f.payload.direct_count as number | undefined;
        if (directCount != null) points.push({ label: "Direct sellers", value: String(directCount), status: directCount > 0 ? "good" : "neutral", source: "Ads.txt Scanner", observedAt: f.observed_at });
        const ssps = (f.payload.ssps ?? f.payload.networks) as string[] | undefined;
        if (ssps?.length) points.push({ label: "SSPs", value: ssps.slice(0, 3).join(", "), status: "neutral", source: "Ads.txt Scanner", observedAt: f.observed_at });
      }
    }
  }

  const shodan = signalEvidence["shodan_internetdb"];
  if (shodan) {
    for (const f of shodan) {
      if (f.payload) {
        const cdn = f.payload.cdn as string | undefined;
        if (cdn) points.push({ label: "CDN", value: cdn, status: "good", source: "Shodan", observedAt: f.observed_at });
        const cloud = f.payload.cloud as string | undefined;
        if (cloud) points.push({ label: "Cloud provider", value: cloud, status: "neutral", source: "Shodan", observedAt: f.observed_at });
        const hasHttps = f.payload.has_https;
        if (hasHttps != null) points.push({ label: "HTTPS", value: hasHttps ? "Yes" : "No", status: hasHttps ? "good" : "bad", source: "Shodan", observedAt: f.observed_at });
        const matScore = f.payload.maturity_score as number | undefined;
        if (matScore != null) points.push({ label: "Infra maturity", value: `${matScore}/100`, status: matScore >= 50 ? "good" : "bad", source: "Shodan", observedAt: f.observed_at });
      }
    }
  }

  const ipinfo = signalEvidence["ipinfo_geo"];
  if (ipinfo) {
    for (const f of ipinfo) {
      if (f.payload) {
        const country = f.payload.country as string | undefined;
        if (country) points.push({ label: "Hosted in", value: country, status: "neutral", source: "IP Geolocation", observedAt: f.observed_at });
        const org = f.payload.org as string | undefined;
        if (org) points.push({ label: "Hosting org", value: org, status: "neutral", source: "IP Geolocation", observedAt: f.observed_at });
        const isIndia = f.payload.is_india_hosted;
        if (isIndia != null) points.push({ label: "India hosted", value: isIndia ? "Yes" : "No", status: "neutral", source: "IP Geolocation", observedAt: f.observed_at });
      }
    }
  }

  const rdap = signalEvidence["rdap_whois"];
  if (rdap) {
    for (const f of rdap) {
      if (f.payload) {
        const ageLabel = f.payload.age_label as string | undefined;
        if (ageLabel) points.push({ label: "Domain age", value: ageLabel, status: "neutral", source: "WHOIS", observedAt: f.observed_at });
        const registrar = f.payload.registrar as string | undefined;
        if (registrar) points.push({ label: "Registrar", value: registrar, status: "neutral", source: "WHOIS", observedAt: f.observed_at });
        const platformHint = f.payload.platform_hint as string | undefined;
        if (platformHint) points.push({ label: "Platform hint", value: platformHint, status: "neutral", source: "WHOIS", observedAt: f.observed_at });
      }
    }
  }

  const urlscan = signalEvidence["urlscan_search"];
  if (urlscan) {
    for (const f of urlscan) {
      if (f.payload) {
        const server = f.payload.server as string | undefined;
        if (server) points.push({ label: "Web server", value: server, status: "neutral", source: "URLScan", observedAt: f.observed_at });
        const ecom = f.payload.has_ecommerce_tag;
        if (ecom != null) points.push({ label: "E-commerce detected", value: ecom ? "Yes" : "No", status: ecom ? "good" : "neutral", source: "URLScan", observedAt: f.observed_at });
      }
    }
  }

  const cbFunding = signalEvidence["crunchbase_funding"];
  if (cbFunding) {
    for (const f of cbFunding) {
      if (f.type === "funding.round" && f.payload) {
        const round = f.payload.round as string | undefined;
        const amount = f.payload.amount as string | undefined;
        if (round || amount) {
          points.push({ label: `Funding ${round ?? "Round"}`, value: amount ?? "detected", status: "good", source: "Crunchbase", observedAt: f.observed_at });
        }
        const investors = f.payload.investors as string[] | undefined;
        if (investors?.length) {
          points.push({ label: "Investors", value: investors.slice(0, 3).join(", "), status: "neutral", source: "Crunchbase", observedAt: f.observed_at });
        }
      }
      if (f.type === "funding.total" && f.payload) {
        const total = f.payload.total_funding as string | undefined;
        if (total) points.push({ label: "Total funding", value: total, status: "good", source: "Crunchbase", observedAt: f.observed_at });
      }
    }
  }

  const txFunding = signalEvidence["tracxn_funding"];
  if (txFunding) {
    for (const f of txFunding) {
      if (f.type === "funding.round" && f.payload) {
        const round = f.payload.round as string | undefined;
        const amount = f.payload.amount as string | undefined;
        if (round || amount) {
          points.push({ label: `Funding ${round ?? "Round"}`, value: amount ?? "detected", status: "good", source: "Tracxn", observedAt: f.observed_at });
        }
      }
      if (f.type === "revenue.estimate") {
        points.push({ label: "Revenue estimate", value: f.value_text ?? "detected", status: "good", source: "Tracxn", observedAt: f.observed_at });
      }
      if (f.type === "company.employees") {
        points.push({ label: "Employee count", value: f.value_text ?? String(f.value_num), status: "neutral", source: "Tracxn", observedAt: f.observed_at });
      }
    }
  }

  const tofler = signalEvidence["tofler_company"];
  if (tofler) {
    for (const f of tofler) {
      if (f.type === "company.revenue_filing") {
        points.push({ label: "Revenue (MCA)", value: f.value_text ?? "detected", status: "good", source: "Tofler MCA", observedAt: f.observed_at });
      }
      if (f.type === "company.employees") {
        points.push({ label: "Employees (MCA)", value: f.value_text ?? String(f.value_num), status: "neutral", source: "Tofler MCA", observedAt: f.observed_at });
      }
      if (f.type === "company.directors" && f.payload) {
        const dirs = f.payload.directors as string[] | undefined;
        if (dirs?.length) {
          points.push({ label: "Directors/Founders", value: dirs.slice(0, 3).join(", "), status: "good", source: "Tofler MCA", observedAt: f.observed_at });
        }
      }
      if (f.type === "company.incorporated") {
        points.push({ label: "Incorporated", value: f.value_text ?? "found", status: "neutral", source: "Tofler MCA", observedAt: f.observed_at });
      }
      if (f.type === "company.capital") {
        points.push({ label: "Paid-up Capital", value: f.value_text ?? "detected", status: "neutral", source: "Tofler MCA", observedAt: f.observed_at });
      }
    }
  }

  return points;
}

function ProofPointsSection({ signalEvidence }: { signalEvidence: SignalEvidence }) {
  const points = extractProofPoints(signalEvidence);
  if (points.length === 0) return null;

  const good = points.filter((p) => p.status === "good");
  const bad = points.filter((p) => p.status === "bad");
  const neutral = points.filter((p) => p.status === "neutral");

  return (
    <Section icon={Search} label={`Verified Intelligence (${points.length} data points)`} color="text-cyan-400">
      <p className="text-[11px] text-[var(--text-muted)] mb-3">
        Real metrics collected from {Object.keys(signalEvidence).length} sources — not AI-generated
      </p>
      {good.length > 0 && (
        <div className="mb-3">
          <p className="text-[11px] font-medium text-emerald-400 mb-1.5 flex items-center gap-1">
            <CheckCircle2 size={11} /> Strengths ({good.length})
          </p>
          <div className="grid grid-cols-2 gap-1.5">
            {good.map((p, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg bg-emerald-950/20 border border-emerald-500/10 px-3 py-2">
                <CheckCircle2 size={10} className="text-emerald-400 shrink-0" />
                <div className="min-w-0">
                  <span className="text-[10px] text-[var(--text-muted)]">{p.label}</span>
                  <span className="text-[11px] text-emerald-400 font-medium ml-1.5">{p.value}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {bad.length > 0 && (
        <div className="mb-3">
          <p className="text-[11px] font-medium text-rose-400 mb-1.5 flex items-center gap-1">
            <XCircle size={11} /> Gaps / Weaknesses ({bad.length})
          </p>
          <div className="grid grid-cols-2 gap-1.5">
            {bad.map((p, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg bg-rose-950/20 border border-rose-500/10 px-3 py-2">
                <XCircle size={10} className="text-rose-400 shrink-0" />
                <div className="min-w-0">
                  <span className="text-[10px] text-[var(--text-muted)]">{p.label}</span>
                  <span className="text-[11px] text-rose-400 font-medium ml-1.5">{p.value}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {neutral.length > 0 && (
        <div>
          <p className="text-[11px] font-medium text-[var(--text-secondary)] mb-1.5">Other Findings ({neutral.length})</p>
          <div className="grid grid-cols-2 gap-1.5">
            {neutral.map((p, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg bg-[var(--surface-overlay)] px-3 py-2">
                <span className="w-[10px] h-[10px] rounded-full bg-[var(--text-subtle)] shrink-0 inline-block" />
                <div className="min-w-0">
                  <span className="text-[10px] text-[var(--text-muted)]">{p.label}</span>
                  <span className="text-[11px] text-[var(--text-secondary)] ml-1.5 truncate">{p.value}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}

/* ── Signal Evidence section for collectors not tied to scored services ── */

function SignalEvidenceSection({
  signalEvidence,
  serviceGaps,
}: {
  signalEvidence: SignalEvidence;
  serviceGaps: Record<string, ServiceGap>;
}) {
  const coveredCollectors = new Set<string>();
  for (const svc of Object.keys(serviceGaps)) {
    for (const col of SERVICE_COLLECTOR_MAP[svc] ?? []) {
      coveredCollectors.add(col);
    }
  }
  const uncovered = Object.entries(signalEvidence).filter(
    ([col, findings]) => !coveredCollectors.has(col) && findings.length > 0
  );
  if (uncovered.length === 0) return null;

  return (
    <Section icon={Search} label={`Additional Intelligence (${uncovered.length} sources)`} color="text-cyan-400">
      <div className="space-y-2">
        {uncovered.map(([col, findings]) => (
          <EvidenceCard
            key={col}
            item={{
              collector: col,
              collectorLabel: COLLECTOR_LABELS[col] ?? col,
              findings,
            }}
          />
        ))}
      </div>
    </Section>
  );
}

/* ── Tab: Outreach ── */

function OutreachTab({ outreach }: { outreach: DossierOutreach }) {
  const subjects = outreach.subjects ?? [];
  const bodies = outreach.bodies ?? [];

  if (outreach.skipped || (subjects.length === 0 && !outreach.linkedin_inmail && !outreach.voicemail_script)) {
    return <EmptyTab message="No outreach content generated" />;
  }

  return (
    <>
      {/* Email sequence */}
      {subjects.length > 0 && (
        <Section icon={Mail} label={`Email Sequence (${subjects.length}-touch)`} color="text-cyan-400">
          <div className="space-y-2">
            {subjects.map((subj, i) => (
              <div key={i} className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="flex items-center justify-center w-5 h-5 rounded-full bg-cyan-600/15 text-[11px] font-bold text-cyan-400">{i + 1}</span>
                  <span className="text-xs font-medium text-[var(--text-primary)]">{subj}</span>
                </div>
                <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed pl-7">{bodies[i] || ""}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* LinkedIn InMail */}
      {outreach.linkedin_inmail && (
        <Section icon={Linkedin} label="LinkedIn InMail" color="text-blue-400">
          <div className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
            <p className="text-xs text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">{outreach.linkedin_inmail}</p>
          </div>
        </Section>
      )}

      {/* Voicemail */}
      {outreach.voicemail_script && (
        <Section icon={Phone} label="Voicemail Script" color="text-amber-400">
          <div className="rounded-lg bg-[var(--surface-overlay)] px-4 py-3">
            <p className="text-xs text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">{outreach.voicemail_script}</p>
          </div>
        </Section>
      )}
    </>
  );
}

/* ── Activity Feed ── */

function ActivityFeed({ traces }: { traces: Trace[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
      <h3 className="flex items-center gap-2 text-xs font-medium text-[var(--text-secondary)] mb-3">
        <Radio size={13} className="text-blue-400" /> Live Activity
      </h3>
      <div className="relative">
        <div className="absolute left-[7px] top-1 bottom-1 w-px bg-[var(--border)]" />
        <div className="space-y-2">
          {traces.map((t) => {
            const st = STATUS_STYLES[t.status] || STATUS_STYLES.pending;
            const StIcon = st.icon;
            const AgentIcon = AGENT_ICONS[t.agent] || FileText;
            const elapsed = t.finished_at
              ? `${((new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()) / 1000).toFixed(1)}s`
              : t.status === "running"
              ? "running..."
              : "";
            const timeAgo = formatTimeAgo(t.started_at);

            return (
              <div key={t.id} className="relative pl-6">
                <div className={cn(
                  "absolute left-0 top-1.5 w-[15px] h-[15px] rounded-full border-2 bg-[var(--surface)] z-10 flex items-center justify-center",
                  t.status === "running" ? "border-blue-500" :
                  t.status === "completed" ? "border-emerald-500" :
                  t.status === "failed" ? "border-rose-500" :
                  "border-[var(--border-strong)]"
                )}>
                  {t.status === "running" && (
                    <div className="w-[5px] h-[5px] rounded-full bg-blue-400 animate-pulse" />
                  )}
                </div>
                <div className={cn(
                  "rounded-lg px-3 py-2.5",
                  t.status === "running" ? "bg-blue-950/30 border border-blue-800/30" : "bg-[var(--surface-overlay)]"
                )}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <AgentIcon size={12} className={st.color} />
                      <span className="text-xs font-medium text-[var(--text-primary)] capitalize">{t.agent}</span>
                      <StIcon size={11} className={cn(st.color, t.status === "running" && "animate-pulse")} />
                    </div>
                    <div className="flex items-center gap-2.5 text-[11px] text-[var(--text-muted)]">
                      {elapsed && <span>{elapsed}</span>}
                      {t.total_cost_cents > 0 && <span>{t.total_cost_cents}¢</span>}
                      <span>{timeAgo}</span>
                    </div>
                  </div>
                  {t.nodes_visited?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {t.nodes_visited.map((node, i) => (
                        <span
                          key={i}
                          className={cn(
                            "rounded-md px-2 py-0.5 text-[11px]",
                            i === t.nodes_visited.length - 1 && t.status === "running"
                              ? "bg-blue-600/15 text-blue-400 animate-pulse"
                              : "bg-[var(--surface-elevated)] text-[var(--text-muted)]"
                          )}
                        >
                          {node}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ── Shared components ── */

function Section({
  icon: Icon,
  label,
  color,
  children,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
      <h3 className="flex items-center gap-2 text-xs font-semibold text-[var(--text-secondary)] mb-3">
        <Icon size={14} className={color} />
        {label}
      </h3>
      {children}
    </section>
  );
}

function MiniCard({ icon: Icon, label, value }: { icon: React.ComponentType<{ size?: number; className?: string }>; label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)] mb-1">
        <Icon size={12} /> {label}
      </div>
      <div className="text-xs font-semibold text-[var(--text-primary)] truncate">{value}</div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <span className="text-[var(--text-muted)]">{label}:</span>{" "}
      <span className="text-[var(--text-primary)]">{value || "—"}</span>
    </div>
  );
}

function SignalBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-[11px] text-[var(--text-subtle)]">—</span>;
  const v = value.toLowerCase();
  const color =
    v === "strong" || v === "advanced" ? "text-emerald-400 bg-emerald-600/15" :
    v === "moderate" ? "text-amber-400 bg-amber-600/15" :
    v === "basic" ? "text-[var(--text-secondary)] bg-[var(--surface-elevated)]" :
    "text-[var(--text-muted)] bg-[var(--surface)]";
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", color)}>{value}</span>;
}

function ThreatBadge({ level }: { level: string | undefined }) {
  if (!level) return null;
  const l = level.toLowerCase();
  const color =
    l === "high" ? "text-rose-400 bg-rose-600/15 border-rose-500/20" :
    l === "medium" ? "text-amber-400 bg-amber-600/15 border-amber-500/20" :
    "text-emerald-400 bg-emerald-600/15 border-emerald-500/20";
  return <span className={cn("rounded-full border px-2.5 py-0.5 text-[11px] font-medium shrink-0", color)}>{level}</span>;
}

function EmptyTab({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-8 text-center">
      <p className="text-sm text-[var(--text-muted)]">{message}</p>
    </div>
  );
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function PeopleTab({ brandId }: { brandId: number }) {
  const [contacts, setContacts] = useState<import("@/lib/types").Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", title: "", email: "", linkedin_url: "" });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchApi<{ items: import("@/lib/types").Person[] }>(`/contacts?brand_id=${brandId}&limit=50`);
    if (data) setContacts(data.items ?? []);
    setLoading(false);
  }, [brandId]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    const res = await postApi(`/contacts`, { brand_id: brandId, ...form });
    if (res) {
      setForm({ name: "", title: "", email: "", linkedin_url: "" });
      setShowForm(false);
      load();
    }
    setSaving(false);
  };

  const handleVerify = async (id: number) => {
    try {
      await fetch(`${API_BASE}/contacts/${id}/verify`, {
        method: "PATCH",
        headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      });
      setContacts((prev) => prev.map((c) => c.id === id ? { ...c, verified: true } : c));
    } catch { /* noop */ }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={20} className="animate-spin text-[var(--text-muted)]" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          Contacts ({contacts.length})
        </h3>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 transition-colors"
        >
          {showForm ? "Cancel" : "+ Add"}
        </button>
      </div>

      {showForm && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-4 space-y-3">
          <input
            placeholder="Full name *"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-blue-500 focus:outline-none"
          />
          <div className="grid grid-cols-2 gap-3">
            <input
              placeholder="Title"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-blue-500 focus:outline-none"
            />
            <input
              placeholder="Email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-blue-500 focus:outline-none"
            />
          </div>
          <input
            placeholder="LinkedIn URL"
            value={form.linkedin_url}
            onChange={(e) => setForm({ ...form, linkedin_url: e.target.value })}
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-blue-500 focus:outline-none"
          />
          <button
            onClick={handleAdd}
            disabled={saving || !form.name.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50 transition-colors"
          >
            {saving ? "Saving..." : "Add Contact"}
          </button>
        </div>
      )}

      {contacts.length === 0 && !showForm ? (
        <EmptyTab message="No contacts found. Add contacts manually or run enrichment." />
      ) : (
        <div className="space-y-2">
          {contacts.map((c) => (
            <div
              key={c.id}
              className="flex items-start gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface-elevated)] p-3 hover:border-[var(--border-strong)] transition-colors"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500/20 to-indigo-500/20 text-sm font-semibold text-blue-400">
                {c.name.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-[var(--text-primary)] truncate">{c.name}</span>
                  {c.verified && (
                    <CheckCircle2 size={13} className="shrink-0 text-emerald-400" />
                  )}
                  <span className="ml-auto text-[10px] text-[var(--text-muted)]">
                    {Math.round(c.confidence * 100)}%
                  </span>
                </div>
                {c.title && (
                  <p className="text-xs text-[var(--text-secondary)] truncate">{c.title}</p>
                )}
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {c.email && (
                    <a
                      href={`mailto:${c.email}`}
                      className="flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300"
                    >
                      <Mail size={11} /> {c.email}
                    </a>
                  )}
                  {c.phone && (
                    <span className="flex items-center gap-1 text-[11px] text-[var(--text-secondary)]">
                      <Phone size={11} /> {c.phone}
                    </span>
                  )}
                  {c.linkedin_url && (
                    <a
                      href={c.linkedin_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300"
                    >
                      <Linkedin size={11} /> LinkedIn
                    </a>
                  )}
                </div>
              </div>
              {!c.verified && (
                <button
                  onClick={() => handleVerify(c.id)}
                  className="shrink-0 rounded-lg border border-emerald-500/30 px-2 py-1 text-[10px] font-medium text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                >
                  Verify
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TimelineTab({ brandId }: { brandId: number }) {
  const [events, setEvents] = useState<import("@/lib/types").LeadEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const data = await fetchApi<{ items: import("@/lib/types").LeadEvent[] }>(`/lifecycle/${brandId}/timeline`);
      if (data) setEvents(data.items ?? []);
      setLoading(false);
    })();
  }, [brandId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={20} className="animate-spin text-[var(--text-muted)]" />
      </div>
    );
  }

  if (events.length === 0) {
    return <EmptyTab message="No lifecycle events recorded yet." />;
  }

  const eventIcon = (type: string) => {
    switch (type) {
      case "status_change": return <Radio size={14} className="text-blue-400" />;
      case "dossier_generated": return <Brain size={14} className="text-purple-400" />;
      case "approval_created": return <CheckCircle2 size={14} className="text-amber-400" />;
      case "enrichment_run": return <Zap size={14} className="text-emerald-400" />;
      default: return <Clock size={14} className="text-[var(--text-muted)]" />;
    }
  };

  return (
    <div className="space-y-0">
      {events.map((ev, i) => (
        <div key={ev.id} className="flex gap-3">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--surface-overlay)] border border-[var(--border)]">
              {eventIcon(ev.event_type)}
            </div>
            {i < events.length - 1 && (
              <div className="w-px flex-1 bg-[var(--border)] my-1" />
            )}
          </div>
          <div className="pb-4 min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-[var(--text-primary)] capitalize">
                {ev.event_type.replace(/_/g, " ")}
              </span>
              <span className="text-[10px] text-[var(--text-muted)]">
                {formatTimeAgo(ev.created_at)}
              </span>
            </div>
            {ev.from_status && ev.to_status && (
              <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                <span className="capitalize">{ev.from_status}</span>
                {" → "}
                <span className="capitalize font-medium">{ev.to_status}</span>
              </p>
            )}
            {ev.note && (
              <p className="text-[11px] text-[var(--text-muted)] mt-0.5">{ev.note}</p>
            )}
            {ev.actor && (
              <span className="text-[10px] text-[var(--text-subtle)]">by {ev.actor}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
