"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import type { ArtifactData } from "@/lib/useRecruiterChat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */
interface Dim {
  key?: string;
  label?: string;
  weight?: number;
  what_good_looks_like?: string[];
  anti_signals?: string[];
}
interface Knockout {
  key?: string;
  rule?: string;
}
interface Stage {
  stage_key?: string;
  stage_type?: string;
  label?: string;
  position?: number;
  mode?: string;
  is_enabled?: boolean;
}
interface CompanyContext {
  summary?: string;
  what_matters_here?: string[];
  hiring_bar?: string;
}
interface Draft {
  title?: string;
  jd_text?: string;
  ctc_min_lpa?: number | null;
  ctc_max_lpa?: number | null;
  location?: string | null;
  remote_policy?: string | null;
  max_notice_days?: number | null;
  pipeline?: Stage[];
  evaluation_spec?: {
    dimensions?: Dim[];
    knockouts?: Knockout[];
  };
  company_context?: CompanyContext;
  assignment?: {
    enabled?: boolean;
    brief?: string;
    instructions?: string;
    n_problems?: number;
    time_budget_hours?: number;
    deadline_days?: number;
  };
  notes?: string | null;
}

const STAGE_TYPES = [
  "voice_screen",
  "assignment",
  "interview",
  "decision",
  "offer",
] as const;

const STAGE_TYPE_LABELS: Record<string, string> = {
  intake: "Intake",
  parse: "Parse",
  fit: "Fit Score",
  screening: "Screening",
  voice_screen: "Voice Screen",
  assignment: "Assignment",
  interview: "Interview",
  decision: "Decision",
  offer: "Offer",
};

const linesToArr = (s: string): string[] => s.split("\n");
const arrToLines = (a?: string[]): string => (a || []).join("\n");

/** Sentinel for the "Select..." placeholder option of remote_policy, since
 *  Radix SelectItem cannot use an empty-string value. */
const REMOTE_POLICY_NONE = "__none__";

/** Docked (collapsed) width of the panel, in px. The chat surface reserves this
 *  much right-padding so the docked panel never covers the conversation. */
export const ARTIFACT_PANEL_WIDTH = 460;

