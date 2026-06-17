"use client";

import React, { useState, useRef, useCallback, useMemo } from "react";
import {
  Zap,
  Phone,
  MessageCircle,
  FileText,
  Brain,
  Code,
  UserCheck,
  Crown,
  HeartHandshake,
  Users,
  Shield,
  PhoneForwarded,
  Search,
  Gift,
  Plus,
  GripVertical,
  X,
  ChevronRight,
  AlertTriangle,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PipelineStepDef {
  id: string;
  label: string;
  icon: string;
  category: "screening" | "assessment" | "interview" | "verification" | "terminal";
  description: string;
  required?: boolean;
  defaultEnabled?: boolean;
  fixed?: boolean;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

export const PIPELINE_STEPS: PipelineStepDef[] = [
  { id: "fit_score", label: "Fit Score", icon: "Zap", category: "screening", description: "AI scores resume against job description", required: false, defaultEnabled: true },
  { id: "voice_screen", label: "Voice Screen", icon: "Phone", category: "screening", description: "AI phone screening call", required: false },
  { id: "assignment", label: "Take-Home", icon: "FileText", category: "assessment", description: "Take-home assignment with problems", required: false },
  { id: "technical_interview", label: "Technical Round", icon: "Code", category: "interview", description: "Technical interview with engineering panel", required: false },
  { id: "hiring_manager", label: "Hiring Manager", icon: "UserCheck", category: "interview", description: "Interview with hiring manager", required: false },
  { id: "ceo_interview", label: "CEO Round", icon: "Crown", category: "interview", description: "CEO or founder interview", required: false },
  { id: "hr_interview", label: "HR Discussion", icon: "HeartHandshake", category: "interview", description: "HR culture fit and comp discussion", required: false },
  { id: "panel_interview", label: "Panel Round", icon: "Users", category: "interview", description: "Multi-person panel interview", required: false },
  { id: "bar_raiser", label: "Bar Raiser", icon: "Shield", category: "interview", description: "Cross-team bar raiser interview", required: false },
  { id: "reference_check", label: "Reference Check", icon: "PhoneForwarded", category: "verification", description: "Contact and verify references", required: false },
  { id: "background_check", label: "Background Check", icon: "Search", category: "verification", description: "Background verification", required: false },
  { id: "offer", label: "Offer", icon: "Gift", category: "terminal", description: "Generate and send offer letter", required: true, fixed: true },
];

export const PRESETS: Record<string, { label: string; description: string; steps: string[] }> = {
  standard_engineer: { label: "Standard Engineer", description: "Full pipeline with assignment + 3 interview rounds", steps: ["fit_score", "voice_screen", "assignment", "technical_interview", "ceo_interview", "hr_interview", "offer"] },
  senior_engineer: { label: "Senior/Staff Engineer", description: "Technical depth with architecture review", steps: ["fit_score", "voice_screen", "assignment", "technical_interview", "bar_raiser", "ceo_interview", "offer"] },
  intern: { label: "Intern / Fresher", description: "Lightweight — assignment and HR only", steps: ["fit_score", "assignment", "hr_interview", "offer"] },
  executive: { label: "Executive / CXO", description: "CEO-heavy, no assignment", steps: ["fit_score", "voice_screen", "ceo_interview", "hr_interview", "offer"] },
  referral: { label: "Referral", description: "Skip screening — trusted source", steps: ["assignment", "technical_interview", "hr_interview", "offer"] },
  contract: { label: "Contract / Freelance", description: "Minimal, fast track", steps: ["fit_score", "technical_interview", "offer"] },
  campus: { label: "Campus / Bulk", description: "Group screening for volume hiring", steps: ["fit_score", "voice_screen", "hr_interview", "offer"] },
  internal: { label: "Internal Transfer", description: "Minimal formality", steps: ["hiring_manager", "hr_interview", "offer"] },
  rehire: { label: "Re-hire", description: "Already vetted", steps: ["hr_interview", "offer"] },
};

// ---------------------------------------------------------------------------
// Icon map
// ---------------------------------------------------------------------------

const ICON_MAP: Record<string, LucideIcon> = {
  Zap,
  Phone,
  MessageCircle,
  FileText,
  Brain,
  Code,
  UserCheck,
  Crown,
  HeartHandshake,
  Users,
  Shield,
  PhoneForwarded,
  Search,
  Gift,
};

function getIcon(name: string): LucideIcon {
  return ICON_MAP[name] ?? Zap;
}

// ---------------------------------------------------------------------------
// Category styling
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, { bg: string; border: string; badge: string; text: string; dragBorder: string }> = {
  screening: {
    bg: "bg-blue-50 dark:bg-blue-950/40",
    border: "border-blue-200 dark:border-blue-800",
    badge: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    text: "text-blue-600 dark:text-blue-400",
    dragBorder: "border-blue-400 dark:border-blue-500",
  },
  assessment: {
    bg: "bg-amber-50 dark:bg-amber-950/40",
    border: "border-amber-200 dark:border-amber-800",
    badge: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
    text: "text-amber-600 dark:text-amber-400",
    dragBorder: "border-amber-400 dark:border-amber-500",
  },
  interview: {
    bg: "bg-violet-50 dark:bg-violet-950/40",
    border: "border-violet-200 dark:border-violet-800",
    badge: "bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300",
    text: "text-violet-600 dark:text-violet-400",
    dragBorder: "border-violet-400 dark:border-violet-500",
  },
  verification: {
    bg: "bg-slate-50 dark:bg-slate-900/40",
    border: "border-slate-200 dark:border-slate-700",
    badge: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
    text: "text-slate-600 dark:text-slate-400",
    dragBorder: "border-slate-400 dark:border-slate-500",
  },
  terminal: {
    bg: "bg-emerald-50 dark:bg-emerald-950/40",
    border: "border-emerald-200 dark:border-emerald-800",
    badge: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
    text: "text-emerald-600 dark:text-emerald-400",
    dragBorder: "border-emerald-400 dark:border-emerald-500",
  },
};

function catStyle(category: string) {
  return CATEGORY_COLORS[category] ?? CATEGORY_COLORS.screening;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STEP_MAP = new Map(PIPELINE_STEPS.map((s) => [s.id, s]));

function getStep(id: string): PipelineStepDef | undefined {
  return STEP_MAP.get(id);
}

const SCREENING_IDS = new Set(["fit_score", "voice_screen"]);
const INTERVIEW_IDS = new Set(["technical_interview", "hiring_manager", "ceo_interview", "hr_interview", "panel_interview", "bar_raiser"]);

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface StepCardProps {
  step: PipelineStepDef;
  index: number;
  compact: boolean;
  isDragging: boolean;
  isDropTarget: boolean;
  onRemove: () => void;
  onDragStart: (e: React.DragEvent, idx: number) => void;
  onDragOver: (e: React.DragEvent, idx: number) => void;
  onDragEnd: () => void;
  onDrop: (e: React.DragEvent, idx: number) => void;
}

function StepCard({
  step,
  index,
  compact,
  isDragging,
  isDropTarget,
  onRemove,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
}: StepCardProps) {
  const colors = catStyle(step.category);
  const Icon = getIcon(step.icon);
  const isFixed = step.fixed;

  if (compact) {
    return (
      <div
        draggable={!isFixed}
        onDragStart={(e) => onDragStart(e, index)}
        onDragOver={(e) => onDragOver(e, index)}
        onDragEnd={onDragEnd}
        onDrop={(e) => onDrop(e, index)}
        className={cn(
          "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-all select-none",
          colors.bg,
          colors.border,
          isDragging && "opacity-40",
          isDropTarget && "ring-2 ring-offset-1 ring-blue-400 dark:ring-blue-500",
          !isFixed && "cursor-grab active:cursor-grabbing",
        )}
      >
        {!isFixed && <GripVertical className="h-3 w-3 shrink-0 text-muted-foreground/50" />}
        <Icon className={cn("h-3.5 w-3.5 shrink-0", colors.text)} />
        <span className="font-medium text-foreground truncate">{step.label}</span>
        {!isFixed && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            className="ml-auto shrink-0 rounded p-0.5 hover:bg-destructive/10 hover:text-destructive transition-colors"
            aria-label={`Remove ${step.label}`}
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      draggable={!isFixed}
      onDragStart={(e) => onDragStart(e, index)}
      onDragOver={(e) => onDragOver(e, index)}
      onDragEnd={onDragEnd}
      onDrop={(e) => onDrop(e, index)}
      className={cn(
        "group relative flex items-start gap-3 rounded-lg border p-3 transition-all select-none min-w-[180px] max-w-[220px]",
        colors.bg,
        colors.border,
        isDragging && "opacity-40 scale-95",
        isDropTarget && cn("ring-2 ring-offset-2 ring-offset-background", colors.dragBorder),
        !isFixed && "cursor-grab active:cursor-grabbing",
      )}
    >
      {/* Drag handle */}
      {!isFixed && (
        <div className="flex flex-col items-center justify-center pt-0.5">
          <GripVertical className="h-4 w-4 text-muted-foreground/40 group-hover:text-muted-foreground/70 transition-colors" />
        </div>
      )}

      {/* Icon */}
      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-md", colors.badge)}>
        <Icon className="h-4 w-4" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground truncate">{step.label}</span>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2 leading-relaxed">
          {step.description}
        </p>
        <span className={cn("mt-1.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide", colors.badge)}>
          {step.category}
        </span>
      </div>

      {/* Remove button */}
      {!isFixed && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm opacity-0 group-hover:opacity-100 hover:bg-destructive hover:text-destructive-foreground hover:border-destructive transition-all"
          aria-label={`Remove ${step.label}`}
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

function Arrow({ compact }: { compact: boolean }) {
  return (
    <div className={cn("flex shrink-0 items-center", compact ? "px-0.5" : "px-1")}>
      <ChevronRight className={cn("text-muted-foreground/40", compact ? "h-3 w-3" : "h-4 w-4")} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add Step Dropdown
// ---------------------------------------------------------------------------

interface AddStepDropdownProps {
  availableSteps: PipelineStepDef[];
  onAdd: (id: string) => void;
  compact: boolean;
}

function AddStepDropdown({ availableSteps, onAdd, compact }: AddStepDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  React.useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const grouped = useMemo(() => {
    const groups: Record<string, PipelineStepDef[]> = {};
    for (const s of availableSteps) {
      if (s.fixed) continue; // don't show terminal steps
      (groups[s.category] ??= []).push(s);
    }
    return groups;
  }, [availableSteps]);

  if (availableSteps.filter((s) => !s.fixed).length === 0) return null;

  return (
    <div ref={ref} className="relative shrink-0">
      <Button
        type="button"
        variant="outline"
        size={compact ? "sm" : "default"}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "border-dashed",
          compact ? "h-7 px-2 text-xs gap-1" : "gap-1.5",
        )}
      >
        <Plus className={cn(compact ? "h-3 w-3" : "h-4 w-4")} />
        Add step
        <ChevronDown className={cn("transition-transform", compact ? "h-3 w-3" : "h-3.5 w-3.5", open && "rotate-180")} />
      </Button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-64 rounded-lg border bg-popover p-1 shadow-lg animate-in fade-in-0 zoom-in-95">
          {Object.entries(grouped).map(([cat, steps]) => {
            const colors = catStyle(cat);
            return (
              <div key={cat}>
                <div className={cn("px-2 py-1 text-[10px] font-semibold uppercase tracking-wider", colors.text)}>
                  {cat}
                </div>
                {steps.map((s) => {
                  const Icon = getIcon(s.icon);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => { onAdd(s.id); setOpen(false); }}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent transition-colors"
                    >
                      <Icon className={cn("h-4 w-4 shrink-0", colors.text)} />
                      <div className="min-w-0">
                        <div className="font-medium text-foreground truncate">{s.label}</div>
                        <div className="text-xs text-muted-foreground truncate">{s.description}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preset Picker
// ---------------------------------------------------------------------------

interface PresetPickerProps {
  onSelect: (steps: string[]) => void;
  activePreset: string | null;
}

function PresetPicker({ onSelect, activePreset }: PresetPickerProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {Object.entries(PRESETS).map(([key, preset]) => (
        <button
          key={key}
          type="button"
          onClick={() => onSelect(preset.steps)}
          className={cn(
            "rounded-lg border px-3 py-2 text-left transition-all hover:shadow-sm",
            activePreset === key
              ? "border-primary bg-primary/5 ring-1 ring-primary/20 dark:bg-primary/10"
              : "border-border bg-card hover:border-primary/30 hover:bg-accent/50",
          )}
        >
          <div className="text-sm font-semibold text-foreground">{preset.label}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">{preset.description}</div>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Validation warnings
// ---------------------------------------------------------------------------

function ValidationWarnings({ steps }: { steps: string[] }) {
  const warnings: string[] = [];

  const hasScreening = steps.some((id) => SCREENING_IDS.has(id));
  const hasInterview = steps.some((id) => INTERVIEW_IDS.has(id));

  if (!hasScreening) {
    warnings.push("Pipeline has no screening step. Consider adding Fit Score or Voice Screen to filter candidates early.");
  }

  if (hasInterview && !hasScreening) {
    warnings.push("Interview rounds without screening may overwhelm interviewers with unqualified candidates.");
  }

  if (warnings.length === 0) return null;

  return (
    <div className="space-y-1.5">
      {warnings.map((w, i) => (
        <div
          key={i}
          className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export interface PipelineBuilderProps {
  value: string[];
  onChange: (steps: string[]) => void;
  compact?: boolean;
}

export function PipelineBuilder({ value, onChange, compact = false }: PipelineBuilderProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  // Determine which preset is currently active
  const activePreset = useMemo(() => {
    const key = value.join(",");
    for (const [k, p] of Object.entries(PRESETS)) {
      if (p.steps.join(",") === key) return k;
    }
    return null;
  }, [value]);

  // Steps currently in the pipeline (resolved to defs)
  const activeSteps = useMemo(() => {
    return value.map((id) => getStep(id)).filter(Boolean) as PipelineStepDef[];
  }, [value]);

  // Steps available to add
  const availableSteps = useMemo(() => {
    const inPipeline = new Set(value);
    return PIPELINE_STEPS.filter((s) => !inPipeline.has(s.id));
  }, [value]);

  // Ensure "offer" is always at the end
  const ensureOffer = useCallback((steps: string[]) => {
    const without = steps.filter((s) => s !== "offer");
    return [...without, "offer"];
  }, []);

  // Handlers
  const handlePresetSelect = useCallback(
    (steps: string[]) => {
      onChange(ensureOffer(steps));
    },
    [onChange, ensureOffer],
  );

  const handleRemove = useCallback(
    (index: number) => {
      const next = value.filter((_, i) => i !== index);
      onChange(ensureOffer(next));
    },
    [value, onChange, ensureOffer],
  );

  const handleAdd = useCallback(
    (id: string) => {
      if (value.includes(id)) return;
      // Insert before "offer"
      const without = value.filter((s) => s !== "offer");
      onChange([...without, id, "offer"]);
    },
    [value, onChange],
  );

  // Drag and drop
  const handleDragStart = useCallback((e: React.DragEvent, idx: number) => {
    const step = STEP_MAP.get(value[idx]);
    if (step?.fixed) { e.preventDefault(); return; }
    setDragIndex(idx);
    e.dataTransfer.effectAllowed = "move";
    // Transparent drag image
    const ghost = document.createElement("div");
    ghost.style.opacity = "0";
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 0, 0);
    setTimeout(() => document.body.removeChild(ghost), 0);
  }, [value]);

  const handleDragOver = useCallback((e: React.DragEvent, idx: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    // Don't allow dropping on the fixed "offer" position
    const step = STEP_MAP.get(value[idx]);
    if (step?.fixed) return;
    setDropIndex(idx);
  }, [value]);

  const handleDrop = useCallback(
    (e: React.DragEvent, targetIdx: number) => {
      e.preventDefault();
      if (dragIndex === null || dragIndex === targetIdx) {
        setDragIndex(null);
        setDropIndex(null);
        return;
      }
      const step = STEP_MAP.get(value[targetIdx]);
      if (step?.fixed) { setDragIndex(null); setDropIndex(null); return; }

      const next = [...value];
      const [moved] = next.splice(dragIndex, 1);
      next.splice(targetIdx, 0, moved);
      onChange(ensureOffer(next));
      setDragIndex(null);
      setDropIndex(null);
    },
    [dragIndex, value, onChange, ensureOffer],
  );

  const handleDragEnd = useCallback(() => {
    setDragIndex(null);
    setDropIndex(null);
  }, []);

  return (
    <div className={cn("space-y-4", compact && "space-y-2")}>
      {/* Preset picker — hidden in compact mode */}
      {!compact && (
        <div>
          <label className="mb-2 block text-sm font-medium text-foreground">
            Start from a template
          </label>
          <PresetPicker onSelect={handlePresetSelect} activePreset={activePreset} />
        </div>
      )}

      {/* Pipeline canvas */}
      <div>
        {!compact && (
          <label className="mb-2 block text-sm font-medium text-foreground">
            Pipeline steps
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              Drag to reorder
            </span>
          </label>
        )}

        <div
          className={cn(
            "flex items-center gap-0 overflow-x-auto rounded-lg border bg-background p-3",
            compact ? "p-2 gap-0" : "p-4",
          )}
          onDragOver={(e) => e.preventDefault()}
        >
          {activeSteps.map((step, idx) => (
            <React.Fragment key={step.id}>
              {idx > 0 && <Arrow compact={compact} />}
              <StepCard
                step={step}
                index={idx}
                compact={compact}
                isDragging={dragIndex === idx}
                isDropTarget={dropIndex === idx && dragIndex !== idx}
                onRemove={() => handleRemove(idx)}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDragEnd={handleDragEnd}
                onDrop={handleDrop}
              />
            </React.Fragment>
          ))}

          {/* Add step button */}
          <Arrow compact={compact} />
          <AddStepDropdown
            availableSteps={availableSteps}
            onAdd={handleAdd}
            compact={compact}
          />
        </div>
      </div>

      {/* Validation warnings */}
      {!compact && <ValidationWarnings steps={value} />}
    </div>
  );
}
