"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronRight,
  FileSearch,
  Fingerprint,
  Gauge,
  Scale,
  ShieldCheck,
} from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { cn, fmtRelative } from "@/lib/utils";
import {
  evidenceApi,
  type EvidenceChainResponse,
  type EvidenceItem,
  type DecisionItem,
  type ContradictionItem,
  type PipelineConfidenceResponse,
} from "@/lib/api/supervisor";

// ---------------------------------------------------------------------------
// Confidence gauge
// ---------------------------------------------------------------------------

function ConfidenceGauge({ value, recommendation }: { value: number; recommendation: string }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 85
      ? "text-success"
      : pct >= 60
        ? "text-warning"
        : "text-destructive";

  const recLabel =
    recommendation === "auto_advance"
      ? "Auto-advance"
      : recommendation === "escalate"
        ? "Escalate"
        : "Human review";

  const recColor =
    recommendation === "auto_advance"
      ? "bg-success/15 text-success"
      : recommendation === "escalate"
        ? "bg-destructive/15 text-destructive"
        : "bg-warning/15 text-warning";

  return (
    <div className="flex items-center gap-4">
      <div className="flex flex-col items-center">
        <span className={cn("text-3xl font-bold tabular-nums", color)}>
          {pct}%
        </span>
        <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          Pipeline confidence
        </span>
      </div>
      <span
        className={cn(
          "rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase",
          recColor,
        )}
      >
        {recLabel}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stage confidence bars
// ---------------------------------------------------------------------------

function StageConfidenceBars({
  stages,
}: {
  stages: PipelineConfidenceResponse["per_stage"];
}) {
  if (stages.length === 0) return null;

  return (
    <div className="space-y-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        Per-stage confidence
      </span>
      <div className="space-y-1.5">
        {stages.map((s) => {
          const pct = Math.round(s.avg_confidence * 100);
          return (
            <div key={s.stage} className="flex items-center gap-2">
              <span className="w-28 truncate text-xs text-muted-foreground">
                {s.stage}
              </span>
              <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    pct >= 85
                      ? "bg-success"
                      : pct >= 60
                        ? "bg-warning"
                        : "bg-destructive",
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="w-8 text-right font-mono text-[10px] tabular-nums">
                {pct}%
              </span>
              <span className="w-6 text-right font-mono text-[10px] text-muted-foreground tabular-nums">
                {s.evidence_count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Evidence list
// ---------------------------------------------------------------------------

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  const conf = item.confidence != null ? Math.round(item.confidence * 100) : null;

  return (
    <div className="border-b border-border">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="grid w-full grid-cols-[1fr_120px_100px_80px_120px] items-center gap-3 px-4 py-2.5 text-left text-sm transition hover:bg-muted/40"
      >
        <div className="flex items-center gap-2 min-w-0">
          {open ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate font-medium">{item.fact_key}</span>
        </div>
        <span className="truncate text-xs text-muted-foreground">
          {item.source_stage ?? "—"}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {item.extraction_method ?? "—"}
        </span>
        <span className="font-mono text-xs tabular-nums">
          {conf != null ? `${conf}%` : "—"}
        </span>
        <span className="text-xs text-muted-foreground">
          {fmtRelative(item.created_at)}
        </span>
      </button>

      {open && (
        <div className="bg-muted/20 px-8 py-3 space-y-2 text-sm">
          <div className="flex gap-4">
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Value
              </span>
              <p className="mt-0.5 font-semibold">
                {typeof item.fact_value === "object"
                  ? JSON.stringify(item.fact_value)
                  : String(item.fact_value ?? "—")}
              </p>
            </div>
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Source type
              </span>
              <p className="mt-0.5">{item.source_type ?? "—"}</p>
            </div>
          </div>
          {item.evidence_text && (
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Evidence text
              </span>
              <p className="mt-0.5 rounded bg-muted p-2 text-xs italic">
                &ldquo;{item.evidence_text}&rdquo;
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Decision list
// ---------------------------------------------------------------------------

function DecisionRow({ item }: { item: DecisionItem }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-border">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="grid w-full grid-cols-[1fr_120px_100px_120px] items-center gap-3 px-4 py-2.5 text-left text-sm transition hover:bg-muted/40"
      >
        <div className="flex items-center gap-2 min-w-0">
          {open ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate font-medium">{item.decision_type}</span>
        </div>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
            item.outcome === "pass" || item.outcome === "executed"
              ? "bg-success/15 text-success"
              : item.outcome === "reject" || item.outcome === "failed"
                ? "bg-destructive/15 text-destructive"
                : "bg-muted text-muted-foreground",
          )}
        >
          {item.outcome}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {item.evidence_ids.length} evidence
        </span>
        <span className="text-xs text-muted-foreground">
          {fmtRelative(item.created_at)}
        </span>
      </button>

      {open && (
        <div className="bg-muted/20 px-8 py-3 space-y-2 text-sm">
          {Object.keys(item.outcome_value).length > 0 && (
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Details
              </span>
              <pre className="mt-1 rounded-md bg-muted p-2 text-xs overflow-auto max-h-32">
                {JSON.stringify(item.outcome_value, null, 2)}
              </pre>
            </div>
          )}
          {item.policy_rule_ids.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Policy rules: {item.policy_rule_ids.join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contradiction list
// ---------------------------------------------------------------------------

function ContradictionRow({ item }: { item: ContradictionItem }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-semibold">{item.fact_key ?? "Unknown fact"}</p>
        <div className="flex gap-4 text-xs">
          <span>
            <span className="text-muted-foreground">{item.old_source_stage}:</span>{" "}
            <span className="font-mono font-semibold">
              {String(item.old_value ?? "—")}
            </span>
          </span>
          <span className="text-muted-foreground">→</span>
          <span>
            <span className="text-muted-foreground">{item.new_source_stage}:</span>{" "}
            <span className="font-mono font-semibold text-destructive">
              {String(item.new_value ?? "—")}
            </span>
          </span>
        </div>
        <p className="text-[10px] text-muted-foreground">
          {fmtRelative(item.created_at)}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function EvidencePanel({ applicationId }: { applicationId: string }) {
  const { data: chain, isLoading: chainLoading } = useSWR(
    `/evidence-chain/${applicationId}`,
    () => evidenceApi.chain(applicationId),
    { refreshInterval: 30_000 },
  );

  const { data: confidence, isLoading: confLoading } = useSWR(
    `/confidence/${applicationId}`,
    () => evidenceApi.confidence(applicationId),
    { refreshInterval: 30_000 },
  );

  const isLoading = chainLoading || confLoading;

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="h-20 rounded-lg skeleton" />
        <div className="h-40 rounded-lg skeleton" />
      </div>
    );
  }

  const evidence = chain?.evidence ?? [];
  const decisions = chain?.decisions ?? [];
  const contradictions = chain?.contradictions ?? [];

  return (
    <div className="space-y-4">
      {/* Confidence overview */}
      {confidence && (
        <Card>
          <CardContent className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Gauge className="h-4 w-4 text-primary" />
                <span className="text-sm font-bold uppercase tracking-[0.12em]">
                  Pipeline confidence
                </span>
              </div>
            </div>
            <ConfidenceGauge
              value={confidence.overall}
              recommendation={confidence.recommendation}
            />
            <StageConfidenceBars stages={confidence.per_stage} />
          </CardContent>
        </Card>
      )}

      {/* Contradictions alert */}
      {contradictions.length > 0 && (
        <Card className="border-destructive/30">
          <CardContent className="p-5 space-y-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-destructive" />
              <span className="text-sm font-bold uppercase tracking-[0.12em] text-destructive">
                Contradictions ({contradictions.length})
              </span>
            </div>
            <div className="space-y-2">
              {contradictions.map((c) => (
                <ContradictionRow key={c.id} item={c} />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Evidence + Decisions tabs */}
      <Tabs defaultValue="evidence">
        <TabsList>
          <TabsTrigger value="evidence">
            <FileSearch className="mr-1 h-3.5 w-3.5" />
            Evidence ({evidence.length})
          </TabsTrigger>
          <TabsTrigger value="decisions">
            <Scale className="mr-1 h-3.5 w-3.5" />
            Decisions ({decisions.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="evidence" className="mt-3">
          {evidence.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
                <Fingerprint className="h-8 w-8 text-primary" />
                <p className="text-sm font-bold">No evidence collected yet</p>
                <p className="text-xs text-muted-foreground">
                  Evidence records appear as the candidate progresses through the pipeline.
                </p>
              </CardContent>
            </Card>
          ) : (
            <Card className="overflow-hidden">
              <div className="grid grid-cols-[1fr_120px_100px_80px_120px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                <span>Fact</span>
                <span>Stage</span>
                <span>Method</span>
                <span>Conf.</span>
                <span>When</span>
              </div>
              <div>
                {evidence.map((e) => (
                  <EvidenceRow key={e.id} item={e} />
                ))}
              </div>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="decisions" className="mt-3">
          {decisions.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
                <Brain className="h-8 w-8 text-primary" />
                <p className="text-sm font-bold">No decisions recorded yet</p>
              </CardContent>
            </Card>
          ) : (
            <Card className="overflow-hidden">
              <div className="grid grid-cols-[1fr_120px_100px_120px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                <span>Decision</span>
                <span>Outcome</span>
                <span>Evidence</span>
                <span>When</span>
              </div>
              <div>
                {decisions.map((d) => (
                  <DecisionRow key={d.id} item={d} />
                ))}
              </div>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
