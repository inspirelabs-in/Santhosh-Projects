"use client";

interface MeetingReport {
  overall_score?: number | null;
  technical_score?: number | null;
  communication_score?: number | null;
  confidence_score?: number | null;
  verdict?: string | null;
  summary?: string | null;
  strengths?: string[] | null;
  red_flags?: string[] | null;
  highlights?: string[] | null;
}

export function MeetingReportCard({ report }: { report: MeetingReport | null | undefined }) {
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
        <Score label="Technical" v={report.technical_score} />
        <Score label="Communication" v={report.communication_score} />
        <Score label="Confidence" v={report.confidence_score} />
      </div>
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

function Score({ label, v }: { label: string; v?: number | null }) {
  return (
    <div className="rounded-md bg-muted px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-sm font-bold tabular-nums">{v ?? "—"}</div>
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
