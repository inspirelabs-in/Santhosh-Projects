"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  FileText,
  GripVertical,
  Loader2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import type { ArtifactData } from "@/lib/useRecruiterChat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
  intensity?: string;
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
    n_problems?: number;
    time_budget_hours?: number;
    deadline_days?: number;
  };
  notes?: string | null;
}

const STAGE_TYPES = [
  "intake",
  "parse",
  "fit",
  "screening",
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

/* ------------------------------------------------------------------ */
/* Collapsible section                                                 */
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
    <div className="rounded-lg border border-border/40 bg-background/50">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium hover:bg-muted/30"
      >
        <span className="flex items-center gap-2">
          {title}
          {badge}
        </span>
        {open ? (
          <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
      {open && <div className="border-t border-border/30 px-4 py-3">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main panel                                                          */
/* ------------------------------------------------------------------ */
export function ArtifactPanel({
  artifact,
  onClose,
  onSave,
  onApply,
}: {
  artifact: ArtifactData;
  onClose: () => void;
  onSave: (content: Record<string, unknown>) => Promise<void>;
  onApply: () => Promise<{ ok: boolean; role_url?: string } | null>;
}) {
  const [draft, setDraft] = useState<Draft>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);

  // Resize state
  const panelRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(520);
  const [resizing, setResizing] = useState(false);

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
    if (res?.ok && res.role_url) {
      try {
        window.open(res.role_url, "_blank");
      } catch {
        /* ignore */
      }
    }
  };

  const weightTotal = dims.reduce((s, d) => s + (Number(d.weight) || 0), 0);

  /* -- Drag-to-resize -- */
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setResizing(true);
      const startX = e.clientX;
      const startW = width;
      const onMove = (ev: MouseEvent) => {
        setWidth(Math.max(400, Math.min(900, startW + startX - ev.clientX)));
      };
      const onUp = () => {
        setResizing(false);
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [width],
  );

  return (
    <aside
      ref={panelRef}
      className="relative flex h-full shrink-0 flex-col border-l border-border/40 bg-card/40"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={onMouseDown}
        className={cn(
          "group/handle absolute left-0 top-0 z-10 flex h-full w-1.5 cursor-col-resize items-center justify-center hover:bg-brand-green/30",
          resizing && "bg-brand-green/40",
        )}
      >
        <GripVertical className="h-4 w-4 text-muted-foreground/40 opacity-0 group-hover/handle:opacity-100" />
      </div>

      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-border/40 px-4 py-3">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-brand-green" />
          <span className="text-sm font-semibold">Role draft</span>
          <Badge variant="outline" className="text-[10px] font-mono">
            v{artifact.version} · {artifact.status}
          </Badge>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
          <X className="h-4 w-4" />
        </Button>
      </header>

      {/* Scrollable body */}
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {/* ====== BASICS ====== */}
        <Section title="Basics" defaultOpen={true}>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Title</Label>
              <Input
                value={draft.title || ""}
                onChange={(e) => patch({ title: e.target.value })}
                placeholder="e.g. Senior Backend Engineer"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">CTC min (LPA)</Label>
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
              <div className="space-y-1.5">
                <Label className="text-xs">CTC max (LPA)</Label>
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
              <div className="space-y-1.5">
                <Label className="text-xs">Location</Label>
                <Input
                  value={draft.location || ""}
                  onChange={(e) => patch({ location: e.target.value })}
                  placeholder="Hyderabad"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Remote policy</Label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={draft.remote_policy || ""}
                  onChange={(e) => patch({ remote_policy: e.target.value })}
                >
                  <option value="">Select...</option>
                  <option value="onsite">Onsite</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="remote">Remote</option>
                </select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Max notice period (days)</Label>
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
            <Badge variant="outline" className="text-[10px]">
              {stages.filter((s) => s.is_enabled !== false).length} stages
            </Badge>
          }
        >
          <div className="space-y-2">
            {stages.map((s, i) => (
              <div
                key={i}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-2.5 py-2 transition-colors",
                  s.is_enabled !== false
                    ? "border-border/50 bg-background"
                    : "border-border/30 bg-muted/30 opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 rounded accent-brand-green"
                  checked={s.is_enabled !== false}
                  onChange={(e) => setStage(i, { is_enabled: e.target.checked })}
                />
                <Input
                  className="h-7 flex-1 border-0 bg-transparent px-1 text-sm shadow-none focus-visible:ring-0"
                  value={s.label || ""}
                  onChange={(e) => setStage(i, { label: e.target.value })}
                />
                <select
                  className="h-7 rounded border border-input bg-background px-1.5 text-xs"
                  value={s.stage_type || "interview"}
                  onChange={(e) => setStage(i, { stage_type: e.target.value })}
                >
                  {STAGE_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {STAGE_TYPE_LABELS[t] || t}
                    </option>
                  ))}
                </select>
                <select
                  className="h-7 rounded border border-input bg-background px-1.5 text-xs"
                  value={s.mode || "manual"}
                  onChange={(e) => setStage(i, { mode: e.target.value })}
                >
                  <option value="auto">Auto</option>
                  <option value="manual">Manual</option>
                </select>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
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
              className="h-auto gap-1.5 px-2 text-xs text-brand-green hover:text-brand-green"
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
                "text-[10px] font-mono",
                weightTotal >= 95 && weightTotal <= 105
                  ? "text-muted-foreground"
                  : "text-amber-500",
              )}
            >
              {weightTotal}/100
            </span>
          }
        >
          <div className="space-y-3">
            {dims.map((d, i) => (
              <div
                key={i}
                className="space-y-2 rounded-md border border-border/40 p-3"
              >
                <div className="flex items-center gap-2">
                  <Input
                    className="h-7 flex-1 text-sm font-medium"
                    value={d.label || ""}
                    onChange={(e) => setDim(i, { label: e.target.value })}
                    placeholder="Dimension label"
                  />
                  <div className="flex items-center gap-1">
                    <Input
                      type="number"
                      className="h-7 w-14 text-center text-xs"
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
                    className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => removeDim(i)}
                    aria-label="Remove dimension"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">
                    What good looks like (one per line)
                  </Label>
                  <AutoTextarea
                    value={arrToLines(d.what_good_looks_like)}
                    onChange={(val) =>
                      setDim(i, { what_good_looks_like: linesToArr(val) })
                    }
                    className="min-h-[48px] text-xs"
                    placeholder="Concrete signals of a strong candidate..."
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">
                    Anti-signals (one per line)
                  </Label>
                  <AutoTextarea
                    value={arrToLines(d.anti_signals)}
                    onChange={(val) =>
                      setDim(i, { anti_signals: linesToArr(val) })
                    }
                    className="min-h-[36px] text-xs"
                    placeholder="Red flags or weak signals..."
                  />
                </div>
              </div>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onClick={addDim}
              className="h-auto gap-1.5 px-2 text-xs text-brand-green hover:text-brand-green"
            >
              <Plus className="h-3 w-3" /> Add dimension
            </Button>
          </div>

          {/* Knockouts */}
          <div className="mt-4 space-y-2">
            <Label className="text-xs font-medium">
              Knockouts (hard disqualifiers)
            </Label>
            {knockouts.map((k, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  className="h-7 flex-1 text-xs"
                  value={k.rule || ""}
                  onChange={(e) => setKnockout(i, { rule: e.target.value })}
                  placeholder="e.g. No experience with databases at all"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
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
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Intensity</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={ctx.intensity || "standard"}
                onChange={(e) => patchCtx({ intensity: e.target.value })}
              >
                <option value="light">Light (junior/contract)</option>
                <option value="standard">Standard (mid-level)</option>
                <option value="high">High (senior/lead)</option>
                <option value="critical">Critical (staff/exec)</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Summary</Label>
              <AutoTextarea
                value={ctx.summary || ""}
                onChange={(val) => patchCtx({ summary: val })}
                className="min-h-[60px] text-xs"
                placeholder="Role-specific grounding narrative for LLM stages..."
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">What matters here (one per line)</Label>
              <AutoTextarea
                value={arrToLines(ctx.what_matters_here)}
                onChange={(val) =>
                  patchCtx({ what_matters_here: linesToArr(val) })
                }
                className="min-h-[48px] text-xs"
                placeholder="Role-specific signals that matter..."
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Hiring bar</Label>
              <AutoTextarea
                value={ctx.hiring_bar || ""}
                onChange={(val) => patchCtx({ hiring_bar: val })}
                className="min-h-[36px] text-xs"
                placeholder="What clearing the bar looks like for this role..."
              />
            </div>
          </div>
        </Section>

        {/* ====== ASSIGNMENT ====== */}
        <Section title="Take-home Assignment" defaultOpen={false}>
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4 rounded accent-brand-green"
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
              <div className="grid grid-cols-3 gap-3">
                <div className="space-y-1">
                  <Label className="text-[11px]">Problems</Label>
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
                <div className="space-y-1">
                  <Label className="text-[11px]">Hours</Label>
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
                <div className="space-y-1">
                  <Label className="text-[11px]">Deadline (days)</Label>
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
            )}
          </div>
        </Section>

        {/* ====== NOTES ====== */}
        <Section title="Notes" defaultOpen={false}>
          <AutoTextarea
            value={draft.notes || ""}
            onChange={(val) => patch({ notes: val })}
            className="min-h-[60px] text-xs"
            placeholder="Internal notes about this role..."
          />
        </Section>
      </div>

      {/* Footer */}
      <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-border/40 px-4 py-3">
        <span className="text-xs text-muted-foreground">
          {dirty ? "Unsaved changes" : applied ? "Applied" : "Saved"}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={!dirty || saving || applied}
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
          </Button>
          <Button
            size="sm"
            onClick={handleApply}
            disabled={applying || applied}
            className="bg-brand-green text-white hover:bg-brand-green/90"
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
