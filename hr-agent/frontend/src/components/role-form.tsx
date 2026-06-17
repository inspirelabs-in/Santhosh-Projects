"use client";

import { useState } from "react";
import {
  FileText,
  Loader2,
  Upload,
  X,
  PhoneCall,

  Video,
  CalendarClock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Role } from "@/lib/types";
import { PanelMemberPicker } from "@/components/panel-member-picker";

type RoleWritable = Omit<Role, "id" | "created_at"> & { id?: string };

// Agentic settings live under role.scoring_rubric.agentic so we don't need a
// schema migration to ship them. Backend reads them off the same JSONB.
type AgenticConfig = {
  voice_screening_enabled: boolean;
  meeting_bot_enabled: boolean;
  technical_rubric: string; // free-text rubric the LLM scores against
};

const DEFAULT_AGENTIC: AgenticConfig = {
  voice_screening_enabled: false,
  meeting_bot_enabled: false,
  technical_rubric: "",
};

function readAgentic(rubric: Record<string, unknown> | null | undefined): AgenticConfig {
  const a = (rubric as any)?.agentic;
  if (!a) return { ...DEFAULT_AGENTIC };
  return {
    voice_screening_enabled: !!a.voice_screening_enabled,
    meeting_bot_enabled: !!a.meeting_bot_enabled,
    technical_rubric: typeof a.technical_rubric === "string" ? a.technical_rubric : "",
  };
}

// Scheduling: persisted under role.scoring_rubric.scheduling so the agent
// can find slots + book Teams meetings without HR picking the time manually.
type RoundKey = "technical" | "ceo" | "hr";
type AvailabilityWindow = {
  days: number[]; // 0=Mon..6=Sun
  start_hhmm: string;
  end_hhmm: string;
};
type RoundScheduling = {
  panel_emails: string[];
  duration_minutes: number;
  windows: AvailabilityWindow[];
};
type SchedulingConfig = {
  enabled: boolean;
  panel_timezone: string;
  rounds: Record<RoundKey, RoundScheduling>;
  horizon_business_days: number;
  min_lead_hours: number;
};

const DEFAULT_WINDOW: AvailabilityWindow = {
  days: [0, 1, 2, 3, 4],
  start_hhmm: "14:00",
  end_hhmm: "18:00",
};

const DEFAULT_SCHEDULING: SchedulingConfig = {
  enabled: false,
  panel_timezone: "Asia/Kolkata",
  rounds: {
    technical: { panel_emails: [], duration_minutes: 60, windows: [DEFAULT_WINDOW] },
    ceo: { panel_emails: [], duration_minutes: 30, windows: [DEFAULT_WINDOW] },
    hr: { panel_emails: [], duration_minutes: 30, windows: [DEFAULT_WINDOW] },
  },
  horizon_business_days: 10,
  min_lead_hours: 18,
};

function sanitizeWindow(w: any): AvailabilityWindow {
  return {
    days: Array.isArray(w?.days)
      ? (w.days.filter((n: any) => Number.isInteger(n) && n >= 0 && n <= 6) as number[])
      : [...DEFAULT_WINDOW.days],
    start_hhmm: typeof w?.start_hhmm === "string" ? w.start_hhmm : DEFAULT_WINDOW.start_hhmm,
    end_hhmm: typeof w?.end_hhmm === "string" ? w.end_hhmm : DEFAULT_WINDOW.end_hhmm,
  };
}

function sanitizeRound(r: any, fallback: RoundScheduling): RoundScheduling {
  const windowsRaw = Array.isArray(r?.windows) ? r.windows : null;
  const windows =
    windowsRaw && windowsRaw.length > 0
      ? windowsRaw.map(sanitizeWindow)
      : [...fallback.windows];
  return {
    panel_emails: Array.isArray(r?.panel_emails)
      ? r.panel_emails.filter((e: any) => typeof e === "string")
      : [],
    duration_minutes:
      typeof r?.duration_minutes === "number" && r.duration_minutes > 0
        ? r.duration_minutes
        : fallback.duration_minutes,
    windows,
  };
}

