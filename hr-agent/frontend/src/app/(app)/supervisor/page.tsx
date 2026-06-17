"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  Gauge,
  Heart,
  HeartPulse,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Target,
  TrendingUp,
  X,
  Zap,
  Activity,
  AlertTriangle,
  BarChart3,
  Clock,
  Mail,
  MessageSquare,
  Phone,
  Users,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Topbar } from "@/components/layout/topbar";
import { fmtRelative, cn } from "@/lib/utils";
import {
  supervisorApi,
  experimentApi,
  type SupervisorAction,
  type SupervisorEvent,
  type AccuracyReport,
  type ActionListResponse,
  type EventListResponse,
  type ExperimentSummary,
} from "@/lib/api/supervisor";
import { ApiError } from "@/lib/api";

// ---------------------------------------------------------------------------
// Safe fetchers: swallow 404/5xx so SWR refreshInterval doesn't spam console
// ---------------------------------------------------------------------------

const EMPTY_ACTIONS: ActionListResponse = { actions: [], total: 0 };
const EMPTY_EVENTS: EventListResponse = { events: [], total: 0 };
const EMPTY_ACCURACY: AccuracyReport = {
  total_proposals: 0, approved: 0, rejected: 0, pending: 0, executed: 0, approval_rate: null,
};

async function safeFetch<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status >= 500)) {
      return fallback;
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// KPI strip
// ---------------------------------------------------------------------------

function KpiCard({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string | number;
  icon: typeof Bot;
  accent?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <span
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            accent ?? "bg-primary/10 text-primary",
          )}
        >
          <Icon className="h-5 w-5" />
        </span>
        <div>
          <p className="text-2xl font-bold tabular-nums">{value}</p>
          <p className="text-xs text-muted-foreground uppercase tracking-[0.1em]">
            {label}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Confidence ring (small SVG gauge)
// ---------------------------------------------------------------------------

function ConfidenceRing({ value, size = 36 }: { value: number; size?: number }) {
  const pct = Math.round(value * 100);
  const r = (size - 4) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (circ * pct) / 100;
  const color =
    pct >= 85 ? "text-success" : pct >= 60 ? "text-warning" : "text-destructive";

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={3} className="text-muted/30" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={3} strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" className={color} />
      </svg>
      <span className="absolute font-mono text-[9px] font-bold tabular-nums">{pct}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Decision Trail Item — perceive → reason → act narrative
// ---------------------------------------------------------------------------

function DecisionTrailItem({ action }: { action: SupervisorAction }) {
  const [expanded, setExpanded] = useState(false);

  const statusIcon = action.approved_by
    ? <ShieldCheck className="h-4 w-4 text-success" />
    : action.rejected_by
      ? <ShieldAlert className="h-4 w-4 text-destructive" />
      : action.executed
        ? <Zap className="h-4 w-4 text-primary" />
        : <Eye className="h-4 w-4 text-warning" />;

  const reasoning = action.reasoning ?? "";
  const parts = reasoning.split(/(?=PERCEIVE:|REASON:|ACT:)/i);
  const perceive = parts.find((p) => /^PERCEIVE:/i.test(p))?.replace(/^PERCEIVE:\s*/i, "") ?? null;
  const reason = parts.find((p) => /^REASON:/i.test(p))?.replace(/^REASON:\s*/i, "") ?? null;
  const act = parts.find((p) => /^ACT:/i.test(p))?.replace(/^ACT:\s*/i, "") ?? null;
  const hasStructured = perceive || reason || act;

  return (
    <div className="relative pl-8 pb-6 last:pb-0">
      {/* Timeline connector */}
      <div className="absolute left-3 top-0 bottom-0 w-px bg-border last:hidden" />
      <div className="absolute left-[5px] top-1 flex h-6 w-6 items-center justify-center rounded-full bg-background border border-border">
        {statusIcon}
      </div>

      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left"
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{action.action_type}</span>
          {action.confidence != null && <ConfidenceRing value={action.confidence} size={28} />}
          <span className="text-xs text-muted-foreground ml-auto">{fmtRelative(action.created_at)}</span>
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
          {hasStructured ? (reason ?? perceive ?? "") : reasoning.slice(0, 120)}
        </p>
      </button>

      {expanded && (
        <div className="mt-3 space-y-2">
          {hasStructured ? (
            <>
              {perceive && (
                <div className="rounded-md bg-blue-500/5 border border-blue-500/20 px-3 py-2">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Eye className="h-3 w-3 text-blue-500" />
                    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-blue-500">Perceive</span>
                  </div>
                  <p className="text-xs text-foreground/80">{perceive}</p>
                </div>
              )}
              {reason && (
                <div className="rounded-md bg-violet-500/5 border border-violet-500/20 px-3 py-2">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Brain className="h-3 w-3 text-violet-500" />
                    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-violet-500">Reason</span>
                  </div>
                  <p className="text-xs text-foreground/80">{reason}</p>
                </div>
              )}
              {act && (
                <div className="rounded-md bg-emerald-500/5 border border-emerald-500/20 px-3 py-2">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Zap className="h-3 w-3 text-emerald-500" />
                    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-emerald-500">Act</span>
                  </div>
                  <p className="text-xs text-foreground/80">{act}</p>
                </div>
              )}
            </>
          ) : (
            reasoning && (
              <div className="rounded-md bg-muted/30 px-3 py-2">
                <p className="text-xs text-foreground/80">{reasoning}</p>
              </div>
            )
          )}
          {action.execution_result && (
            <pre className="rounded-md bg-muted p-2 text-[10px] overflow-auto max-h-32 font-mono">
              {JSON.stringify(action.execution_result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Action row (original proposals table)
// ---------------------------------------------------------------------------

function ActionRow({
  action,
  onApprove,
  onReject,
}: {
  action: SupervisorAction;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isPending = !action.approved_by && !action.rejected_by && !action.executed;

  const statusBadge = action.approved_by
    ? { label: "Approved", cls: "bg-success/15 text-success" }
    : action.rejected_by
      ? { label: "Rejected", cls: "bg-destructive/15 text-destructive" }
      : action.executed
        ? { label: "Executed", cls: "bg-primary/15 text-primary" }
        : { label: "Pending", cls: "bg-warning/15 text-warning" };

  return (
    <div className="border-b border-border">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="grid w-full grid-cols-[1fr_140px_100px_80px_100px_140px_100px] items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-muted/40"
      >
        <div className="flex items-center gap-2 min-w-0">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate font-medium">{action.action_type}</span>
        </div>
        <span className="font-mono text-xs text-muted-foreground truncate">
          {action.application_id?.slice(0, 8) ?? "—"}
        </span>
        <span
          className={cn(
            "inline-flex items-center justify-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
            statusBadge.cls,
          )}
        >
          {statusBadge.label}
        </span>
        <span className="font-mono text-xs tabular-nums">
          {action.confidence != null ? `${Math.round(action.confidence * 100)}%` : "—"}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {action.mode}
        </span>
        <span className="text-xs text-muted-foreground">
          {fmtRelative(action.created_at)}
        </span>
        <div className="flex items-center gap-1">
          {isPending && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0 text-success hover:bg-success/10"
                onClick={(e) => {
                  e.stopPropagation();
                  onApprove(action.id);
                }}
              >
                <Check className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0 text-destructive hover:bg-destructive/10"
                onClick={(e) => {
                  e.stopPropagation();
                  onReject(action.id);
                }}
              >
                <X className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>
      </button>

      {expanded && (
        <div className="bg-muted/20 px-8 py-4 space-y-3 text-sm">
          {action.reasoning && (
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Reasoning
              </span>
              <p className="mt-1 text-foreground">{action.reasoning}</p>
            </div>
          )}
          {action.execution_result && (
            <div>
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Execution result
              </span>
              <pre className="mt-1 rounded-md bg-muted p-2 text-xs overflow-auto max-h-40">
                {JSON.stringify(action.execution_result, null, 2)}
              </pre>
            </div>
          )}
          {action.approved_by && (
            <p className="text-xs text-muted-foreground">
              Approved by <span className="font-semibold">{action.approved_by}</span>
            </p>
          )}
          {action.rejected_by && (
            <p className="text-xs text-muted-foreground">
              Rejected by <span className="font-semibold">{action.rejected_by}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Event row
// ---------------------------------------------------------------------------

function EventRow({ event }: { event: SupervisorEvent }) {
  const [expanded, setExpanded] = useState(false);
  const statusColor =
    event.status === "processed"
      ? "bg-success/15 text-success"
      : event.status === "failed"
        ? "bg-destructive/15 text-destructive"
        : event.status === "claimed"
          ? "bg-primary/15 text-primary"
          : "bg-warning/15 text-warning";

  return (
    <div className="border-b border-border">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="grid w-full grid-cols-[1fr_140px_100px_160px] items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-muted/40"
      >
        <div className="flex items-center gap-2 min-w-0">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate font-medium">{event.event_type}</span>
        </div>
        <span className="font-mono text-xs text-muted-foreground truncate">
          {event.application_id?.slice(0, 8) ?? "—"}
        </span>
        <span
          className={cn(
            "inline-flex items-center justify-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
            statusColor,
          )}
        >
          {event.status}
        </span>
        <span className="text-xs text-muted-foreground">
          {fmtRelative(event.created_at)}
        </span>
      </button>

      {expanded && (
        <div className="bg-muted/20 px-8 py-4">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            Payload
          </span>
          <pre className="mt-1 rounded-md bg-muted p-2 text-xs overflow-auto max-h-40">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline Health — stall alerts + confidence trends
// ---------------------------------------------------------------------------

function PipelineHealthView({ events }: { events: SupervisorEvent[] }) {
  const stalls = useMemo(
    () => events.filter((e) => e.event_type === "stall_detected" || e.event_type === "candidate_withdrawal"),
    [events],
  );

  const stageCounts = useMemo(() => {
    const map: Record<string, { stalls: number; withdrawals: number }> = {};
    for (const e of stalls) {
      const stage = (e.payload?.stage as string) ?? "unknown";
      if (!map[stage]) map[stage] = { stalls: 0, withdrawals: 0 };
      if (e.event_type === "stall_detected") map[stage].stalls++;
      else map[stage].withdrawals++;
    }
    return Object.entries(map).sort((a, b) => (b[1].stalls + b[1].withdrawals) - (a[1].stalls + a[1].withdrawals));
  }, [stalls]);

  const severityCounts = useMemo(() => {
    const map = { urgent: 0, warning: 0, info: 0 };
    for (const e of stalls) {
      const sev = (e.payload?.severity as keyof typeof map) ?? "info";
      if (sev in map) map[sev]++;
    }
    return map;
  }, [stalls]);

  return (
    <div className="space-y-4">
      {/* Severity KPIs */}
      <div className="grid grid-cols-3 gap-3">
        <KpiCard label="Urgent stalls" value={severityCounts.urgent} icon={AlertTriangle} accent="bg-destructive/15 text-destructive" />
        <KpiCard label="Warnings" value={severityCounts.warning} icon={Clock} accent="bg-warning/15 text-warning" />
        <KpiCard label="Info" value={severityCounts.info} icon={Activity} accent="bg-primary/15 text-primary" />
      </div>

      {/* Stage breakdown */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Stalls by Stage</CardTitle>
        </CardHeader>
        <CardContent>
          {stageCounts.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No stalls detected. Pipeline flowing smoothly.</p>
          ) : (
            <div className="space-y-2">
              {stageCounts.map(([stage, counts]) => {
                const total = counts.stalls + counts.withdrawals;
                const maxBar = Math.max(...stageCounts.map(([, c]) => c.stalls + c.withdrawals));
                return (
                  <div key={stage} className="flex items-center gap-3">
                    <span className="font-mono text-xs w-40 truncate">{stage}</span>
                    <div className="flex-1 h-5 bg-muted/30 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-warning to-destructive rounded-full transition-all"
                        style={{ width: `${(total / maxBar) * 100}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs tabular-nums w-16 text-right">
                      {counts.stalls}s / {counts.withdrawals}w
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent stall events */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Recent Stall Events</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {stalls.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No stall events recorded.</p>
          ) : (
            <div className="divide-y divide-border">
              {stalls.slice(0, 20).map((e) => (
                <div key={e.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  {e.payload?.severity === "urgent" ? (
                    <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
                  ) : (
                    <Clock className="h-4 w-4 text-warning shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{(e.payload?.rule_name as string) ?? e.event_type}</span>
                    <span className="text-muted-foreground"> · {(e.payload?.stage as string) ?? ""}</span>
                  </div>
                  <span className="font-mono text-xs text-muted-foreground truncate max-w-[100px]">
                    {e.application_id?.slice(0, 8) ?? "—"}
                  </span>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">{fmtRelative(e.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Autonomy Controls — mode toggle per stage
// ---------------------------------------------------------------------------

const PIPELINE_STAGES = [
  "intake", "parse", "fit_score", "screening", "voice_screen",
  "assignment", "tech_interview", "ceo_interview", "offer",
] as const;

const AUTONOMY_LEVELS = [
  { value: "human_required", label: "Human Required", color: "bg-destructive/15 text-destructive" },
  { value: "supervisor_shadow", label: "Shadow", color: "bg-warning/15 text-warning" },
  { value: "supervisor_execute", label: "Execute", color: "bg-primary/15 text-primary" },
  { value: "full_auto", label: "Full Auto", color: "bg-success/15 text-success" },
];

const PERMANENT_GATES = new Set(["tech_interview", "ceo_interview"]);

function AutonomyControlsView() {
  const [stageConfig, setStageConfig] = useState<Record<string, string>>(() => {
    const defaults: Record<string, string> = {};
    for (const s of PIPELINE_STAGES) {
      defaults[s] = PERMANENT_GATES.has(s) ? "human_required" : "supervisor_execute";
    }
    return defaults;
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold">Autonomy Level per Stage</CardTitle>
            <div className="flex items-center gap-2">
              {AUTONOMY_LEVELS.map((l) => (
                <span key={l.value} className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase", l.color)}>
                  {l.label}
                </span>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {PIPELINE_STAGES.map((stage) => {
              const isLocked = PERMANENT_GATES.has(stage);
              const current = stageConfig[stage] ?? "supervisor_execute";
              const levelInfo = AUTONOMY_LEVELS.find((l) => l.value === current)!;

              return (
                <div key={stage} className="flex items-center gap-3 py-1.5">
                  <span className="font-mono text-xs w-40">{stage}</span>
                  {isLocked ? (
                    <div className="flex items-center gap-2">
                      <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase", levelInfo.color)}>
                        {levelInfo.label}
                      </span>
                      <Shield className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-[10px] text-muted-foreground">Permanent gate — cannot change</span>
                    </div>
                  ) : (
                    <Select value={current} onValueChange={(v) => setStageConfig((p) => ({ ...p, [stage]: v }))}>
                      <SelectTrigger className="w-44 h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {AUTONOMY_LEVELS.map((l) => (
                          <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Guardrail Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2 mb-2">
                <Shield className="h-4 w-4 text-destructive" />
                <span className="text-xs font-semibold">Hard Deny List</span>
              </div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>override_stage</li>
                <li>auto_hire (finals)</li>
                <li>auto_reject (finals)</li>
              </ul>
            </div>
            <div className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2 mb-2">
                <ShieldCheck className="h-4 w-4 text-success" />
                <span className="text-xs font-semibold">Permanent Human Gates</span>
              </div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>technical_pending_approval</li>
                <li>ceo_pending_approval</li>
                <li>hr_evaluated</li>
              </ul>
            </div>
            <div className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2 mb-2">
                <Gauge className="h-4 w-4 text-warning" />
                <span className="text-xs font-semibold">Rate Limits</span>
              </div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>Max 5 actions per event</li>
                <li>Max 10 actions per candidate/hour</li>
                <li>Auto-demotion after 3 consecutive rejections</li>
              </ul>
            </div>
            <div className="rounded-md border border-border p-3">
              <div className="flex items-center gap-2 mb-2">
                <Target className="h-4 w-4 text-primary" />
                <span className="text-xs font-semibold">Confidence Thresholds</span>
              </div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>advance_stage: 80%</li>
                <li>send_email: 70%</li>
                <li>evaluate_assignment: 60%</li>
                <li>escalate_to_hr: 30%</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Engagement & Re-engagement Campaign Tracker
// ---------------------------------------------------------------------------

function EngagementTrackerView({ events }: { events: SupervisorEvent[] }) {
  const campaigns = useMemo(() => {
    const reeng = events.filter(
      (e) =>
        e.event_type === "stall_detected" &&
        (e.payload?.trigger === "reengagement"),
    );
    const withdrawals = events.filter(
      (e) =>
        e.event_type === "candidate_withdrawal" &&
        (e.payload?.trigger === "reengagement_timeout"),
    );
    return { nudges: reeng, withdrawals };
  }, [events]);

  const nudgesByType = useMemo(() => {
    const map: Record<string, number> = { gentle: 0, urgent: 0, final: 0 };
    for (const e of campaigns.nudges) {
      const t = (e.payload?.nudge_type as string) ?? "gentle";
      map[t] = (map[t] ?? 0) + 1;
    }
    return map;
  }, [campaigns.nudges]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard label="Gentle nudges" value={nudgesByType.gentle ?? 0} icon={Mail} accent="bg-blue-500/15 text-blue-500" />
        <KpiCard label="Urgent nudges" value={nudgesByType.urgent ?? 0} icon={MessageSquare} accent="bg-warning/15 text-warning" />
        <KpiCard label="Final notices" value={nudgesByType.final ?? 0} icon={AlertTriangle} accent="bg-orange-500/15 text-orange-500" />
        <KpiCard label="Auto-withdrawals" value={campaigns.withdrawals.length} icon={X} accent="bg-destructive/15 text-destructive" />
      </div>

      {/* Campaign sequence visual */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Re-engagement Sequence</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 overflow-x-auto pb-2">
            {[
              { day: 0, label: "Gentle", icon: Heart, color: "bg-blue-500/15 border-blue-500/30 text-blue-500" },
              { day: 3, label: "Urgent", icon: HeartPulse, color: "bg-warning/15 border-warning/30 text-warning" },
              { day: 7, label: "Final", icon: AlertTriangle, color: "bg-orange-500/15 border-orange-500/30 text-orange-500" },
              { day: 9, label: "Withdraw", icon: X, color: "bg-destructive/15 border-destructive/30 text-destructive" },
            ].map((step, i) => (
              <div key={step.day} className="flex items-center gap-2">
                {i > 0 && <div className="w-8 h-px bg-border shrink-0" />}
                <div className={cn("flex flex-col items-center gap-1 rounded-lg border p-3 min-w-[80px]", step.color)}>
                  <step.icon className="h-4 w-4" />
                  <span className="text-[10px] font-semibold uppercase">{step.label}</span>
                  <span className="text-[9px] opacity-70">Day {step.day}</span>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Recent campaign events */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Recent Campaign Activity</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {campaigns.nudges.length === 0 && campaigns.withdrawals.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No re-engagement campaigns active.</p>
          ) : (
            <div className="divide-y divide-border">
              {[...campaigns.nudges, ...campaigns.withdrawals]
                .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                .slice(0, 20)
                .map((e) => {
                  const isWithdrawal = e.event_type === "candidate_withdrawal";
                  return (
                    <div key={e.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                      {isWithdrawal ? (
                        <X className="h-4 w-4 text-destructive shrink-0" />
                      ) : (
                        <Mail className="h-4 w-4 text-blue-500 shrink-0" />
                      )}
                      <div className="min-w-0 flex-1">
                        <span className="font-medium">
                          {isWithdrawal ? "Auto-withdrawal" : `${(e.payload?.nudge_type as string) ?? "gentle"} nudge`}
                        </span>
                        <span className="text-muted-foreground"> · score {(e.payload?.engagement_score as number)?.toFixed(2) ?? "—"}</span>
                      </div>
                      <span className="font-mono text-xs text-muted-foreground truncate max-w-[100px]">
                        {e.application_id?.slice(0, 8) ?? "—"}
                      </span>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">{fmtRelative(e.created_at)}</span>
                    </div>
                  );
                })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Performance Metrics — accuracy, calibration, decisions
// ---------------------------------------------------------------------------

function PerformanceMetricsView({
  actions,
  accuracy,
}: {
  actions: SupervisorAction[];
  accuracy: AccuracyReport | null;
}) {
  const actionTypeBreakdown = useMemo(() => {
    const map: Record<string, { total: number; executed: number; approved: number; rejected: number }> = {};
    for (const a of actions) {
      if (!map[a.action_type]) map[a.action_type] = { total: 0, executed: 0, approved: 0, rejected: 0 };
      map[a.action_type].total++;
      if (a.executed) map[a.action_type].executed++;
      if (a.approved_by) map[a.action_type].approved++;
      if (a.rejected_by) map[a.action_type].rejected++;
    }
    return Object.entries(map).sort((a, b) => b[1].total - a[1].total);
  }, [actions]);

  const confidenceCalibration = useMemo(() => {
    const buckets: Record<string, { count: number; approved: number }> = {
      "90-100": { count: 0, approved: 0 },
      "80-89": { count: 0, approved: 0 },
      "70-79": { count: 0, approved: 0 },
      "60-69": { count: 0, approved: 0 },
      "<60": { count: 0, approved: 0 },
    };
    for (const a of actions) {
      if (a.confidence == null) continue;
      const pct = Math.round(a.confidence * 100);
      const bucket = pct >= 90 ? "90-100" : pct >= 80 ? "80-89" : pct >= 70 ? "70-79" : pct >= 60 ? "60-69" : "<60";
      buckets[bucket].count++;
      if (a.approved_by || a.executed) buckets[bucket].approved++;
    }
    return buckets;
  }, [actions]);

  const modeBreakdown = useMemo(() => {
    const map: Record<string, number> = {};
    for (const a of actions) {
      map[a.mode] = (map[a.mode] ?? 0) + 1;
    }
    return map;
  }, [actions]);

  const channelBreakdown = useMemo(() => {
    const map: Record<string, number> = {};
    for (const a of actions) {
      if (a.action_type.startsWith("send_")) {
        const ch = a.action_type.replace("send_", "");
        map[ch] = (map[ch] ?? 0) + 1;
      }
    }
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
  }, [actions]);

  return (
    <div className="space-y-4">
      {/* Top-level accuracy */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard
          label="Approval rate"
          value={accuracy?.approval_rate != null ? `${Math.round(accuracy.approval_rate * 100)}%` : "—"}
          icon={Target}
          accent="bg-success/15 text-success"
        />
        <KpiCard label="Total decisions" value={accuracy?.total_proposals ?? 0} icon={Brain} />
        <KpiCard
          label="Shadow mode"
          value={modeBreakdown["shadow"] ?? 0}
          icon={Eye}
          accent="bg-warning/15 text-warning"
        />
        <KpiCard
          label="Execute mode"
          value={modeBreakdown["execute"] ?? 0}
          icon={Zap}
          accent="bg-primary/15 text-primary"
        />
      </div>

      {/* Confidence calibration */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Confidence Calibration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            How well confidence scores predict approval. Well-calibrated = bar height matches bucket label.
          </p>
          <div className="flex items-end gap-2 h-32">
            {Object.entries(confidenceCalibration).map(([bucket, data]) => {
              const rate = data.count > 0 ? data.approved / data.count : 0;
              const height = Math.max(4, rate * 100);
              return (
                <div key={bucket} className="flex-1 flex flex-col items-center gap-1">
                  <span className="font-mono text-[9px] tabular-nums">
                    {data.count > 0 ? `${Math.round(rate * 100)}%` : "—"}
                  </span>
                  <div className="w-full bg-muted/30 rounded-t relative" style={{ height: "100px" }}>
                    <div
                      className="absolute bottom-0 w-full bg-gradient-to-t from-primary to-primary/60 rounded-t transition-all"
                      style={{ height: `${height}%` }}
                    />
                  </div>
                  <span className="font-mono text-[9px] text-muted-foreground">{bucket}</span>
                  <span className="font-mono text-[8px] text-muted-foreground/60">n={data.count}</span>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Action type breakdown */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Decisions by Type</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border">
            {actionTypeBreakdown.slice(0, 15).map(([type, counts]) => {
              const approvalRate = counts.total > 0 ? ((counts.approved + counts.executed) / counts.total) : 0;
              return (
                <div key={type} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="font-mono text-xs w-44 truncate">{type}</span>
                  <div className="flex-1 h-4 bg-muted/30 rounded-full overflow-hidden flex">
                    <div className="h-full bg-success/60" style={{ width: `${(counts.approved / counts.total) * 100}%` }} />
                    <div className="h-full bg-primary/60" style={{ width: `${(counts.executed / counts.total) * 100}%` }} />
                    <div className="h-full bg-destructive/60" style={{ width: `${(counts.rejected / counts.total) * 100}%` }} />
                  </div>
                  <span className="font-mono text-[10px] tabular-nums w-12 text-right">{counts.total}</span>
                  <span className={cn(
                    "font-mono text-[10px] tabular-nums w-12 text-right",
                    approvalRate >= 0.8 ? "text-success" : approvalRate >= 0.5 ? "text-warning" : "text-destructive",
                  )}>
                    {Math.round(approvalRate * 100)}%
                  </span>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Channel decisions */}
      {channelBreakdown.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">Channel Usage</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-4">
              {channelBreakdown.map(([channel, count]) => (
                <div key={channel} className="flex items-center gap-2">
                  {channel === "email" ? <Mail className="h-4 w-4 text-blue-500" /> :
                   channel === "whatsapp" ? <MessageSquare className="h-4 w-4 text-green-500" /> :
                   <Phone className="h-4 w-4 text-violet-500" />}
                  <span className="text-xs font-medium capitalize">{channel}</span>
                  <span className="font-mono text-xs text-muted-foreground">{count}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Experiments panel
// ---------------------------------------------------------------------------

function ExperimentsView() {
  const { data: experiments } = useSWR<ExperimentSummary[]>(
    "/supervisor/experiments",
    () => safeFetch(() => experimentApi.list(), []),
    { refreshInterval: 30_000 },
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">A/B Experiments</CardTitle>
        </CardHeader>
        <CardContent>
          {!experiments || experiments.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">No active experiments.</p>
          ) : (
            <div className="divide-y divide-border">
              {experiments.map((exp) => (
                <div key={exp.name} className="flex items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{exp.name}</span>
                      <span className={cn(
                        "inline-flex rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase",
                        exp.status === "active" ? "bg-success/15 text-success" : "bg-muted text-muted-foreground",
                      )}>
                        {exp.status}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">{exp.description}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-mono text-xs tabular-nums">{exp.variant_count} variants</p>
                    <p className="font-mono text-[10px] text-muted-foreground">{Math.round(exp.sample_rate * 100)}% traffic</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type ViewTab = "trail" | "proposals" | "events" | "health" | "autonomy" | "engagement" | "performance" | "experiments";

export default function SupervisorPage() {
  const [view, setView] = useState<ViewTab>("trail");
  const [statusFilter, setStatusFilter] = useState("pending");

  const {
    data: actionsData,
    isLoading: actionsLoading,
    mutate: mutateActions,
  } = useSWR<ActionListResponse>(
    `/supervisor/actions-all`,
    () => safeFetch(() => supervisorApi.actions({ limit: 200 }), EMPTY_ACTIONS),
    { refreshInterval: 15_000 },
  );

  const {
    data: filteredActionsData,
    isLoading: filteredLoading,
    mutate: mutateFiltered,
  } = useSWR<ActionListResponse>(
    view === "proposals" ? `/supervisor/actions?status=${statusFilter}` : null,
    () => safeFetch(() => supervisorApi.actions({ status: statusFilter, limit: 100 }), EMPTY_ACTIONS),
    { refreshInterval: 15_000 },
  );

  const {
    data: eventsData,
    isLoading: eventsLoading,
  } = useSWR<EventListResponse>(
    "/supervisor/events",
    () => safeFetch(() => supervisorApi.events({ limit: 200 }), EMPTY_EVENTS),
    { refreshInterval: 15_000 },
  );

  const { data: accuracy } = useSWR<AccuracyReport>(
    "/supervisor/accuracy-report",
    () => safeFetch(() => supervisorApi.accuracyReport(), EMPTY_ACCURACY),
    { refreshInterval: 30_000 },
  );

  async function handleApprove(id: string) {
    try {
      await supervisorApi.approve(id);
      mutateActions();
      mutateFiltered();
    } catch (err) {
      console.error("approve failed", err);
    }
  }

  async function handleReject(id: string) {
    try {
      await supervisorApi.reject(id);
      mutateActions();
      mutateFiltered();
    } catch (err) {
      console.error("reject failed", err);
    }
  }

  const allActions = actionsData?.actions ?? [];
  const filteredActions = filteredActionsData?.actions ?? [];
  const events = eventsData?.events ?? [];

  return (
    <>
      <Topbar title="Supervisor Intelligence" subtitle="Autonomous agent control center" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        {/* KPIs */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <KpiCard
            label="Total proposals"
            value={accuracy?.total_proposals ?? 0}
            icon={Bot}
          />
          <KpiCard
            label="Pending"
            value={accuracy?.pending ?? 0}
            icon={Eye}
            accent="bg-warning/15 text-warning"
          />
          <KpiCard
            label="Approved"
            value={accuracy?.approved ?? 0}
            icon={ShieldCheck}
            accent="bg-success/15 text-success"
          />
          <KpiCard
            label="Rejected"
            value={accuracy?.rejected ?? 0}
            icon={ShieldAlert}
            accent="bg-destructive/15 text-destructive"
          />
          <KpiCard
            label="Executed"
            value={accuracy?.executed ?? 0}
            icon={Zap}
            accent="bg-primary/15 text-primary"
          />
          <KpiCard
            label="Approval rate"
            value={
              accuracy?.approval_rate != null
                ? `${Math.round(accuracy.approval_rate * 100)}%`
                : "—"
            }
            icon={BarChart3}
          />
        </div>

        {/* Tab bar */}
        <div className="flex flex-wrap items-center gap-2">
          <Tabs
            value={view}
            onValueChange={(v) => setView(v as ViewTab)}
          >
            <TabsList className="h-9">
              <TabsTrigger value="trail" className="gap-1">
                <Brain className="h-3.5 w-3.5" />
                Decision Trail
              </TabsTrigger>
              <TabsTrigger value="proposals" className="gap-1">
                <Shield className="h-3.5 w-3.5" />
                Proposals
              </TabsTrigger>
              <TabsTrigger value="events" className="gap-1">
                <Activity className="h-3.5 w-3.5" />
                Events
              </TabsTrigger>
              <TabsTrigger value="health" className="gap-1">
                <HeartPulse className="h-3.5 w-3.5" />
                Health
              </TabsTrigger>
              <TabsTrigger value="autonomy" className="gap-1">
                <Sliders className="h-3.5 w-3.5" />
                Autonomy
              </TabsTrigger>
              <TabsTrigger value="engagement" className="gap-1">
                <Users className="h-3.5 w-3.5" />
                Engagement
              </TabsTrigger>
              <TabsTrigger value="performance" className="gap-1">
                <TrendingUp className="h-3.5 w-3.5" />
                Performance
              </TabsTrigger>
              <TabsTrigger value="experiments" className="gap-1">
                <Target className="h-3.5 w-3.5" />
                Experiments
              </TabsTrigger>
            </TabsList>
          </Tabs>

          {view === "proposals" && (
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-36">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="approved">Approved</SelectItem>
                <SelectItem value="rejected">Rejected</SelectItem>
                <SelectItem value="executed">Executed</SelectItem>
              </SelectContent>
            </Select>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={() => mutateActions()}
          >
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
        </div>

        {/* ============ Decision Trail ============ */}
        {view === "trail" && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Brain className="h-4 w-4 text-violet-500" />
                Agent Decision Trail
                <span className="text-[10px] text-muted-foreground font-normal ml-1">perceive → reason → act</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {actionsLoading ? (
                <div className="space-y-3">
                  {[0, 1, 2, 3].map((i) => (
                    <div key={i} className="h-16 rounded-lg skeleton" />
                  ))}
                </div>
              ) : allActions.length === 0 ? (
                <div className="flex flex-col items-center gap-3 py-12 text-center">
                  <Bot className="h-8 w-8 text-primary animate-pulse" />
                  <p className="text-base font-bold">Agent idle</p>
                  <p className="text-sm text-muted-foreground">
                    Decision trail populates as supervisor processes pipeline events.
                  </p>
                </div>
              ) : (
                <div className="relative">
                  {allActions.slice(0, 30).map((a) => (
                    <DecisionTrailItem key={a.id} action={a} />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* ============ Proposals (original) ============ */}
        {view === "proposals" && (
          <>
            {filteredLoading ? (
              <div className="space-y-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="h-14 rounded-lg skeleton" />
                ))}
              </div>
            ) : filteredActions.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
                  <ShieldCheck className="h-8 w-8 text-primary" />
                  <p className="text-base font-bold">No {statusFilter} proposals</p>
                  <p className="text-sm text-muted-foreground">
                    Supervisor proposals appear here when the agent reasons about pipeline events.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <Card className="overflow-hidden">
                <div className="grid grid-cols-[1fr_140px_100px_80px_100px_140px_100px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  <span>Action</span>
                  <span>Application</span>
                  <span>Status</span>
                  <span>Conf.</span>
                  <span>Mode</span>
                  <span>When</span>
                  <span>Review</span>
                </div>
                <div>
                  {filteredActions.map((a) => (
                    <ActionRow
                      key={a.id}
                      action={a}
                      onApprove={handleApprove}
                      onReject={handleReject}
                    />
                  ))}
                </div>
              </Card>
            )}
          </>
        )}

        {/* ============ Events ============ */}
        {view === "events" && (
          <>
            {eventsLoading ? (
              <div className="space-y-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="h-14 rounded-lg skeleton" />
                ))}
              </div>
            ) : events.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
                  <Activity className="h-8 w-8 text-primary" />
                  <p className="text-base font-bold">No events yet</p>
                  <p className="text-sm text-muted-foreground">
                    Pipeline events appear here as the supervisor processes them.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <Card className="overflow-hidden">
                <div className="grid grid-cols-[1fr_140px_100px_160px] items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  <span>Event type</span>
                  <span>Application</span>
                  <span>Status</span>
                  <span>When</span>
                </div>
                <div>
                  {events.map((e) => (
                    <EventRow key={e.id} event={e} />
                  ))}
                </div>
              </Card>
            )}
          </>
        )}

        {/* ============ Pipeline Health ============ */}
        {view === "health" && <PipelineHealthView events={events} />}

        {/* ============ Autonomy Controls ============ */}
        {view === "autonomy" && <AutonomyControlsView />}

        {/* ============ Engagement Tracker ============ */}
        {view === "engagement" && <EngagementTrackerView events={events} />}

        {/* ============ Performance Metrics ============ */}
        {view === "performance" && <PerformanceMetricsView actions={allActions} accuracy={accuracy ?? null} />}

        {/* ============ Experiments ============ */}
        {view === "experiments" && <ExperimentsView />}
      </div>
    </>
  );
}
