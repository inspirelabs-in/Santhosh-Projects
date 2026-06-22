"use client";

/**
 * Artifact side panel — editable structured output from Pulse.
 *
 * v1 renders a `role_draft` artifact (JD + pipeline + evaluation spec +
 * assignment) as an editable form. Human edits Save (PATCH); the agent can also
 * rewrite the same artifact (a new `artifact` event re-seeds this panel). Apply
 * turns it into a real Role.
 *
 * Self-contained on purpose: it does not reuse the confirm-card internals, so
 * the confirm flow stays untouched.
 */

import { useEffect, useState } from "react";
import { Check, FileText, Loader2, Plus, X } from "lucide-react";
import type { ArtifactData } from "@/lib/useRecruiterChat";

interface Dim {
  key?: string;
  label?: string;
  weight?: number;
  what_good_looks_like?: string[];
  anti_signals?: string[];
}
interface Stage {
  stage_key?: string;
  stage_type?: string;
  label?: string;
  position?: number;
  mode?: string;
  is_enabled?: boolean;
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
  evaluation_spec?: { dimensions?: Dim[]; knockouts?: { key?: string; rule?: string }[] };
  assignment?: { enabled?: boolean; n_problems?: number; time_budget_hours?: number; deadline_days?: number };
  notes?: string | null;
}

const STAGE_TYPES = [
  "intake", "parse", "fit", "screening", "voice_screen", "assignment",
  "assessment_review", "interview", "decision", "offer",
];

const linesToArr = (s: string): string[] =>
  s.split("\n").map((x) => x.trim()).filter(Boolean);
const arrToLines = (a?: string[]): string => (a || []).join("\n");

const inputCls =
  "w-full rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-sm focus:border-brand-green focus:outline-none";
