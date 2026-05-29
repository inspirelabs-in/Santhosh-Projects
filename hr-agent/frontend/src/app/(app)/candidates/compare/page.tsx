"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { StatusTag, type Stage } from "@/components/status-tag";
import { SkeletonLines } from "@/components/skeleton";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface CompareItem {
  application_id: string;
  candidate: { id: string | null; name: string | null; email: string | null };
  role_title: string | null;
  current_stage: Stage;
  screening_score: number | null;
  verdict: string | null;
  verdict_rationale: string | null;
  logistics_values: {
    current_ctc_lpa?: number | null;
    expected_ctc_lpa?: number | null;
    notice_period_days?: number | null;
    current_location?: string | null;
    willing_to_relocate?: boolean | null;
  } | null;
  logistics_check: {
    ctc_in_range?: boolean;
    notice_acceptable?: boolean;
    location_workable?: boolean;
    rationale?: string;
  } | null;
  strengths: string[];
  red_flags: string[];
  profile: Record<string, any> | null;
  assignment_summary: string | null;
  assignment_highlights: string[];
  assignment_concerns: string[];
  per_question: Array<{
    question_id: string;
    score: number;
    relevance: string;
    notes: string;
  }>;
}

export default function ComparePage() {
  const sp = useSearchParams();
  const ids = sp.get("ids") ?? "";
  const { data, isLoading, error } = useSWR<CompareItem[]>(
    ids ? `/dashboard/v1/candidates-compare?ids=${ids}` : null,
    swrFetcher,
  );

  const count = data?.length ?? 0;

  return (
    <>
      <Topbar title="Compare candidates" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-[1400px] px-8 py-6">
          <Link
            href="/candidates"
            className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.15em] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" /> back to candidates
          </Link>

          <h1 className="mt-6 font-display text-[40px] font-normal leading-none">
            Side-by-side
          </h1>
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
            {count > 0 ? `${count} candidates` : "no candidates"}
          </p>

          {isLoading && (
            <div className="mt-8">
              <SkeletonLines lines={10} />
            </div>
          )}

          {error && (
            <p className="mt-8 text-sm text-destructive">
              Failed to load: {String((error as any)?.message ?? error)}
            </p>
          )}

          {!isLoading && data && data.length > 0 && (
            <CompareGrid items={data} />
          )}

          {!isLoading && data && data.length === 0 && (
            <p className="mt-8 text-sm text-muted-foreground">
              None of the selected candidates were found.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function CompareGrid({ items }: { items: CompareItem[] }) {
  const cols = items.length;
  const gridCols = cn(
    "mt-10 grid gap-4",
    cols === 2 && "grid-cols-[200px_1fr_1fr]",
    cols === 3 && "grid-cols-[200px_1fr_1fr_1fr]",
    cols === 4 && "grid-cols-[180px_1fr_1fr_1fr_1fr]",
  );

  return (
    <div className={gridCols}>
      {/* Header row */}
      <div />
      {items.map((it) => (
        <div key={it.application_id} className="rounded-lg border border-border bg-card p-4">
          <Link
            href={`/candidates/${it.application_id}`}
            className="group block"
          >
            <div className="font-display text-xl leading-tight group-hover:underline">
              {it.candidate.name ?? it.candidate.email ?? "Unnamed"}
              <ExternalLink className="ml-1 inline h-3 w-3 text-muted-foreground" />
            </div>
            <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
              {it.candidate.email ?? "no email"}
            </div>
          </Link>
          <div className="mt-3 flex items-center justify-between gap-2">
            <StatusTag stage={it.current_stage} />
            {it.screening_score != null && (
              <span className="font-display text-2xl tabular-nums">
                {it.screening_score}
              </span>
            )}
          </div>
          <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            {it.role_title ?? "no role"}
          </div>
        </div>
      ))}

      <Row label="Verdict" items={items} render={(it) => (
        <div>
          <div className="font-display text-base">
            {it.verdict ? it.verdict.replaceAll("_", " ") : "—"}
          </div>
          {it.verdict_rationale && (
            <p className="mt-1 text-xs text-muted-foreground">
              {it.verdict_rationale}
            </p>
          )}
        </div>
      )} />

      <Row label="Current CTC" items={items} render={(it) => (
        <Num v={it.logistics_values?.current_ctc_lpa} unit="LPA" />
      )} />

      <Row label="Expected CTC" items={items} render={(it) => (
        <Num v={it.logistics_values?.expected_ctc_lpa} unit="LPA" />
      )} />

      <Row label="Notice period" items={items} render={(it) => (
        <Num v={it.logistics_values?.notice_period_days} unit="days" />
      )} />

      <Row label="Location" items={items} render={(it) => (
        <span className="text-sm">
          {it.logistics_values?.current_location ?? it.profile?.location ?? "—"}
        </span>
      )} />

      <Row label="Experience" items={items} render={(it) => (
        <Num v={it.profile?.total_years_experience} unit="yrs" />
      )} />

      <Row label="Top skills" items={items} render={(it) => {
        const skills = (it.profile?.skills ?? []).slice(0, 5);
        if (skills.length === 0) return <span className="text-sm text-muted-foreground">—</span>;
        return (
          <div className="flex flex-wrap gap-1">
            {skills.map((s: string) => (
              <span key={s} className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px]">
                {s}
              </span>
            ))}
          </div>
        );
      }} />

      <Row label="Logistics fit" items={items} render={(it) => {
        const lc = it.logistics_check;
        if (!lc) return <span className="text-sm text-muted-foreground">—</span>;
        return (
          <div className="flex flex-wrap gap-2 font-mono text-[11px]">
            <Chip ok={lc.ctc_in_range} label="CTC" />
            <Chip ok={lc.notice_acceptable} label="notice" />
            <Chip ok={lc.location_workable} label="loc" />
          </div>
        );
      }} />

      <Row label="Strengths" items={items} render={(it) => (
        <BulletList items={it.strengths} tone="ok" empty="none recorded" />
      )} />

      <Row label="Red flags" items={items} render={(it) => (
        <BulletList items={it.red_flags} tone="warn" empty="none" />
      )} />

      <Row label="Per-question avg" items={items} render={(it) => {
        if (!it.per_question.length) return <span className="text-sm text-muted-foreground">—</span>;
        const avg =
          it.per_question.reduce((a, b) => a + b.score, 0) / it.per_question.length;
        const low = it.per_question.filter((q) => q.relevance === "low").length;
        return (
          <div className="text-sm">
            <span className="font-display text-xl tabular-nums">{avg.toFixed(1)}</span>
            <span className="ml-1 text-muted-foreground">/10</span>
            {low > 0 && (
              <span className="ml-2 font-mono text-[11px] text-warning">
                {low} low-relevance
              </span>
            )}
          </div>
        );
      }} />

      <Row label="Assignment" items={items} render={(it) => {
        if (!it.assignment_summary && !it.assignment_highlights.length) {
          return <span className="text-sm italic text-muted-foreground">not submitted</span>;
        }
        return (
          <div className="space-y-2">
            {it.assignment_summary && (
              <p className="text-xs text-muted-foreground">{it.assignment_summary}</p>
            )}
            {it.assignment_highlights.length > 0 && (
              <BulletList items={it.assignment_highlights} tone="ok" empty="" />
            )}
            {it.assignment_concerns.length > 0 && (
              <BulletList items={it.assignment_concerns} tone="warn" empty="" />
            )}
          </div>
        );
      }} />
    </div>
  );
}

function Row({
  label,
  items,
  render,
}: {
  label: string;
  items: CompareItem[];
  render: (it: CompareItem) => React.ReactNode;
}) {
  return (
    <>
      <div className="flex items-center border-t border-border py-4 pr-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      {items.map((it) => (
        <div key={it.application_id} className="border-t border-border py-4">
          {render(it)}
        </div>
      ))}
    </>
  );
}

