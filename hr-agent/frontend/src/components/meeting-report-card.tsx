"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface ScoreRationale {
  overall?: string;
  technical?: string;
  communication?: string;
  confidence?: string;
}

interface MeetingReport {
  overall_score?: number | null;
  technical_score?: number | null;
  communication_score?: number | null;
  confidence_score?: number | null;
  score_rationale?: ScoreRationale | null;
  verdict?: string | null;
  summary?: string | null;
  strengths?: string[] | null;
  red_flags?: string[] | null;
  highlights?: string[] | null;
}

export function MeetingReportCard({ report }: { report: MeetingReport | null | undefined }) {
  const [showReasoning, setShowReasoning] = useState(false);

  if (!report) {
    return (
      <p className="text-sm text-muted-foreground">No meeting report yet.</p>
    );
  }
  const verdictColor =
    report.verdict === "clear_pass"
      ? "text-emerald-600"
      : report.verdict === "clear_reject"
        ? "text-rose-600"
        : "text-amber-600";

  const rationale = report.score_rationale;
  const hasRationale = rationale && (rationale.overall || rationale.technical || rationale.communication || rationale.confidence);

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-2xl font-bold tabular-nums">
          {report.overall_score ?? "—"}
          <span className="text-sm font-normal text-muted-foreground">/100</span>
        </span>
        {report.verdict ? (
          <span className={`text-xs font-mono uppercase tracking-wider ${verdictColor}`}>
            {report.verdict.replace("_", " ")}
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Score label="Technical" v={report.technical_score} rationale={rationale?.technical} />
        <Score label="Communication" v={report.communication_score} rationale={rationale?.communication} />
        <Score label="Confidence" v={report.confidence_score} rationale={rationale?.confidence} />
      </div>
      {hasRationale ? (
        <div>
          <button
            type="button"
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
          >
            {showReasoning ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            Score Reasoning
          </button>
          {showReasoning ? (
            <div className="mt-2 space-y-2 text-sm text-muted-foreground">
              {rationale?.overall ? (
                <div>
                  <span className="font-semibold text-foreground">Overall:</span> {rationale.overall}
                </div>
              ) : null}
              {rationale?.technical ? (
                <div>
                  <span className="font-semibold text-foreground">Technical:</span> {rationale.technical}
                </div>
              ) : null}
              {rationale?.communication ? (
                <div>
                  <span className="font-semibold text-foreground">Communication:</span> {rationale.communication}
                </div>
              ) : null}
              {rationale?.confidence ? (
                <div>
                  <span className="font-semibold text-foreground">Confidence:</span> {rationale.confidence}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {report.summary ? (
        <p className="text-sm text-foreground">{report.summary}</p>
      ) : null}
      {report.strengths?.length ? (
        <Bullets title="Strengths" items={report.strengths} tone="ok" />
      ) : null}
      {report.red_flags?.length ? (
        <Bullets title="Red flags" items={report.red_flags} tone="bad" />
      ) : null}
      {report.highlights?.length ? (
        <Bullets title="Highlights" items={report.highlights} tone="info" />
      ) : null}
    </div>
  );
}

function Score({ label, v, rationale }: { label: string; v?: number | null; rationale?: string | null }) {
  const [showTip, setShowTip] = useState(false);
  return (
    <div
      className="rounded-md bg-muted px-2 py-1.5 relative cursor-help"
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-sm font-bold tabular-nums">{v ?? "—"}</div>
      {showTip && rationale ? (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 w-56 rounded-md border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-lg z-10">
          {rationale}
        </div>
      ) : null}
    </div>
  );
}

function Bullets({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "ok" | "bad" | "info";
}) {
  const dot =
    tone === "ok" ? "bg-emerald-500" : tone === "bad" ? "bg-rose-500" : "bg-sky-500";
  return (
    <div>
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
