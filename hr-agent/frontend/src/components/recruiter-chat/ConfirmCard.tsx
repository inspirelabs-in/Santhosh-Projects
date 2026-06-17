"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Edit3,
  Loader2,
  Plus,
  RotateCw,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarkdownLite } from "@/components/markdown-lite";
import { PipelineBuilder } from "@/components/pipeline-builder";
import { getDashboardKey } from "@/lib/auth";
import { cn } from "@/lib/utils";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

interface ConfirmCardData {
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
  preview: string;
}

/* ── Inline editable field ────────────────────────────────────────────── */

function EditableField({
  label,
  value,
  onChange,
  type = "text",
  options,
  suffix,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: "text" | "number" | "select";
  options?: { value: string; label: string }[];
  suffix?: string;
  placeholder?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | HTMLSelectElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function commit() {
    setEditing(false);
    if (draft !== value) onChange(draft);
  }

  if (!editing) {
    return (
      <div
        className="group relative cursor-pointer rounded-lg border border-transparent bg-background/70 p-2.5 transition-all hover:border-primary/30 hover:bg-primary/5"
        onClick={() => { setDraft(value); setEditing(true); }}
      >
        <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-sm font-medium">
          {value || <span className="text-muted-foreground/50">{placeholder ?? "not set"}</span>}
          {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
          <Edit3 className="ml-auto h-3 w-3 text-muted-foreground/0 transition-colors group-hover:text-primary/60" />
        </div>
      </div>
    );
  }

  if (type === "select" && options) {
    return (
      <div className="rounded-lg border border-primary/40 bg-primary/5 p-2.5">
        <div className="font-mono text-[9px] uppercase tracking-wider text-primary/70">
          {label}
        </div>
        <select
          ref={inputRef as React.RefObject<HTMLSelectElement>}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 p-2.5">
      <div className="font-mono text-[9px] uppercase tracking-wider text-primary/70">
        {label}
      </div>
      <input
        ref={inputRef as React.RefObject<HTMLInputElement>}
        type={type}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        placeholder={placeholder}
        className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
      />
    </div>
  );
}

/* ── Editable long text (markdown) ────────────────────────────────────── */

function EditableTextArea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing && taRef.current) {
      taRef.current.focus();
      taRef.current.style.height = "auto";
      taRef.current.style.height = taRef.current.scrollHeight + "px";
    }
  }, [editing]);

  function commit() {
    setEditing(false);
    if (draft !== value) onChange(draft);
  }

  if (!editing) {
    return (
      <div className="group relative">
        {label && (
          <div className="mb-1 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            {label}
          </div>
        )}
        <div
          className="relative max-h-72 cursor-pointer overflow-y-auto rounded-lg border border-border/60 bg-background p-3 text-sm transition-all scrollbar-slim hover:border-primary/30"
          onClick={() => { setDraft(value); setEditing(true); }}
        >
          <MarkdownLite source={value} />
          <div className="absolute right-2 top-2 rounded-md bg-background/90 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground/0 transition-colors group-hover:text-primary/70">
            click to edit
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      {label && (
        <div className="mb-1 font-mono text-[9px] uppercase tracking-wider text-primary/70">
          {label}
        </div>
      )}
      <textarea
        ref={taRef}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = e.target.scrollHeight + "px";
        }}
        onBlur={commit}
        className="min-h-[120px] w-full resize-y rounded-lg border border-primary/40 bg-background p-3 text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/30 scrollbar-slim"
      />
      <div className="mt-1 flex justify-end gap-1.5">
        <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        <Button size="sm" className="h-6 text-[10px]" onClick={commit}>
          Apply
        </Button>
      </div>
    </div>
  );
}

/* ── Editable problem card ────────────────────────────────────────────── */

interface Problem {
  title?: string;
  statement?: string;
  estimated_minutes?: number;
  expected_artifacts?: string[];
}

