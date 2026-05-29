"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Visualises a meeting/voice-call emotion timeline as a stacked area chart.
 *
 * Input shape mirrors ``MeetingAnalysis.candidate_emotion_timeline``:
 *   { t_start_sec, t_end_sec, speaker, emotion, confidence }
 *
 * Each emotion bucket gets one series. Confidence is plotted on the y-axis.
 * Hovering a region shows the exact emotion + confidence at that timestamp.
 */

export interface EmotionTimelineEntry {
  t_start_sec: number;
  t_end_sec: number;
  speaker: string;
  emotion: string;
  confidence: number;
}

const EMOTION_COLORS: Record<string, string> = {
  calm: "hsl(var(--success))",
  engaged: "hsl(var(--primary))",
  happy: "hsl(var(--accent))",
  neutral: "hsl(var(--muted-foreground))",
  anxious: "hsl(var(--warning))",
  frustrated: "hsl(var(--destructive))",
  evasive: "hsl(var(--destructive) / 0.7)",
  fearful: "hsl(var(--warning))",
  sad: "hsl(var(--info))",
  angry: "hsl(var(--destructive))",
  surprised: "hsl(var(--accent))",
  disgust: "hsl(var(--destructive) / 0.6)",
};

function colorFor(emotion: string): string {
  return EMOTION_COLORS[emotion.toLowerCase()] ?? "hsl(var(--muted-foreground))";
}

function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function EmotionTimelineChart({
  entries,
  height = 220,
}: {
  entries: EmotionTimelineEntry[];
  height?: number;
}) {
  // Bucket into 1s samples so Recharts has a flat x-domain to plot. We pick
  // the dominant emotion in each bucket and use its confidence as the y.
  const points = useMemo(() => {
    if (!entries.length) return [];
    const maxT = Math.max(...entries.map((e) => e.t_end_sec));
    const buckets = Math.max(1, Math.ceil(maxT));
    const out: Array<{ t: number; emotion: string; confidence: number }> = [];
    for (let i = 0; i < buckets; i += 1) {
      const active = entries.find((e) => e.t_start_sec <= i && e.t_end_sec >= i);
      if (active) {
        out.push({ t: i, emotion: active.emotion, confidence: active.confidence });
      } else {
        out.push({ t: i, emotion: "neutral", confidence: 0 });
      }
    }
    return out;
  }, [entries]);

  if (entries.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
        No emotion timeline available for this session.
      </div>
    );
  }

  // Distinct emotions in the data, in their first-seen order.
  const series = Array.from(
    new Set(entries.map((e) => e.emotion.toLowerCase())),
  );

  // For Recharts: each row gets a column per emotion containing the
  // confidence when that emotion is active, else 0. Stacked areas paint the
  // active emotion at each timestamp.
  const data = points.map((p) => {
    const row: Record<string, number> = { t: p.t };
    for (const e of series) {
      row[e] = p.emotion.toLowerCase() === e ? p.confidence : 0;
    }
    return row;
  });

  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
          <XAxis
            dataKey="t"
            tickFormatter={fmtTime}
            stroke="hsl(var(--muted-foreground))"
            fontSize={10}
            tickMargin={6}
          />
          <YAxis
            domain={[0, 1]}
            stroke="hsl(var(--muted-foreground))"
            fontSize={10}
            tickFormatter={(v) => `${Math.round(v * 100)}%`}
          />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(label) => `t = ${fmtTime(label as number)}`}
            formatter={(value, name) => {
              const v = typeof value === "number" ? value : Number(value ?? 0);
              const n = typeof name === "string" ? name : String(name ?? "");
              return v > 0 ? [`${Math.round(v * 100)}%`, n] : ["—", n];
            }}
          />
          {series.map((emo) => (
            <Area
              key={emo}
              type="step"
              dataKey={emo}
              stackId="1"
              stroke={colorFor(emo)}
              fill={colorFor(emo)}
              fillOpacity={0.45}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {series.map((emo) => (
          <span key={emo} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: colorFor(emo) }}
            />
            {emo}
          </span>
        ))}
      </div>
    </div>
  );
}
