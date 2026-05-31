"use client";

import { useMemo } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { DossierScore, ScoreBreakdown, ServiceGap } from "@/lib/types";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const FACTORS = [
  { key: "budget_potential", label: "Budget" },
  { key: "growth_stage_fit", label: "Growth Fit" },
  { key: "marketing_maturity_gap", label: "Maturity Gap" },
  { key: "competitive_pressure", label: "Competition" },
  { key: "urgency", label: "Urgency" },
  { key: "brand_fit", label: "Brand Fit" },
  { key: "expansion_likelihood", label: "Expansion" },
  { key: "channel_weakness_severity", label: "Channel Gaps" },
  { key: "decision_maker_reach", label: "DM Reach" },
] as const;

const FACTOR_COUNT = FACTORS.length;
const ANGLE_STEP = (2 * Math.PI) / FACTOR_COUNT;
const RADAR_SIZE = 260;
const CX = RADAR_SIZE / 2;
const CY = RADAR_SIZE / 2;
const R = 100; // max radius

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function polarToXY(cx: number, cy: number, r: number, angle: number) {
  return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
}

function buildPolygon(scores: number[], radius: number): string {
  return scores
    .map((s, i) => {
      const angle = -Math.PI / 2 + i * ANGLE_STEP;
      const { x, y } = polarToXY(CX, CY, radius * s, angle);
      return `${x},${y}`;
    })
    .join(" ");
}

function scoreColor(s: number) {
  if (s >= 0.7) return "text-emerald-400";
  if (s >= 0.4) return "text-amber-400";
  return "text-rose-400";
}

function scoreBg(s: number) {
  if (s >= 0.7) return "bg-emerald-500";
  if (s >= 0.4) return "bg-amber-500";
  return "bg-rose-500";
}

function priorityBadge(p: string | undefined) {
  switch (p?.toLowerCase()) {
    case "high":
      return "bg-rose-500/15 text-rose-400";
    case "medium":
      return "bg-amber-500/15 text-amber-400";
    case "low":
      return "bg-blue-500/15 text-blue-400";
    default:
      return "bg-neutral-500/15 text-neutral-400";
  }
}

function tierLabel(tier: string | undefined) {
  switch (tier) {
    case "hot":
      return { label: "HOT", cls: "bg-rose-500/20 text-rose-400 border-rose-500/40" };
    case "warm":
      return { label: "WARM", cls: "bg-amber-500/20 text-amber-400 border-amber-500/40" };
    case "watchlist":
      return { label: "WATCHLIST", cls: "bg-blue-500/20 text-blue-400 border-blue-500/40" };
    case "park":
      return { label: "PARK", cls: "bg-neutral-500/20 text-neutral-400 border-neutral-500/40" };
    default:
      return { label: tier?.toUpperCase() ?? "N/A", cls: "bg-neutral-500/20 text-neutral-400 border-neutral-500/40" };
  }
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function RadarChart({ breakdown }: { breakdown: Record<string, ScoreBreakdown> }) {
  const scores = FACTORS.map((f) => breakdown[f.key]?.score ?? 0);

  const outerPoly = buildPolygon(Array(FACTOR_COUNT).fill(1), R);
  const midPoly = buildPolygon(Array(FACTOR_COUNT).fill(0.5), R);
  const scorePoly = buildPolygon(scores, R);

  // grid lines from center to each vertex
  const gridLines = FACTORS.map((_, i) => {
    const angle = -Math.PI / 2 + i * ANGLE_STEP;
    const end = polarToXY(CX, CY, R, angle);
    return { x2: end.x, y2: end.y };
  });

  // labels outside the radar
  const labels = FACTORS.map((f, i) => {
    const angle = -Math.PI / 2 + i * ANGLE_STEP;
    const pos = polarToXY(CX, CY, R * 1.25, angle);
    const anchor =
      Math.abs(pos.x - CX) < 5 ? "middle" : pos.x > CX ? "start" : "end";
    return { ...f, x: pos.x, y: pos.y, anchor };
  });

  return (
    <div className="flex items-center justify-center">
      <svg
        width={RADAR_SIZE}
        height={RADAR_SIZE}
        viewBox={`-20 -20 ${RADAR_SIZE + 40} ${RADAR_SIZE + 40}`}
        className="overflow-visible"
      >
        {/* grid rings */}
        <polygon
          points={outerPoly}
          fill="none"
          stroke="var(--border)"
          strokeWidth={1}
        />
        <polygon
          points={midPoly}
          fill="none"
          stroke="var(--border)"
          strokeWidth={0.5}
          strokeDasharray="3 3"
        />

        {/* grid spokes */}
        {gridLines.map((l, i) => (
          <line
            key={i}
            x1={CX}
            y1={CY}
            x2={l.x2}
            y2={l.y2}
            stroke="var(--border)"
            strokeWidth={0.5}
          />
        ))}

        {/* score area */}
        <motion.polygon
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          points={scorePoly}
          fill="rgba(59,130,246,0.2)"
          stroke="rgb(59,130,246)"
          strokeWidth={1.5}
        />

        {/* dots on vertices */}
        {scores.map((s, i) => {
          const angle = -Math.PI / 2 + i * ANGLE_STEP;
          const { x, y } = polarToXY(CX, CY, R * s, angle);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={3}
              fill="rgb(59,130,246)"
              stroke="var(--surface)"
              strokeWidth={1.5}
            />
          );
        })}

        {/* labels */}
        {labels.map((l) => (
          <text
            key={l.key}
            x={l.x}
            y={l.y}
            textAnchor={l.anchor as "start" | "middle" | "end"}
            dominantBaseline="central"
            fill="var(--text-secondary)"
            fontSize={10}
            fontWeight={500}
          >
            {l.label}
          </text>
        ))}
      </svg>
    </div>
  );
}