/* ------------------------------------------------------------------ */
/* Collapsible section — divider + heading, no box wrapping            */
/* ------------------------------------------------------------------ */
function Section({
  title,
  badge,
  defaultOpen = true,
  children,
}: {
  title: string;
  badge?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-border/40 first:border-t-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="group flex w-full items-center justify-between py-3 text-left"
      >
        <span className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground group-hover:text-foreground transition-colors">
          {title}
          {badge}
        </span>
        {open ? (
          <ChevronUp className="h-3 w-3 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
        )}
      </button>
      {open && <div className="pb-5">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Field row — muted label above an input, used for uniform alignment  */
/* ------------------------------------------------------------------ */
function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="block font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Main panel                                                          */
/* ------------------------------------------------------------------ */
export function ArtifactPanel({
  artifact,
  expanded,
  onToggleExpand,
  onClose,
  onSave,
  onApply,
}: {
  artifact: ArtifactData;
  expanded: boolean;
  onToggleExpand: () => void;
  onClose: () => void;
  onSave: (content: Record<string, unknown>) => Promise<void>;
  onApply: () => Promise<{ ok: boolean; role_url?: string; status?: string } | null>;
}) {
  const [draft, setDraft] = useState<Draft>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyStatus, setApplyStatus] = useState<string | null>(null);

  // Enter/exit slide animation. `shown` flips to true one frame after mount so
  // the panel slides in from the right; closing reverses it before unmount.
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const r = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(r);
  }, []);
  const handleClose = useCallback(() => {
    setShown(false);
    window.setTimeout(onClose, 260);
  }, [onClose]);

  useEffect(() => {
    setDraft((artifact.content as Draft) || {});
    setDirty(false);
  }, [artifact.id, artifact.version]);

  const applied = artifact.status === "applied";
  const patch = useCallback(
    (p: Partial<Draft>) => {
      setDraft((d) => ({ ...d, ...p }));
      setDirty(true);
    },
    [],
  );

  const stages = draft.pipeline || [];
  const dims = draft.evaluation_spec?.dimensions || [];
  const knockouts = draft.evaluation_spec?.knockouts || [];
  const ctx = draft.company_context || {};

  /* -- Stage helpers -- */
  const setStage = (i: number, p: Partial<Stage>) => {
    const next = [...stages];
    next[i] = { ...next[i], ...p };
    patch({ pipeline: next });
  };
  const removeStage = (i: number) => patch({ pipeline: stages.filter((_, j) => j !== i) });
  const addStage = () =>
    patch({
      pipeline: [
        ...stages,
        {
          stage_key: `interview_${stages.length + 1}`,
          stage_type: "interview",
          label: "Interview round",
          mode: "manual",
          is_enabled: true,
        },
      ],
    });

  /* -- Dimension helpers -- */
  const setDim = (i: number, p: Partial<Dim>) => {
    const next = [...dims];
    next[i] = { ...next[i], ...p };
    patch({ evaluation_spec: { ...draft.evaluation_spec, dimensions: next } });
  };
  const removeDim = (i: number) =>
    patch({
      evaluation_spec: {
        ...draft.evaluation_spec,
        dimensions: dims.filter((_, j) => j !== i),
      },
    });
  const addDim = () =>
    patch({
      evaluation_spec: {
        ...draft.evaluation_spec,
        dimensions: [
          ...dims,
          {
            key: `dim_${dims.length + 1}`,
            label: "New dimension",
            weight: 0,
            what_good_looks_like: [],
            anti_signals: [],
          },
        ],
      },
    });

  /* -- Knockout helpers -- */
  const setKnockout = (i: number, p: Partial<Knockout>) => {
    const next = [...knockouts];
    next[i] = { ...next[i], ...p };
    patch({ evaluation_spec: { ...draft.evaluation_spec, knockouts: next } });
  };
  const removeKnockout = (i: number) =>
    patch({
      evaluation_spec: {
        ...draft.evaluation_spec,
        knockouts: knockouts.filter((_, j) => j !== i),
      },
    });
  const addKnockout = () =>
    patch({
      evaluation_spec: {
        ...draft.evaluation_spec,
        knockouts: [
          ...knockouts,
          { key: `ko_${knockouts.length + 1}`, rule: "" },
        ],
      },
    });

  /* -- Company context helpers -- */
  const patchCtx = (p: Partial<CompanyContext>) => {
    patch({ company_context: { ...ctx, ...p } });
  };

  /* -- Save / Apply -- */
  const handleSave = async () => {
    setSaving(true);
    const content: Draft = {
      ...draft,
      pipeline: stages.map((s, i) => ({ ...s, position: i })),
    };
    await onSave(content as Record<string, unknown>);
    setSaving(false);
    setDirty(false);
  };

  const handleApply = async () => {
    setApplying(true);
    if (dirty) await handleSave();
    const res = await onApply();
    setApplying(false);
    if (res?.ok) {
      if (res.status) setApplyStatus(res.status);
    }
  };

  const weightTotal = dims.reduce((s, d) => s + (Number(d.weight) || 0), 0);

  // Only show the take-home assignment section when the pipeline actually
  // includes an enabled assignment stage. If the draft has no pipeline at all,
  // fall back to showing the section (preserves prior behavior).
  const hasAssignmentStage =
    !draft.pipeline ||
    stages.some(
      (s) =>
        (s.stage_type === "assignment" || s.stage_key === "assignment") &&
        s.is_enabled !== false,
    );

  return (
    <aside
      className={cn(
        "absolute right-0 top-0 bottom-0 z-30 flex flex-col border-l border-border/60 bg-card shadow-2xl",
        "transition-[transform,width,opacity] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] will-change-transform",
        shown ? "translate-x-0 opacity-100" : "translate-x-full opacity-0",
      )}
      style={{ width: expanded ? "100%" : ARTIFACT_PANEL_WIDTH }}
    >
      {/* Header / action bar */}
      <header className="flex shrink-0 items-center justify-between border-b border-border/50 bg-muted/30 px-5 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <FileText className="h-3.5 w-3.5 text-primary" />
          </div>
          <div>
            <p className="text-sm font-semibold leading-tight">Role Draft</p>
            <p className="font-mono text-[10px] text-muted-foreground">
              v{artifact.version} · {artifact.status}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleExpand}
            title={expanded ? "Collapse to sidebar" : "Expand to full width"}
            aria-label={expanded ? "Collapse panel" : "Expand panel"}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition duration-150 hover:bg-muted hover:text-foreground hover:scale-105 active:scale-95"
          >
            {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close panel"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition duration-150 hover:bg-muted hover:text-foreground hover:scale-105 active:scale-95"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Scrollable body */}
      <div className={cn("flex-1 overflow-y-auto py-2", expanded ? "px-8" : "px-5")}>
        {/* In expanded mode, constrain content to a readable column so wide
            textareas don't stretch edge-to-edge. */}
        <div className={cn(expanded && "mx-auto max-w-3xl")}>

        {/* ====== BASICS ====== */}
        <Section title="Basics" defaultOpen={true}>
          <div className="space-y-4">
            {/* Title — full width, prominent */}
            <div>
              <FieldLabel>Title</FieldLabel>
              <Input
                value={draft.title || ""}
                onChange={(e) => patch({ title: e.target.value })}
                placeholder="e.g. Senior Backend Engineer"
                className="text-sm font-medium"
              />
            </div>

            <div className="grid grid-cols-2 gap-x-4 gap-y-4">
              <div>
                <FieldLabel>CTC min (LPA)</FieldLabel>
                <Input
                  type="number"
                  value={draft.ctc_min_lpa ?? ""}
                  onChange={(e) =>
                    patch({
                      ctc_min_lpa:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </div>
              <div>
                <FieldLabel>CTC max (LPA)</FieldLabel>
                <Input
                  type="number"
                  value={draft.ctc_max_lpa ?? ""}
                  onChange={(e) =>
                    patch({
                      ctc_max_lpa:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </div>
              <div>
                <FieldLabel>Location</FieldLabel>
                <Input
                  value={draft.location || ""}
                  onChange={(e) => patch({ location: e.target.value })}
                  placeholder="Hyderabad"
                />
              </div>
              <div>
                <FieldLabel>Remote policy</FieldLabel>
                <Select
                  value={draft.remote_policy || REMOTE_POLICY_NONE}
                  onValueChange={(nv) =>
                    patch({
                      remote_policy: nv === REMOTE_POLICY_NONE ? "" : nv,
                    })
                  }
                >
                  <SelectTrigger className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={REMOTE_POLICY_NONE}>Select...</SelectItem>
                    <SelectItem value="onsite">Onsite</SelectItem>
                    <SelectItem value="hybrid">Hybrid</SelectItem>
                    <SelectItem value="remote">Remote</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <FieldLabel>Max notice period (days)</FieldLabel>
              <Input
                type="number"
                value={draft.max_notice_days ?? ""}
                onChange={(e) =>
                  patch({
                    max_notice_days:
                      e.target.value === "" ? null : Number(e.target.value),
                  })
                }
                placeholder="60"
                className="w-32"
              />
            </div>
          </div>
        </Section>

        {/* ====== JOB DESCRIPTION ====== */}
        <Section title="Job Description" defaultOpen={true}>
          <AutoTextarea
            value={draft.jd_text || ""}
            onChange={(val) => patch({ jd_text: val })}
            className="min-h-[180px] font-mono text-xs leading-relaxed"
            placeholder="Paste or edit the full job description here..."
          />
        </Section>

        {/* ====== PIPELINE ====== */}
        <Section
          title="Pipeline"
          badge={
            <Badge variant="outline" className="text-[10px] font-mono">
              {stages.filter((s) => s.is_enabled !== false).length} active
            </Badge>
          }
        >
          {/* Soft note — no box */}
          <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground">
            Always runs first: <span className="text-foreground/70">Intake → Resume Parse → Fit Score</span>
          </p>
          <div className="space-y-1">
            {stages.map((s, i) => (
              <div
                key={i}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
                  s.is_enabled !== false
                    ? "hover:bg-muted/40"
                    : "opacity-50",
                )}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 shrink-0 rounded accent-primary"
                  checked={s.is_enabled !== false}
                  onChange={(e) => setStage(i, { is_enabled: e.target.checked })}
                />
                <Input
                  className="h-7 flex-1 border-0 bg-transparent px-1 text-sm shadow-none focus-visible:ring-0"
                  value={s.label || ""}
                  onChange={(e) => setStage(i, { label: e.target.value })}
                />
                <Select
                  value={s.stage_type || "interview"}
                  onValueChange={(nv) =>
                    setStage(
                      i,
                      nv === "interview"
                        ? { stage_type: nv }
                        : { stage_type: nv, stage_key: nv, label: STAGE_TYPE_LABELS[nv] || nv },
                    )
                  }
                >
                  <SelectTrigger className="h-7 rounded border border-input bg-background px-1.5 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {STAGE_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {STAGE_TYPE_LABELS[t] || t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={s.mode || "manual"}
                  onValueChange={(nv) => setStage(i, { mode: nv })}
                >
                  <SelectTrigger className="h-7 rounded border border-input bg-background px-1.5 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Auto</SelectItem>
                    <SelectItem value="manual">Manual</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 shrink-0 text-muted-foreground/50 hover:text-destructive"
                  onClick={() => removeStage(i)}
                  aria-label="Remove stage"
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onClick={addStage}
              className="mt-1 h-auto gap-1.5 px-2 text-xs text-primary hover:text-primary/80"
            >
              <Plus className="h-3 w-3" /> Add stage
            </Button>
          </div>
        </Section>

        {/* ====== EVALUATION SPEC ====== */}
        <Section
          title="Evaluation Criteria"
          badge={
            <span
              className={cn(
                "font-mono text-[10px]",
                weightTotal >= 95 && weightTotal <= 105
                  ? "text-muted-foreground"
                  : "text-amber-500",
              )}
            >
              {weightTotal}/100
            </span>
          }
        >
          <div className="space-y-4">
            {dims.map((d, i) => (
              /* Each dimension: left accent bar, no border box */
              <div
                key={i}
                className="border-l-2 border-primary/30 pl-3 space-y-2.5"
              >
                <div className="flex items-center gap-2">
                  <Input
                    className="h-7 flex-1 text-sm font-medium border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
                    value={d.label || ""}
                    onChange={(e) => setDim(i, { label: e.target.value })}
                    placeholder="Dimension label"
                  />
                  <div className="flex items-center gap-1 shrink-0">
                    <Input
                      type="number"
                      min={0}
                      max={100}
                      className="h-7 w-20 text-center text-xs font-mono"
                      value={d.weight ?? 0}
                      onChange={(e) =>
                        setDim(i, { weight: Number(e.target.value) })
                      }
                    />
                    <span className="text-[10px] text-muted-foreground">%</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 shrink-0 text-muted-foreground/40 hover:text-destructive"
                    onClick={() => removeDim(i)}
                    aria-label="Remove dimension"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
                <div>
                  <FieldLabel>What good looks like</FieldLabel>
                  <AutoTextarea
                    value={arrToLines(d.what_good_looks_like)}
                    onChange={(val) =>
                      setDim(i, { what_good_looks_like: linesToArr(val) })
                    }
                    className="min-h-[48px] text-xs leading-relaxed"
                    placeholder="Concrete signals of a strong candidate, one per line..."
                  />
                </div>
                <div>
                  <FieldLabel>Anti-signals</FieldLabel>
                  <AutoTextarea
                    value={arrToLines(d.anti_signals)}
                    onChange={(val) =>
                      setDim(i, { anti_signals: linesToArr(val) })
                    }
                    className="min-h-[36px] text-xs leading-relaxed"
                    placeholder="Red flags or weak signals, one per line..."
                  />
                </div>
              </div>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onClick={addDim}
              className="h-auto gap-1.5 px-2 text-xs text-primary hover:text-primary/80"
            >
              <Plus className="h-3 w-3" /> Add dimension
            </Button>
          </div>

          {/* Knockouts — sub-section within Evaluation, separated by thin rule */}
          <div className="mt-5 pt-4 border-t border-border/30 space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground mb-2">
              Knockouts <span className="normal-case font-normal tracking-normal">(hard disqualifiers)</span>
            </p>
            {knockouts.map((k, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-muted-foreground/50 text-xs select-none">·</span>
                <Input
                  className="h-7 flex-1 text-xs"
                  value={k.rule || ""}
                  onChange={(e) => setKnockout(i, { rule: e.target.value })}
                  placeholder="e.g. No experience with databases at all"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 shrink-0 text-muted-foreground/40 hover:text-destructive"
                  onClick={() => removeKnockout(i)}
                  aria-label="Remove knockout"
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onClick={addKnockout}
              className="h-auto gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <Plus className="h-3 w-3" /> Add knockout
            </Button>
          </div>
        </Section>

        {/* ====== COMPANY CONTEXT ====== */}
        <Section title="Company Context" defaultOpen={false}>
          <div className="space-y-4">
            <div>
              <FieldLabel>Summary</FieldLabel>
              <AutoTextarea
                value={ctx.summary || ""}
                onChange={(val) => patchCtx({ summary: val })}
                className="min-h-[60px] text-xs leading-relaxed"
                placeholder="Role-specific grounding narrative for LLM stages..."
              />
            </div>
            <div>
              <FieldLabel>What matters here</FieldLabel>
              <AutoTextarea
                value={arrToLines(ctx.what_matters_here)}
                onChange={(val) =>
                  patchCtx({ what_matters_here: linesToArr(val) })
                }
                className="min-h-[48px] text-xs leading-relaxed"
                placeholder="Role-specific signals that matter, one per line..."
              />
            </div>
            <div>
              <FieldLabel>Hiring bar</FieldLabel>
              <AutoTextarea
                value={ctx.hiring_bar || ""}
                onChange={(val) => patchCtx({ hiring_bar: val })}
                className="min-h-[36px] text-xs leading-relaxed"
                placeholder="What clearing the bar looks like for this role..."
              />
            </div>
          </div>
        </Section>

        {/* ====== ASSIGNMENT ====== */}
        {/* Gated on the pipeline including an enabled assignment stage; if the
            draft has no pipeline, falls back to showing it. */}
        {hasAssignmentStage && (
          <Section title="Take-home Assignment" defaultOpen={false}>
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded accent-primary"
                  checked={draft.assignment?.enabled !== false}
                  onChange={(e) =>
                    patch({
                      assignment: {
                        ...draft.assignment,
                        enabled: e.target.checked,
                      },
                    })
                  }
                />
                Enable take-home assignment
              </label>
              {draft.assignment?.enabled !== false && (
                <div className="space-y-4">
                  <div>
                    <FieldLabel>Assignment brief (markdown)</FieldLabel>
                    <AutoTextarea
                      value={draft.assignment?.brief || ""}
                      onChange={(val) =>
                        patch({
                          assignment: {
                            ...draft.assignment,
                            brief: val,
                          },
                        })
                      }
                      placeholder="Describe the take-home assignment. If the hiring manager described their own brief, capture it here verbatim. Otherwise leave empty and Pulse can draft one later."
                      className="min-h-[80px] text-xs leading-relaxed"
                    />
                  </div>
                  <div>
                    <FieldLabel>Submission instructions</FieldLabel>
                    <AutoTextarea
                      value={draft.assignment?.instructions || ""}
                      onChange={(val) =>
                        patch({
                          assignment: {
                            ...draft.assignment,
                            instructions: val,
                          },
                        })
                      }
                      placeholder="Any specific submission instructions (format, repo link, deadline notes)..."
                      className="min-h-[60px] text-xs leading-relaxed"
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <FieldLabel>Problems</FieldLabel>
                      <Input
                        type="number"
                        min={1}
                        max={8}
                        value={draft.assignment?.n_problems ?? 2}
                        onChange={(e) =>
                          patch({
                            assignment: {
                              ...draft.assignment,
                              n_problems: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </div>
                    <div>
                      <FieldLabel>Hours</FieldLabel>
                      <Input
                        type="number"
                        min={1}
                        max={40}
                        value={draft.assignment?.time_budget_hours ?? 6}
                        onChange={(e) =>
                          patch({
                            assignment: {
                              ...draft.assignment,
                              time_budget_hours: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </div>
                    <div>
                      <FieldLabel>Deadline (days)</FieldLabel>
                      <Input
                        type="number"
                        min={1}
                        max={60}
                        value={draft.assignment?.deadline_days ?? 7}
                        onChange={(e) =>
                          patch({
                            assignment: {
                              ...draft.assignment,
                              deadline_days: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          </Section>
        )}

        {/* ====== NOTES ====== */}
        <Section title="Notes" defaultOpen={false}>
          <AutoTextarea
            value={draft.notes || ""}
            onChange={(val) => patch({ notes: val })}
            className="min-h-[60px] text-xs leading-relaxed"
            placeholder="Internal notes about this role..."
          />
        </Section>
        </div>
      </div>

      {/* Footer — sticky action bar */}
      <footer className={cn("shrink-0 border-t border-border/50 bg-muted/30 py-3", expanded ? "px-8" : "px-5")}>
        <div className={cn("flex items-center justify-between gap-2", expanded && "mx-auto max-w-3xl")}>
          <span className="font-mono text-[10px] text-muted-foreground">
            {dirty ? "Unsaved changes" : applied ? "Applied" : "Saved"}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleSave}
              disabled={!dirty || saving}
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
            </Button>
            <Button
              size="sm"
              onClick={handleApply}
              disabled={applying || applied}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {applying ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : applied ? (
                <>
                  <Check className="h-4 w-4" /> Applied
                </>
              ) : (
                "Apply"
              )}
            </Button>
          </div>
        </div>
      </footer>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/* Auto-resizing textarea                                              */
/* ------------------------------------------------------------------ */
function AutoTextarea({
  value,
  onChange,
  className,
  placeholder,
}: {
  value: string;
  onChange: (val: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (document.activeElement !== el) {
      el.value = value;
    }
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      defaultValue={value}
      className={cn(
        "flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      onBlur={(e) => onChange(e.target.value)}
      placeholder={placeholder}
    />
  );
}
