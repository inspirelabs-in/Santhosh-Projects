"use client";

import type { CSSProperties } from "react";
import { Check, X } from "lucide-react";
import { cn } from "@/lib/utils";

/** A mini resume card: avatar dot + a few text lines, styled for a white card. */
function ResumeChip({ tone = "idle", style }: { tone?: "idle" | "keep"; style?: CSSProperties }) {
  return (
    <div
      className={cn(
        "w-[38px] rounded-md border bg-white p-1.5 shadow-sm",
        tone === "keep" ? "border-brand-green" : "border-border",
      )}
      style={style}
    >
      <div className="mb-1 h-1.5 w-1.5 rounded-full bg-brand-green" />
      <div className="mb-1 h-[3px] w-full rounded bg-foreground/15" />
      <div className="mb-1 h-[3px] w-3/4 rounded bg-foreground/10" />
      <div className="h-[3px] w-5/6 rounded bg-foreground/10" />
    </div>
  );
}

/** A few keep the standard, many don't. Start x spreads across the mouth. */
const KEEP = [
  { x: "46%", d: "0s" },
  { x: "54%", d: "1.4s" },
  { x: "40%", d: "2.6s" },
];
const REJECT = [
  { x: "10%", d: "0.3s" },
  { x: "24%", d: "0.8s" },
  { x: "38%", d: "1.2s" },
  { x: "62%", d: "1.7s" },
  { x: "78%", d: "2.1s" },
  { x: "90%", d: "2.5s" },
  { x: "18%", d: "2.9s" },
  { x: "70%", d: "3.3s" },
  { x: "50%", d: "3.6s" },
];

function flyStyle(x: string, delay: string): CSSProperties {
  return { "--x0": x, animationDelay: delay } as CSSProperties;
}

/** Resumes pour into a funnel, converge at the relevancy neck, then split:
 *  most veer left and drop (not a fit), a few veer right to the shortlist. */
export function FunnelScreen() {
  return (
    <div className="relative h-[212px] w-full overflow-hidden">
      {/* Funnel + diverging chutes */}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <linearGradient id="lp-funnel-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(85 100% 33%)" stopOpacity="0.10" />
            <stop offset="100%" stopColor="hsl(85 100% 33%)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* converging funnel */}
        <path
          d="M6,10 L94,10 L54,48 L46,48 Z"
          fill="url(#lp-funnel-fill)"
          stroke="hsl(85 100% 33%)"
          strokeOpacity="0.4"
          strokeWidth="0.6"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        {/* reject chute (left) */}
        <path d="M47,50 L20,92" stroke="hsl(218 80% 12%)" strokeOpacity="0.18" strokeWidth="0.6"
          strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
        {/* shortlist chute (right) */}
        <path d="M53,50 L80,92" stroke="hsl(85 100% 33%)" strokeOpacity="0.45" strokeWidth="0.6"
          strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      </svg>

      {/* Relevancy glow at the neck */}
      <div className="lp-neck-glow absolute left-1/2 top-[46%] h-7 w-7 -translate-x-1/2 rounded-full bg-brand-green/40 blur-md" />

      {/* Rejected resumes (the many) veer left */}
      {REJECT.map((r, i) => (
        <div key={`r${i}`} className="lp-funnel-reject absolute" style={flyStyle(r.x, r.d)}>
          <div className="relative">
            <ResumeChip />
            <span
              className="lp-badge-keep absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-muted text-muted-foreground ring-1 ring-border"
              style={{ animationDelay: r.d }}
            >
              <X className="h-2.5 w-2.5" />
            </span>
          </div>
        </div>
      ))}

      {/* Kept resumes (the few) veer right */}
      {KEEP.map((k, i) => (
        <div key={`k${i}`} className="lp-funnel-keep absolute" style={flyStyle(k.x, k.d)}>
          <div className="relative">
            <ResumeChip tone="keep" />
            <span
              className="lp-badge-keep absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-brand-green text-white"
              style={{ animationDelay: k.d }}
            >
              <Check className="h-2.5 w-2.5" />
            </span>
          </div>
        </div>
      ))}

      {/* Top counter */}
      <div className="absolute left-1/2 top-1 -translate-x-1/2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span className="text-foreground">1,284</span> applications
      </div>

      {/* Outcome labels: reject left, shortlist right */}
      <div className="absolute bottom-1 left-2 inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
        <X className="h-3 w-3" />
        Not a fit
      </div>
      <div className="absolute bottom-1 right-2 inline-flex items-center gap-1.5 rounded-full bg-brand-green/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-brand-green">
        <Check className="h-3 w-3" />
        12 shortlisted
      </div>
    </div>
  );
}