function EditableProblem({
  problem,
  index,
  onChange,
  onRemove,
}: {
  problem: Problem;
  index: number;
  onChange: (p: Problem) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(problem);

  function commit() {
    setEditing(false);
    onChange(draft);
  }

  if (!editing) {
    return (
      <div
        className="group relative cursor-pointer rounded-lg border border-border/60 bg-background/60 p-3 text-xs transition-all hover:border-primary/30 hover:bg-primary/5"
        onClick={() => { setDraft(problem); setEditing(true); }}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="font-semibold text-sm">
            {index + 1}. {problem.title || "Untitled problem"}
          </div>
          <div className="flex shrink-0 gap-1">
            <Edit3 className="h-3 w-3 text-muted-foreground/0 group-hover:text-primary/60" />
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRemove(); }}
              className="text-muted-foreground/0 group-hover:text-destructive/60 hover:text-destructive"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        </div>
        <div className="mt-1 text-foreground/80 leading-relaxed">{problem.statement}</div>
        {problem.estimated_minutes && (
          <div className="mt-1.5 text-[10px] text-muted-foreground">
            ~{problem.estimated_minutes} min
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-primary/40 bg-primary/5 p-3">
      <input
        value={draft.title || ""}
        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        placeholder="Problem title"
        className="w-full rounded border border-border bg-background px-2 py-1 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-primary/30"
        autoFocus
      />
      <textarea
        value={draft.statement || ""}
        onChange={(e) => setDraft({ ...draft, statement: e.target.value })}
        placeholder="Problem statement"
        rows={3}
        className="w-full resize-y rounded border border-border bg-background px-2 py-1.5 text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/30"
      />
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={draft.estimated_minutes || ""}
          onChange={(e) => setDraft({ ...draft, estimated_minutes: parseInt(e.target.value) || undefined })}
          placeholder="Est. minutes"
          className="w-28 rounded border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
        <span className="text-[10px] text-muted-foreground">min</span>
      </div>
      <div className="flex justify-end gap-1.5">
        <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        <Button size="sm" className="h-6 text-[10px]" onClick={commit}>
          Save
        </Button>
      </div>
    </div>
  );
}

/* ── Main ConfirmCard ─────────────────────────────────────────────────── */

export function ConfirmCard({
  data,
  conversationId,
  onResolved,
}: {
  data: ConfirmCardData;
  conversationId: string;
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"accepted" | "cancelled" | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [editedArgs, setEditedArgs] = useState<Record<string, unknown>>({ ...data.args });
  const [hasEdits, setHasEdits] = useState(false);

  const updateArg = useCallback((key: string, value: unknown) => {
    setEditedArgs((prev) => ({ ...prev, [key]: value }));
    setHasEdits(true);
  }, []);

  function resetEdits() {
    setEditedArgs({ ...data.args });
    setHasEdits(false);
  }

  async function send(accept: boolean) {
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        request_id: data.request_id,
        accept,
      };
      if (accept && hasEdits) {
        payload.edited_args = editedArgs;
      }
      await fetch(
        `${BASE}/v2/recruiter-chat/conversations/${conversationId}/confirm`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Dashboard-Key": getDashboardKey() || "",
          },
          body: JSON.stringify(payload),
        },
      );
      setDone(accept ? "accepted" : "cancelled");
      onResolved?.();
    } finally {
      setBusy(false);
    }
  }

  const args = editedArgs;
  const isCreateRole = data.tool === "create_role" || data.tool === "create_role_with_assignment";
  const isOverrideStage = data.tool === "override_stage";
  const isSendEmail = data.tool === "send_custom_email";
  const isPublishLi = data.tool === "publish_linkedin_post";
  const isSchedule = data.tool === "schedule_interview";
  const isAssignment =
    data.tool === "generate_assignment_for_role" ||
    data.tool === "set_role_assignment_brief" ||
    data.tool === "create_role_with_assignment";

  const longTextKeys = useMemo(() => {
    const keys: { key: string; label: string }[] = [];
    if (typeof args.jd_text === "string" && args.jd_text) keys.push({ key: "jd_text", label: "Job description" });
    if (typeof args.assignment_brief === "string" && args.assignment_brief) keys.push({ key: "assignment_brief", label: "Assignment brief" });
    if (typeof args.body_markdown === "string" && args.body_markdown) keys.push({ key: "body_markdown", label: "Email body" });
    if (typeof args.text === "string" && args.text) keys.push({ key: "text", label: "Post content" });
    return keys;
  }, [args]);

  /* Done state — compact badge */
  if (done) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-xl border px-4 py-3 text-xs",
          done === "accepted"
            ? "border-emerald-300/50 bg-emerald-50/60 text-emerald-700 dark:border-emerald-700/40 dark:bg-emerald-950/20 dark:text-emerald-300"
            : "border-border bg-muted/40 text-muted-foreground",
        )}
      >
        {done === "accepted" ? (
          <>
            <Check className="h-3.5 w-3.5" />
            <span className="font-medium">Confirmed{hasEdits ? " (with edits)" : ""}</span>
          </>
        ) : (
          <>
            <X className="h-3.5 w-3.5" />
            <span>Cancelled</span>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-xl border border-border/80 bg-card p-5 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <Check className="h-4 w-4 text-primary" />
          </div>
          <div>
            <div className="text-sm font-semibold text-foreground">Review before creating</div>
            <div className="text-[11px] text-muted-foreground">Click any field to edit</div>
          </div>
        </div>
        {hasEdits && (
          <button
            type="button"
            onClick={resetEdits}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <RotateCw className="h-2.5 w-2.5" />
            Reset
          </button>
        )}
      </div>

      <div className="text-sm text-foreground/70 border-b border-border/60 pb-3">{data.preview}</div>

      {/* ── Editable role fields ──────────────────────────────────── */}
      {isCreateRole && (
        <div className="space-y-3">
          {/* Role title - full width, prominent */}
          <EditableField
            label="Role title"
            value={String(args.title ?? "")}
            onChange={(v) => updateArg("title", v)}
            placeholder="e.g. Senior Backend Engineer"
          />

          {/* Compensation & Location */}
          <div className="grid grid-cols-3 gap-2">
            <EditableField
              label="Budget min"
              value={String(args.ctc_min_lpa ?? "")}
              onChange={(v) => updateArg("ctc_min_lpa", parseFloat(v) || 0)}
              type="number"
              suffix="LPA"
            />
            <EditableField
              label="Budget max"
              value={String(args.ctc_max_lpa ?? "")}
              onChange={(v) => updateArg("ctc_max_lpa", parseFloat(v) || 0)}
              type="number"
              suffix="LPA"
            />
            <EditableField
              label="Location"
              value={String(args.location ?? "")}
              onChange={(v) => updateArg("location", v)}
              placeholder="e.g. Hyderabad"
            />
          </div>

          {/* Work policy & logistics */}
          <div className="grid grid-cols-3 gap-2">
            <EditableField
              label="Work mode"
              value={String(args.remote_policy ?? "")}
              onChange={(v) => updateArg("remote_policy", v)}
              type="select"
              options={[
                { value: "hybrid", label: "Hybrid" },
                { value: "remote", label: "Remote" },
                { value: "onsite", label: "Onsite" },
              ]}
            />
            <EditableField
              label="Max notice"
              value={String(args.max_notice_days ?? "")}
              onChange={(v) => updateArg("max_notice_days", parseInt(v) || null)}
              type="number"
              suffix="days"
            />
            <EditableField
              label="Screening"
              value="voice"
              onChange={() => {}}
              type="select"
              options={[
                { value: "voice", label: "AI voice call" },
              ]}
            />
          </div>
        </div>
      )}

      {/* ── Pipeline builder (for create_role tools) ──────────────── */}
      {isCreateRole && (
        <div className="space-y-1">
          <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            Hiring pipeline
          </div>
          <PipelineBuilder
            value={Array.isArray(args.pipeline_template) ? args.pipeline_template as string[] : []}
            onChange={(steps) => updateArg("pipeline_template", steps)}
            compact
          />
        </div>
      )}

      {/* ── Override stage fields ─────────────────────────────────── */}
      {isOverrideStage && (
        <div className="grid grid-cols-2 gap-2">
          <EditableField
            label="Target stage"
            value={String(args.to_stage ?? "")}
            onChange={(v) => updateArg("to_stage", v)}
          />
          <EditableField
            label="Reason"
            value={String(args.reason ?? "")}
            onChange={(v) => updateArg("reason", v)}
          />
        </div>
      )}

      {/* ── Email fields ──────────────────────────────────────────── */}
      {isSendEmail && (
        <div className="space-y-2">
          <EditableField
            label="Subject"
            value={String(args.subject ?? "")}
            onChange={(v) => updateArg("subject", v)}
          />
        </div>
      )}

      {/* ── Schedule interview fields ─────────────────────────────── */}
      {isSchedule && (
        <div className="grid grid-cols-2 gap-2">
          <EditableField
            label="Round"
            value={String(args.round ?? "")}
            onChange={(v) => updateArg("round", v)}
          />
          <EditableField
            label="Slot"
            value={String(args.slot ?? "")}
            onChange={(v) => updateArg("slot", v)}
          />
        </div>
      )}

      {/* ── LinkedIn fields ───────────────────────────────────────── */}
      {isPublishLi && (
        <EditableField
          label="Visibility"
          value={String(args.visibility ?? "public")}
          onChange={(v) => updateArg("visibility", v)}
          type="select"
          options={[
            { value: "public", label: "Public" },
            { value: "connections", label: "Connections only" },
          ]}
        />
      )}

      {/* ── Editable long text (JD, email body, post, brief) ─────── */}
      {longTextKeys.map(({ key, label }) => (
        <EditableTextArea
          key={key}
          label={label}
          value={String(args[key])}
          onChange={(v) => updateArg(key, v)}
        />
      ))}

      {/* ── Editable problems ─────────────────────────────────────── */}
      {isAssignment && Array.isArray(args.problems) && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {(args.problems as Problem[]).length} problem{(args.problems as Problem[]).length !== 1 ? "s" : ""}
            </div>
            <button
              type="button"
              onClick={() => {
                const probs = [...(args.problems as Problem[]), { title: "", statement: "" }];
                updateArg("problems", probs);
              }}
              className="flex items-center gap-1 text-[10px] font-medium text-primary hover:underline"
            >
              <Plus className="h-2.5 w-2.5" /> Add problem
            </button>
          </div>
          {(args.problems as Problem[]).map((p, i) => (
            <EditableProblem
              key={i}
              problem={p}
              index={i}
              onChange={(updated) => {
                const probs = [...(args.problems as Problem[])];
                probs[i] = updated;
                updateArg("problems", probs);
              }}
              onRemove={() => {
                const probs = (args.problems as Problem[]).filter((_, j) => j !== i);
                updateArg("problems", probs);
              }}
            />
          ))}
        </div>
      )}

      {/* ── Raw JSON drawer ───────────────────────────────────────── */}
      <button
        onClick={() => setShowRaw((v) => !v)}
        type="button"
        className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
      >
        {showRaw ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {data.tool}
      </button>
      {showRaw && (
        <pre className="overflow-x-auto rounded-md bg-background/70 p-2 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}

      {/* ── Action bar ────────────────────────────────────────────── */}
      <div className="flex items-center justify-end gap-2 border-t border-border/60 pt-3">
        {hasEdits && (
          <span className="mr-auto text-[11px] font-medium text-primary">Changes pending</span>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => void send(false)}
          disabled={busy}
          className="h-9 px-4"
        >
          <X className="mr-1.5 h-3.5 w-3.5" />
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={() => void send(true)}
          disabled={busy}
          className="h-9 px-5"
        >
          {busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="mr-1.5 h-3.5 w-3.5" />
          )}
          {hasEdits ? "Confirm with edits" : "Looks good, create"}
        </Button>
      </div>
    </div>
  );
}