function Num({ v, unit }: { v: number | null | undefined; unit: string }) {
  if (v == null) return <span className="text-sm text-muted-foreground">—</span>;
  return (
    <span className="font-display text-lg">
      {v}
      <span className="ml-1 font-mono text-[11px] text-muted-foreground">{unit}</span>
    </span>
  );
}

function Chip({ ok, label }: { ok?: boolean; label: string }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5",
        ok === true && "border-success/40 bg-success/10 text-success",
        ok === false && "border-destructive/40 bg-destructive/10 text-destructive",
        ok == null && "border-border text-muted-foreground",
      )}
    >
      {label}
    </span>
  );
}

function BulletList({
  items,
  tone,
  empty,
}: {
  items: string[];
  tone: "ok" | "warn";
  empty: string;
}) {
  if (!items || items.length === 0) {
    return empty ? <span className="text-sm italic text-muted-foreground">{empty}</span> : null;
  }
  return (
    <ul className="space-y-1 text-sm">
      {items.slice(0, 4).map((s, i) => (
        <li key={i} className="flex items-start gap-2">
          <span
            className={cn(
              "mt-1.5 h-1 w-1 shrink-0 rounded-full",
              tone === "ok" ? "bg-success" : "bg-destructive",
            )}
          />
          <span className="leading-snug">{s}</span>
        </li>
      ))}
    </ul>
  );
}
