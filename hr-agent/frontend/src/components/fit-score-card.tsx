"use client";

interface FitBreakdown {
  overall_score?: number | null;
  dimensions?: Record<string, number | null> | null;
  red_flags?: string[] | null;
  green_flags?: string[] | null;
  summary?: string | null;
  knock_outs?: string[] | null;
  deterministic_tier?: string | null;
  weights_used?: Record<string, number> | null;
  scored_at?: string | null;
}

interface Props {
  score: number | null | undefined;
  tier: string | null | undefined;
  breakdown?: FitBreakdown | null;
}

const TIER_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  green: { bg: "bg-emerald-100 border-emerald-300", text: "text-emerald-700", label: "Green" },
  amber: { bg: "bg-amber-100 border-amber-300", text: "text-amber-700", label: "Amber" },
  red: { bg: "bg-rose-100 border-rose-300", text: "text-rose-700", label: "Red" },
};

export function FitScoreCard({ score, tier, breakdown }: Props) {
  if (score == null && !tier && !breakdown) return null;

  const tierKey = (tier || "").toLowerCase();
  const tierStyle = TIER_STYLES[tierKey] ?? {
    bg: "bg-muted border-border",
    text: "text-foreground",
    label: tier || "—",
  };
  const dims = breakdown?.dimensions ?? {};

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">
            Resume vs JD fit
          </div>
          <div className="mt-1 flex items-baseline gap-3">
            <span className="text-4xl font-extrabold tabular-nums">
              {score ?? "—"}
              <span className="text-lg font-normal text-muted-foreground">/100</span>
            </span>
            <span
              className={`rounded-full border px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider ${tierStyle.bg} ${tierStyle.text}`}
            >
              {tierStyle.label}
            </span>
          </div>
        </div>
        {breakdown?.scored_at ? (
          <span className="text-[10px] font-mono uppercase text-muted-foreground">
            scored {new Date(breakdown.scored_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
          </span>
        ) : null}
      </div>

      {breakdown?.summary ? (
        <p className="mt-3 text-sm text-foreground">{breakdown.summary}</p>
      ) : null}

      {Object.keys(dims).length > 0 ? (
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {Object.entries(dims).map(([k, v]) => (
            <Dimension key={k} label={k} value={v} weight={breakdown?.weights_used?.[k]} />
          ))}
        </div>
      ) : null}

      {breakdown?.green_flags?.length ? (
        <Flags title="Green flags" items={breakdown.green_flags} tone="ok" />
      ) : null}
      {breakdown?.red_flags?.length ? (
        <Flags title="Red flags" items={breakdown.red_flags} tone="bad" />
      ) : null}
      {breakdown?.knock_outs?.length ? (
        <Flags title="Knock-outs" items={breakdown.knock_outs} tone="bad" />
      ) : null}
    </div>
  );
}

function Dimension({
  label,
  value,
  weight,
}: {
  label: string;
  value: number | null | undefined;
  weight?: number;
}) {
  const v = typeof value === "number" ? value : null;
  const pct = v != null ? Math.max(0, Math.min(100, v)) : 0;
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {label.replace(/_/g, " ")}
        </span>
        {weight != null ? (
          <span className="text-[10px] text-muted-foreground">w {weight}</span>
        ) : null}
      </div>
      <div className="mt-1 text-lg font-bold tabular-nums">
        {v ?? "—"}
        <span className="text-xs font-normal text-muted-foreground">/100</span>
      </div>
      <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Flags({ title, items, tone }: { title: string; items: string[]; tone: "ok" | "bad" }) {
  const dot = tone === "ok" ? "bg-emerald-500" : "bg-rose-500";
  return (
    <div className="mt-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">
        {title}
      </div>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-sm">
            <span className={`mt-1.5 inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