function FactorBars({ breakdown }: { breakdown: Record<string, ScoreBreakdown> }) {
  return (
    <div className="space-y-2">
      {FACTORS.map((f) => {
        const entry = breakdown[f.key];
        const s = entry?.score ?? 0;
        return (
          <div key={f.key} className="grid grid-cols-[120px_1fr_1fr] gap-3 items-center text-xs">
            <span className="text-[var(--text-secondary)] truncate">{f.label}</span>
            <div className="h-2 rounded-full bg-[var(--surface-elevated)] overflow-hidden">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${s * 100}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
                className={cn("h-full rounded-full", scoreBg(s))}
              />
            </div>
            <span className="text-[var(--text-muted)] truncate text-[11px]">
              {entry?.evidence ?? "No evidence"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ServiceGapGrid({ gaps }: { gaps: Record<string, ServiceGap> }) {
  const sorted = useMemo(() => {
    return Object.entries(gaps)
      .map(([name, g]) => ({ name, ...g }))
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }, [gaps]);

  if (sorted.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {sorted.map((g) => (
        <div
          key={g.name}
          className="rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)] p-3 space-y-2"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-[var(--text-primary)]">
              {g.name}
            </span>
            <div className="flex items-center gap-2">
              <span className={cn("text-xs font-mono", scoreColor((g.score ?? 0) / 100))}>
                {g.score ?? 0}
              </span>
              <span
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded-full font-medium",
                  priorityBadge(g.priority)
                )}
              >
                {g.priority ?? "—"}
              </span>
            </div>
          </div>
          <p className="text-[11px] text-[var(--text-muted)] line-clamp-2">
            {g.evidence ?? "No evidence available"}
          </p>
        </div>
      ))}
    </div>
  );
}

function ConfidenceMeter({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const circumference = 2 * Math.PI * 40;
  const dashOffset = circumference * (1 - confidence);

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width={100} height={100} viewBox="0 0 100 100">
        {/* background ring */}
        <circle
          cx={50}
          cy={50}
          r={40}
          fill="none"
          stroke="var(--border)"
          strokeWidth={6}
        />
        {/* value ring */}
        <motion.circle
          cx={50}
          cy={50}
          r={40}
          fill="none"
          stroke="rgb(59,130,246)"
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: dashOffset }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          transform="rotate(-90 50 50)"
        />
        {/* percentage text */}
        <text
          x={50}
          y={50}
          textAnchor="middle"
          dominantBaseline="central"
          fill="var(--text-primary)"
          fontSize={20}
          fontWeight={700}
        >
          {pct}%
        </text>
      </svg>
      <span className="text-xs text-[var(--text-muted)]">Confidence</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

type ScoreChartProps = {
  score: DossierScore;
};

export function ScoreChart({ score }: ScoreChartProps) {
  const breakdown = score.breakdown ?? {};
  const serviceGaps = score.service_gaps ?? {};
  const total = score.total ?? 0;
  const tier = tierLabel(score.tier);
  const confidence = score.confidence ?? 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className="space-y-6"
    >
      {/* Overall score + tier badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <span className="text-5xl font-bold text-[var(--text-primary)] tabular-nums">
            {total}
          </span>
          <span className="text-sm text-[var(--text-muted)]">/ 100</span>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "text-xs font-semibold px-2.5 py-1 rounded-full border",
              tier.cls
            )}
          >
            {tier.label}
          </span>
          <ConfidenceMeter confidence={confidence} />
        </div>
      </div>

      {/* Radar chart */}
      {Object.keys(breakdown).length > 0 && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
          <h3 className="text-sm font-medium text-[var(--text-primary)] mb-4">
            Score Breakdown
          </h3>
          <RadarChart breakdown={breakdown} />
        </div>
      )}

      {/* Factor bars */}
      {Object.keys(breakdown).length > 0 && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
          <h3 className="text-sm font-medium text-[var(--text-primary)] mb-3">
            Factor Details
          </h3>
          <FactorBars breakdown={breakdown} />
        </div>
      )}

      {/* Service gap grid */}
      {Object.keys(serviceGaps).length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-[var(--text-primary)] mb-3">
            Service Gaps
          </h3>
          <ServiceGapGrid gaps={serviceGaps} />
        </div>
      )}
    </motion.div>
  );
}