const labelCls = "text-[11px] font-medium uppercase tracking-wide text-muted-foreground";

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

  // Re-seed when the artifact changes (new version from a human save OR an
  // agent rewrite). Keyed on id + version so agent edits refresh the form.
  useEffect(() => {
    setDraft((artifact.content as Draft) || {});
    setDirty(false);
  }, [artifact.id, artifact.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const applied = artifact.status === "applied";
  const patch = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setDirty(true);
  };

  const stages = draft.pipeline || [];
  const dims = draft.evaluation_spec?.dimensions || [];

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
        { stage_key: `interview_${stages.length + 1}`, stage_type: "interview", label: "Interview round", mode: "manual", is_enabled: true },
      ],
    });

  const setDim = (i: number, p: Partial<Dim>) => {
    const next = [...dims];
    next[i] = { ...next[i], ...p };
    patch({ evaluation_spec: { ...draft.evaluation_spec, dimensions: next } });
  };
  const removeDim = (i: number) =>
    patch({ evaluation_spec: { ...draft.evaluation_spec, dimensions: dims.filter((_, j) => j !== i) } });
  const addDim = () =>
    patch({
      evaluation_spec: {
        ...draft.evaluation_spec,
        dimensions: [...dims, { key: `dim_${dims.length + 1}`, label: "New dimension", weight: 0, what_good_looks_like: [], anti_signals: [] }],
      },
    });

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

  return (
    <aside className="flex h-full w-[440px] shrink-0 flex-col border-l border-border/40 bg-card/40">
      <header className="flex shrink-0 items-center justify-between border-b border-border/40 px-4 py-3">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-brand-green" />
          <span className="text-sm font-semibold">Role draft</span>
          <span className="rounded-full border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
            v{artifact.version} · {artifact.status}
          </span>
        </div>
        <button onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label="Close panel">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <div>
          <label className={labelCls}>Title</label>
          <input className={inputCls} value={draft.title || ""} onChange={(e) => patch({ title: e.target.value })} />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={labelCls}>CTC min (LPA)</label>
            <input type="number" className={inputCls} value={draft.ctc_min_lpa ?? ""} onChange={(e) => patch({ ctc_min_lpa: e.target.value === "" ? null : Number(e.target.value) })} />
          </div>
          <div>
            <label className={labelCls}>CTC max (LPA)</label>
            <input type="number" className={inputCls} value={draft.ctc_max_lpa ?? ""} onChange={(e) => patch({ ctc_max_lpa: e.target.value === "" ? null : Number(e.target.value) })} />
          </div>
          <div>
            <label className={labelCls}>Location</label>
            <input className={inputCls} value={draft.location || ""} onChange={(e) => patch({ location: e.target.value })} />
          </div>
          <div>
            <label className={labelCls}>Remote policy</label>
            <select className={inputCls} value={draft.remote_policy || ""} onChange={(e) => patch({ remote_policy: e.target.value })}>
              <option value="">—</option>
              <option value="onsite">onsite</option>
              <option value="hybrid">hybrid</option>
              <option value="remote">remote</option>
            </select>
          </div>
        </div>

        <div>
          <label className={labelCls}>Job description</label>
          <textarea className={`${inputCls} min-h-[140px] font-mono text-xs`} value={draft.jd_text || ""} onChange={(e) => patch({ jd_text: e.target.value })} />
        </div>

        {/* Pipeline */}
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className={labelCls}>Pipeline</label>
            <button onClick={addStage} className="flex items-center gap-1 text-[11px] text-brand-green hover:underline">
              <Plus className="h-3 w-3" /> add stage
            </button>
          </div>
          <div className="space-y-1.5">
            {stages.map((s, i) => (
              <div key={i} className="flex items-center gap-2 rounded-md border border-border/50 px-2 py-1.5">
                <input type="checkbox" checked={s.is_enabled !== false} onChange={(e) => setStage(i, { is_enabled: e.target.checked })} />
                <input className="flex-1 bg-transparent text-sm focus:outline-none" value={s.label || ""} onChange={(e) => setStage(i, { label: e.target.value })} />
                <select className="rounded border border-border/50 bg-background px-1 py-0.5 text-[11px]" value={s.stage_type || "interview"} onChange={(e) => setStage(i, { stage_type: e.target.value })}>
                  {STAGE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <select className="rounded border border-border/50 bg-background px-1 py-0.5 text-[11px]" value={s.mode || "manual"} onChange={(e) => setStage(i, { mode: e.target.value })}>
                  <option value="auto">auto</option>
                  <option value="manual">manual</option>
                </select>
                <button onClick={() => removeStage(i)} className="text-muted-foreground hover:text-destructive" aria-label="Remove stage">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Evaluation spec */}
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className={labelCls}>Evaluation dimensions</label>
            <span className={weightTotal === 100 ? "text-[11px] text-muted-foreground" : "text-[11px] text-amber-600"}>
              Σ {weightTotal} {weightTotal !== 100 ? "(should be 100)" : ""}
            </span>
          </div>
          <div className="space-y-2">
            {dims.map((d, i) => (
              <div key={i} className="rounded-md border border-border/50 p-2">
                <div className="flex items-center gap-2">
                  <input className="flex-1 bg-transparent text-sm font-medium focus:outline-none" value={d.label || ""} onChange={(e) => setDim(i, { label: e.target.value })} />
                  <input type="number" className="w-14 rounded border border-border/50 bg-background px-1 py-0.5 text-xs" value={d.weight ?? 0} onChange={(e) => setDim(i, { weight: Number(e.target.value) })} />
                  <button onClick={() => removeDim(i)} className="text-muted-foreground hover:text-destructive" aria-label="Remove dimension">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <textarea className={`${inputCls} mt-1 min-h-[40px] text-xs`} placeholder="What good looks like (one per line)" value={arrToLines(d.what_good_looks_like)} onChange={(e) => setDim(i, { what_good_looks_like: linesToArr(e.target.value) })} />
                <textarea className={`${inputCls} mt-1 min-h-[40px] text-xs`} placeholder="Anti-signals (one per line)" value={arrToLines(d.anti_signals)} onChange={(e) => setDim(i, { anti_signals: linesToArr(e.target.value) })} />
              </div>
            ))}
            <button onClick={addDim} className="flex items-center gap-1 text-[11px] text-brand-green hover:underline">
              <Plus className="h-3 w-3" /> add dimension
            </button>
          </div>
        </div>

        {/* Assignment */}
        <div className="rounded-md border border-border/50 p-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.assignment?.enabled !== false} onChange={(e) => patch({ assignment: { ...draft.assignment, enabled: e.target.checked } })} />
            Take-home assignment
          </label>
          {draft.assignment?.enabled !== false && (
            <div className="mt-2 grid grid-cols-3 gap-2">
              <div>
                <label className={labelCls}>Problems</label>
                <input type="number" className={inputCls} value={draft.assignment?.n_problems ?? 2} onChange={(e) => patch({ assignment: { ...draft.assignment, n_problems: Number(e.target.value) } })} />
              </div>
              <div>
                <label className={labelCls}>Hours</label>
                <input type="number" className={inputCls} value={draft.assignment?.time_budget_hours ?? 6} onChange={(e) => patch({ assignment: { ...draft.assignment, time_budget_hours: Number(e.target.value) } })} />
              </div>
              <div>
                <label className={labelCls}>Deadline (d)</label>
                <input type="number" className={inputCls} value={draft.assignment?.deadline_days ?? 7} onChange={(e) => patch({ assignment: { ...draft.assignment, deadline_days: Number(e.target.value) } })} />
              </div>
            </div>
          )}
        </div>
      </div>

      <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-border/40 px-4 py-3">
        <span className="text-[11px] text-muted-foreground">{dirty ? "Unsaved changes" : applied ? "Applied" : "Saved"}</span>
        <div className="flex gap-2">
          <button onClick={handleSave} disabled={!dirty || saving || applied} className="rounded-md border border-border/60 px-3 py-1.5 text-sm disabled:opacity-40">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
          </button>
          <button onClick={handleApply} disabled={applying || applied} className="flex items-center gap-1 rounded-md bg-brand-green px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : applied ? <><Check className="h-4 w-4" /> Applied</> : "Apply"}
          </button>
        </div>
      </footer>
    </aside>
  );
}