function readScheduling(
  rubric: Record<string, unknown> | null | undefined,
): SchedulingConfig {
  const s = (rubric as any)?.scheduling;
  if (!s) return JSON.parse(JSON.stringify(DEFAULT_SCHEDULING)) as SchedulingConfig;
  return {
    enabled: !!s.enabled,
    panel_timezone: s.panel_timezone || "Asia/Kolkata",
    rounds: {
      technical: sanitizeRound(s.rounds?.technical, DEFAULT_SCHEDULING.rounds.technical),
      ceo: sanitizeRound(s.rounds?.ceo, DEFAULT_SCHEDULING.rounds.ceo),
      hr: sanitizeRound(s.rounds?.hr, DEFAULT_SCHEDULING.rounds.hr),
    },
    horizon_business_days: s.horizon_business_days ?? 10,
    min_lead_hours: s.min_lead_hours ?? 18,
  };
}

export function RoleForm({
  initial,
  onSubmit,
  submitLabel = "Save role",
}: {
  initial: RoleWritable;
  onSubmit: (role: RoleWritable, problemDoc: File | null) => Promise<void> | void;
  submitLabel?: string;
}) {
  const [role, setRole] = useState<RoleWritable>(initial);
  const [agentic, setAgentic] = useState<AgenticConfig>(
    readAgentic(initial.scoring_rubric as Record<string, unknown> | undefined),
  );
  const [scheduling, setScheduling] = useState<SchedulingConfig>(
    readScheduling(initial.scoring_rubric as Record<string, unknown> | undefined),
  );
  const [problemDoc, setProblemDoc] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof RoleWritable>(k: K, v: RoleWritable[K]) {
    setRole((r) => ({ ...r, [k]: v }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      // V1: screening questions + interviewer panel are not used.
      // V2 agentic settings ride along inside scoring_rubric.agentic so we
      // can persist them without a schema migration.
      const rubric = {
        ...((role.scoring_rubric as Record<string, unknown>) ?? {}),
        agentic,
        scheduling,
      };
      await onSubmit(
        {
          ...role,
          screening_questions: [],
          interviewer_panel: [],
          scoring_rubric: rubric,
        },
        problemDoc,
      );
    } catch (e: any) {
      setErr(e?.message ?? "Failed to save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 pb-8">
      <Card>
        <CardHeader>
          <CardTitle className="font-display text-2xl">Basics</CardTitle>
          <CardDescription>
            Title + job description. The JD goes to the LLM verbatim when
            generating screening questions for each candidate.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-1.5 md:col-span-2">
            <Label>Title</Label>
            <Input
              value={role.title}
              onChange={(e) => set("title", e.target.value)}
              placeholder="Senior Backend Engineer"
            />
          </div>
          <div className="space-y-1.5 md:col-span-2">
            <Label>Job description</Label>
            <Textarea
              rows={10}
              value={role.jd_text}
              onChange={(e) => set("jd_text", e.target.value)}
              placeholder="Paste the full JD. Includes responsibilities, must-have skills, nice-to-haves, seniority."
            />
          </div>
          <div className="space-y-1.5">
            <Label>Status</Label>
            <Select value={role.status} onValueChange={(v) => set("status", v as Role["status"])}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">Open</SelectItem>
                <SelectItem value="paused">Paused</SelectItem>
                <SelectItem value="filled">Filled</SelectItem>
                <SelectItem value="cancelled">Cancelled</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="font-display text-2xl">Problem statement document</CardTitle>
          <CardDescription>
            Word document or PDF with the full problem statement. Sent to
            candidates with the assignment email. Optional — the brief below is
            enough on its own.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {initial.id && !problemDoc && (role as any).has_problem_doc && (
            <div className="mb-3 flex items-center justify-between rounded-md border border-border bg-background p-3">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-muted-foreground" />
                <span className="font-mono text-sm">
                  {(role as any).assignment_problem_doc_filename ?? "problem-statement"}
                </span>
                <span className="font-mono text-[11px] text-success">uploaded</span>
              </div>
              <span className="font-mono text-[11px] text-muted-foreground">
                upload new to replace
              </span>
            </div>
          )}
          {problemDoc ? (
            <div className="flex items-center justify-between rounded-md border border-primary/30 bg-primary/5 p-3">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-primary" />
                <span className="font-mono text-sm">{problemDoc.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {(problemDoc.size / 1024).toFixed(1)} KB
                </span>
              </div>
              <button
                type="button"
                onClick={() => setProblemDoc(null)}
                className="text-muted-foreground hover:text-destructive"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-4 py-2 text-sm transition hover:bg-background">
              <Upload className="h-3.5 w-3.5" />
              {initial.id && (role as any).has_problem_doc
                ? "Replace document"
                : "Upload .docx / .pdf"}
              <input
                type="file"
                accept=".doc,.docx,.pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) setProblemDoc(f);
                  e.target.value = "";
                }}
              />
            </label>
          )}
        </CardContent>
      </Card>

      <Card className="border-primary/30 ring-1 ring-primary/10">
        <CardHeader>
          <CardTitle className="font-display text-2xl">Assignment</CardTitle>
          <CardDescription>
            Sent automatically to candidates who clear the screening stage.
            <strong className="text-foreground"> Required.</strong> Leave empty and every
            clear-pass will land in <code className="font-mono text-xs">needs_hr_review</code>
            for you to send manually.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <div className="space-y-1.5 md:col-span-4">
            <Label>Brief (what the candidate must do)</Label>
            <Textarea
              rows={6}
              value={role.assignment_brief ?? ""}
              onChange={(e) => set("assignment_brief", e.target.value || null)}
              placeholder={
                "Design a 30-minute onboarding flow for a new merchant in a coupon marketplace. Deliver a one-page writeup and a Figma or whiteboard sketch."
              }
            />
          </div>
          <div className="space-y-1.5 md:col-span-4">
            <Label>Instructions (format & constraints)</Label>
            <Textarea
              rows={4}
              value={role.assignment_instructions ?? ""}
              onChange={(e) => set("assignment_instructions", e.target.value || null)}
              placeholder={
                "Max 2 pages. Any format: PDF, Figma link, Loom walkthrough, GitHub repo. Include one assumption you had to make."
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Deadline (days)</Label>
            <Input
              type="number"
              min={1}
              max={60}
              value={role.assignment_deadline_days ?? 7}
              onChange={(e) =>
                set("assignment_deadline_days", Number(e.target.value))
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="font-display text-2xl">Constraints</CardTitle>
          <CardDescription>
            Given to the screening-eval LLM as logistics knock-outs. CTC, notice, location.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <Label>CTC min (LPA)</Label>
            <Input
              type="number"
              step="0.5"
              value={role.ctc_min_lpa ?? ""}
              onChange={(e) =>
                set("ctc_min_lpa", e.target.value === "" ? null : Number(e.target.value))
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>CTC max (LPA)</Label>
            <Input
              type="number"
              step="0.5"
              value={role.ctc_max_lpa ?? ""}
              onChange={(e) =>
                set("ctc_max_lpa", e.target.value === "" ? null : Number(e.target.value))
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Max notice period (days)</Label>
            <Input
              type="number"
              value={role.max_notice_days ?? ""}
              onChange={(e) =>
                set("max_notice_days", e.target.value === "" ? null : Number(e.target.value))
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Location</Label>
            <Input
              value={role.location ?? ""}
              onChange={(e) => set("location", e.target.value || null)}
              placeholder="Hyderabad"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Remote policy</Label>
            <Select
              value={role.remote_policy ?? ""}
              onValueChange={(v) =>
                set("remote_policy", (v || null) as Role["remote_policy"])
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="—" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="onsite">On-site</SelectItem>
                <SelectItem value="hybrid">Hybrid</SelectItem>
                <SelectItem value="remote">Remote</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card className="border-primary/30 bg-primary/[0.02]">
        <CardHeader>
          <CardTitle className="font-display text-2xl">Agentic rounds</CardTitle>
          <CardDescription>
            Choose which AI-powered rounds run for this role. All flags require
            the corresponding backend feature flag to be on
            (<code className="font-mono text-xs">ENABLE_VOICE_SCREENING</code> etc.).
            Settings are saved into <code className="font-mono text-xs">scoring_rubric.agentic</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Voice screening */}
          <ToggleRow
            icon={PhoneCall}
            title="Outbound AI phone screen"
            desc="Agent calls the candidate, asks tailored spoken questions, evaluates the answers post-call."
            checked={agentic.voice_screening_enabled}
            onChange={(v) =>
              setAgentic((a) => ({ ...a, voice_screening_enabled: v }))
            }
          />

          {/* Meeting bot */}
          <ToggleRow
            icon={Video}
            title="Teams meeting analysis"
            desc="AI bot joins technical + CEO interviews, transcribes, scores tone, confidence, and technical depth."
            checked={agentic.meeting_bot_enabled}
            onChange={(v) =>
              setAgentic((a) => ({ ...a, meeting_bot_enabled: v }))
            }
          />

          {/* Rubric */}
          <div className="space-y-1.5">
            <Label>Technical rubric (used for meeting scoring)</Label>
            <Textarea
              rows={5}
              value={agentic.technical_rubric}
              onChange={(e) =>
                setAgentic((a) => ({ ...a, technical_rubric: e.target.value }))
              }
              placeholder="Bullet what 'good' looks like. e.g.:
• Reads ambiguous requirements and asks clarifying questions before coding
• Articulates trade-offs (latency vs cost vs complexity)
• Writes correct code under pressure with reasonable test coverage"
            />
            <p className="text-[11px] text-muted-foreground">
              Sent verbatim to the LLM that scores the technical interview transcript.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card className="border-secondary/30 bg-secondary/[0.02]">
        <CardHeader>
          <CardTitle className="font-display text-2xl flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-secondary" />
            Auto-scheduling
          </CardTitle>
          <CardDescription>
            With this on, the agent picks a slot, books a Teams meeting, calls
            the candidate to confirm, and emails everyone. Works for technical,
            CEO, and HR discussions. Saved into{" "}
            <code className="font-mono text-xs">scoring_rubric.scheduling</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <ToggleRow
            icon={CalendarClock}
            title="Enable auto-scheduling"
            desc="Required to use the 'Schedule technical / CEO / HR' buttons on the candidate page."
            checked={scheduling.enabled}
            onChange={(v) => setScheduling((s) => ({ ...s, enabled: v }))}
          />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Panel timezone</Label>
              <Input
                value={scheduling.panel_timezone}
                onChange={(e) =>
                  setScheduling((s) => ({ ...s, panel_timezone: e.target.value }))
                }
                placeholder="Asia/Kolkata"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Search horizon (business days)</Label>
              <Input
                type="number"
                min={1}
                max={30}
                value={scheduling.horizon_business_days}
                onChange={(e) =>
                  setScheduling((s) => ({
                    ...s,
                    horizon_business_days: Number(e.target.value),
                  }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label>Minimum lead time (hours)</Label>
              <Input
                type="number"
                min={1}
                max={168}
                value={scheduling.min_lead_hours}
                onChange={(e) =>
                  setScheduling((s) => ({
                    ...s,
                    min_lead_hours: Number(e.target.value),
                  }))
                }
              />
            </div>
          </div>

          {(["technical", "ceo", "hr"] as RoundKey[]).map((round) => (
            <RoundConfigBlock
              key={round}
              round={round}
              cfg={scheduling.rounds[round]}
              onChange={(next) =>
                setScheduling((s) => ({
                  ...s,
                  rounds: { ...s.rounds, [round]: next },
                }))
              }
            />
          ))}
        </CardContent>
      </Card>

      {err ? <p className="text-sm text-destructive">{err}</p> : null}
      <div className="flex items-center justify-end gap-2 pt-2">
        <Button
          type="submit"
          disabled={busy || !role.title.trim() || !role.jd_text.trim()}
          size="lg"
          className="px-8"
        >
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

function RoundConfigBlock({
  round,
  cfg,
  onChange,
}: {
  round: RoundKey;
  cfg: RoundScheduling;
  onChange: (next: RoundScheduling) => void;
}) {
  const label =
    round === "technical" ? "Technical" : round === "ceo" ? "CEO" : "HR";
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <p className="text-sm font-bold uppercase tracking-[0.12em] text-muted-foreground">
        {label} round
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="space-y-1.5 sm:col-span-2">
          <Label>Panel members</Label>
          <PanelMemberPicker
            roleType={round}
            value={cfg.panel_emails || []}
            onChange={(emails) => onChange({ ...cfg, panel_emails: emails })}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Duration (minutes)</Label>
          <Input
            type="number"
            min={15}
            max={240}
            value={cfg.duration_minutes}
            onChange={(e) =>
              onChange({ ...cfg, duration_minutes: Number(e.target.value) })
            }
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label>Availability windows</Label>
        {(cfg.windows || []).map((w, idx) => (
          <WindowRow
            key={idx}
            window={w}
            onRemove={() =>
              onChange({
                ...cfg,
                windows: cfg.windows.filter((_, i) => i !== idx),
              })
            }
            onChange={(next) =>
              onChange({
                ...cfg,
                windows: cfg.windows.map((wi, i) => (i === idx ? next : wi)),
              })
            }
          />
        ))}
        <button
          type="button"
          onClick={() => onChange({ ...cfg, windows: [...cfg.windows, DEFAULT_WINDOW] })}
          className="rounded-md border border-dashed border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          + Add window
        </button>
      </div>
    </div>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function WindowRow({
  window,
  onChange,
  onRemove,
}: {
  window: AvailabilityWindow;
  onChange: (next: AvailabilityWindow) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
      <div className="flex flex-wrap gap-1">
        {WEEKDAYS.map((label, idx) => {
          const days = Array.isArray(window.days) ? window.days : [];
          const on = days.includes(idx);
          return (
            <button
              key={label}
              type="button"
              onClick={() =>
                onChange({
                  ...window,
                  days: on
                    ? days.filter((d) => d !== idx)
                    : [...days, idx].sort((a, b) => a - b),
                })
              }
              className={
                "rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.12em] transition " +
                (on
                  ? "bg-primary/10 text-primary"
                  : "bg-muted text-muted-foreground hover:text-foreground")
              }
            >
              {label}
            </button>
          );
        })}
      </div>
      <Input
        type="time"
        value={window.start_hhmm}
        onChange={(e) => onChange({ ...window, start_hhmm: e.target.value })}
        className="h-8 w-28"
      />
      <span className="text-xs text-muted-foreground">to</span>
      <Input
        type="time"
        value={window.end_hhmm}
        onChange={(e) => onChange({ ...window, end_hhmm: e.target.value })}
        className="h-8 w-28"
      />
      <button
        type="button"
        onClick={onRemove}
        className="ml-auto text-muted-foreground hover:text-destructive"
        aria-label="Remove window"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function ToggleRow({
  icon: Icon,
  title,
  desc,
  checked,
  onChange,
}: {
  icon: typeof PhoneCall;
  title: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label
      className={
        "flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition " +
        (checked
          ? "border-primary/50 bg-primary/5"
          : "border-border bg-card hover:border-primary/30")
      }
    >
      <Icon className={"mt-0.5 h-4 w-4 " + (checked ? "text-primary" : "text-muted-foreground")} />
      <div className="flex-1">
        <p className="text-sm font-semibold">{title}</p>
        <p className="text-xs text-muted-foreground">{desc}</p>
      </div>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 h-4 w-4 cursor-pointer accent-primary"
      />
    </label>
  );
}
