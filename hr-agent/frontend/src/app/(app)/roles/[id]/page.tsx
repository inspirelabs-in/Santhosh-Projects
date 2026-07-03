"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import useSWR from "swr";
import {
  ArrowLeft,
  Check,
  Download,
  Eye,
  FileText,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  Save,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { cn, fmtDate } from "@/lib/utils";
import { api, swrFetcher } from "@/lib/api";
import { Topbar } from "@/components/layout/topbar";
import { SectionRail, type SectionRailItem } from "@/components/layout/section-rail";
import { MarkdownLite } from "@/components/markdown-lite";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Role, RolePipelineStageEntry } from "@/lib/types";

const STATUS_VARIANT: Record<string, "success" | "warning" | "muted" | "destructive"> = {
  open: "success",
  paused: "warning",
  filled: "muted",
  cancelled: "destructive",
};

const STAGE_LABELS: Record<string, string> = {
  intake: "Intake",
  parse: "Resume Parse",
  fit_score: "Fit Score",
  fit: "Fit Score",
  screening: "Screening",
  voice_screen: "Voice Screen",
  assignment: "Assignment",
  tech_interview: "Technical Interview",
  ceo_interview: "CEO Interview",
  interview: "Interview",
  decision: "Decision",
  offer: "Offer",
  hired: "Hired",
  rejected: "Rejected",
};

const humanizeStage = (key: string): string =>
  STAGE_LABELS[key] ??
  key
    .split("_")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");

const TABS = ["Overview", "Job Description", "Evaluation", "Pipeline", "Assignment"] as const;
type Tab = (typeof TABS)[number];

const RAIL_ITEMS: SectionRailItem[] = [
  { id: "Overview", label: "Overview" },
  { id: "Job Description", label: "Job Description" },
  { id: "Evaluation", label: "Evaluation" },
  { id: "Pipeline", label: "Pipeline" },
  { id: "Assignment", label: "Assignment" },
];

const SECTION_BLURB: Record<Tab, string> = {
  Overview: "Role details, compensation, and quick actions.",
  "Job Description": "Full JD shown to candidates during the process.",
  Evaluation: "Weighted rubric the AI uses to score each candidate.",
  Pipeline: "Ordered hiring flow for this role.",
  Assignment: "Take-home brief, instructions, and problem document.",
};

/* ------------------------------------------------------------------ */
/*  Inline field primitives — same style as org settings page          */
/* ------------------------------------------------------------------ */

const inlineArea =
  "w-full resize-none rounded-md bg-transparent px-3 py-2.5 text-sm leading-relaxed transition hover:bg-muted/40 focus:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 placeholder:text-muted-foreground/40";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        {label}
        {hint && (
          <span className="ml-1.5 font-mono text-[10px] normal-case tracking-normal text-muted-foreground/50">
            {hint}
          </span>
        )}
      </span>
      <div className="mt-2">{children}</div>
    </div>
  );
}

/* Thin weight bar — primary green fill over muted track */
function WeightBar({ weight }: { weight: number }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-muted">
      <div
        className="h-1.5 rounded-full bg-primary transition-all"
        style={{ width: `${Math.min(100, Math.max(0, weight))}%` }}
      />
    </div>
  );
}

/* Role pipeline as a horizontal connected-dot timeline — the same stages the
   candidate detail page renders, but as the role's definition (no per-candidate
   verdict): label + auto/manual mode per node. */
function PipelineTimeline({ stages }: { stages: RolePipelineStageEntry[] }) {
  const enabled = [...stages].filter((s) => s.is_enabled).sort((a, b) => a.position - b.position);
  if (enabled.length === 0) {
    return (
      <p className="py-4 text-sm italic text-muted-foreground">
        Using the default pipeline (no custom stages configured).
      </p>
    );
  }
  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max items-start">
        {enabled.map((s, i) => {
          const isLast = i === enabled.length - 1;
          const isAuto = s.mode === "auto";
          return (
            <div key={s.stage_key} className="flex items-start">
              <div className="flex w-28 flex-col items-center px-1 text-center">
                <div
                  className={cn(
                    "flex h-9 w-9 items-center justify-center rounded-full border-2 font-mono text-xs font-bold tabular-nums",
                    isAuto
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border bg-card text-foreground",
                  )}
                >
                  {i + 1}
                </div>
                <span className="mt-2 text-xs font-medium capitalize leading-tight">{s.label}</span>
                <span
                  className={cn(
                    "mt-1.5 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
                    isAuto
                      ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  {isAuto ? "auto" : "manual"}
                </span>
              </div>
              {!isLast && <div className="mt-[18px] h-0.5 w-6 shrink-0 rounded-full bg-border" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* One side of the eval signals ledger — ✓ good or ✗ anti */
function EvalSignalColumn({
  tone,
  items,
}: {
  tone: "good" | "bad";
  items: string[];
}) {
  const Marker = tone === "good" ? Check : X;
  const color = tone === "good" ? "text-emerald-600" : "text-red-500";
  const dot = tone === "good" ? "bg-emerald-500" : "bg-red-400";
  const title = tone === "good" ? "What good looks like" : "Anti-signals";
  return (
    <div>
      <p className={cn("mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em]", color)}>
        <Marker className="h-3.5 w-3.5" /> {title}
      </p>
      <div>
        {items.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-2.5 border-b border-border/40 py-1.5 last:border-0"
          >
            <span className={cn("h-1 w-1 shrink-0 rounded-full", dot)} />
            <span className="text-sm text-muted-foreground">{item}</span>
          </div>
        ))}
        {items.length === 0 && (
          <p className="text-xs italic text-muted-foreground/50">None defined</p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  DocViewer                                                          */
/* ------------------------------------------------------------------ */

function DocViewer({
  roleId,
  getDocUrl,
  onClose,
}: {
  roleId: string;
  getDocUrl: (inline: boolean) => string;
  onClose: () => void;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { getDashboardKey } = await import("@/lib/auth");
        const key = getDashboardKey();
        const headers: Record<string, string> = {};
        if (key) headers["X-Dashboard-Key"] = key;
        const res = await fetch(getDocUrl(true), { headers });
        if (!res.ok) throw new Error(`Failed: ${res.status}`);
        const contentType = res.headers.get("content-type") || "application/pdf";
        const arrayBuf = await res.arrayBuffer();
        if (cancelled) return;
        const blob = new Blob([arrayBuf], { type: contentType });
        setBlobUrl(URL.createObjectURL(blob));
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? "Failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [roleId, getDocUrl]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">Document preview</span>
        <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={onClose}>
          <X className="mr-1 h-3 w-3" /> Close
        </Button>
      </div>
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      ) : !blobUrl ? (
        <div className="flex items-center justify-center rounded-lg border p-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <iframe
          src={blobUrl}
          className="w-full rounded-lg border bg-white"
          style={{ height: "70vh" }}
          title="Problem document"
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export default function RoleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const {
    data: role,
    isLoading,
    mutate,
  } = useSWR<Role>(id ? `/dashboard/roles/${id}` : null, swrFetcher);

  const { data: candidateData } = useSWR<{
    items: Array<{ application_id: string; name: string; current_stage: string; fit_score: number | null; fit_tier: string | null }>;
    total: number;
  }>(id ? `/dashboard/v1/candidates?role_id=${id}&limit=500` : null, swrFetcher);


  const [activeTab, setActiveTab] = useState<Tab>("Overview");

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const [ctcMin, setCtcMin] = useState<string>("");
  const [ctcMax, setCtcMax] = useState<string>("");
  const [location, setLocation] = useState("");
  const [remotePolicy, setRemotePolicy] = useState<string>("");
  const [noticeCap, setNoticeCap] = useState<string>("");
  const [assignmentBrief, setAssignmentBrief] = useState("");
  const [assignmentInstructions, setAssignmentInstructions] = useState("");
  const [assignmentDeadlineDays, setAssignmentDeadlineDays] = useState<string>("");
  const [piCognitiveLink, setPiCognitiveLink] = useState("");
  const [piPersonalityLink, setPiPersonalityLink] = useState("");
  const [jdText, setJdText] = useState("");
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [jdDirty, setJdDirty] = useState(false);
  const [editingJd, setEditingJd] = useState(false);
  const [assignmentDirty, setAssignmentDirty] = useState(false);

  const [editingEvalSpec, setEditingEvalSpec] = useState(false);
  const [evalSpecDims, setEvalSpecDims] = useState<
    {
      key: string;
      label: string;
      weight: number;
      what_good_looks_like: string[];
      anti_signals: string[];
    }[]
  >([]);
  const [evalSpecKnockouts, setEvalSpecKnockouts] = useState<{ key: string; rule: string }[]>([]);
  const [evalDirty, setEvalDirty] = useState(false);

  const [editingCtx, setEditingCtx] = useState(false);
  const [ctxDraft, setCtxDraft] = useState<{
    summary: string;
    hiring_bar: string;
    what_matters_here: string[];
  }>({ summary: "", hiring_bar: "", what_matters_here: [] });
  const [ctxDirty, setCtxDirty] = useState(false);

  useEffect(() => {
    if (!role) return;
    setTitleDraft(role.title);
    setCtcMin(role.ctc_min_lpa != null ? String(role.ctc_min_lpa) : "");
    setCtcMax(role.ctc_max_lpa != null ? String(role.ctc_max_lpa) : "");
    setLocation(role.location ?? "");
    setRemotePolicy(role.remote_policy ?? "");
    setNoticeCap(role.max_notice_days != null ? String(role.max_notice_days) : "");
    setAssignmentBrief(role.assignment_brief ?? "");
    setAssignmentInstructions((role as any).assignment_instructions ?? "");
    setAssignmentDeadlineDays((role as any).assignment_deadline_days != null ? String((role as any).assignment_deadline_days) : "");
    setPiCognitiveLink(role.pi_cognitive_link ?? "");
    setPiPersonalityLink(role.pi_personality_link ?? "");
    setJdText(role.jd_text ?? "");

    const spec = role.evaluation_spec as Record<string, unknown> | undefined;
    if (spec?.dimensions) {
      setEvalSpecDims(spec.dimensions as any[]);
      setEvalSpecKnockouts((spec.knockouts ?? []) as any[]);
    }
    const ctx = role.company_context as Record<string, unknown> | undefined;
    if (ctx) {
      setCtxDraft({
        summary: (ctx.summary as string) ?? "",
        hiring_bar: (ctx.hiring_bar as string) ?? "",
        what_matters_here: (ctx.what_matters_here as string[]) ?? [],
      });
    }
  }, [role]);

  const flash = (type: "ok" | "err", text: string) => {
    setSaveMsg({ type, text });
    setTimeout(() => setSaveMsg(null), 3000);
  };

  const saveTitle = async () => {
    if (!id || !titleDraft.trim()) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, { title: titleDraft.trim() });
      setEditingTitle(false);
      await mutate();
      flash("ok", "Title updated");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to update title");
    } finally {
      setSaving(false);
    }
  };

  const saveJd = async () => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, { jd_text: jdText });
      setJdDirty(false);
      setEditingJd(false);
      await mutate();
      flash("ok", "Job description saved");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to save JD");
    } finally {
      setSaving(false);
    }
  };

  const saveDetails = async () => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, {
        ctc_min_lpa: ctcMin ? Number(ctcMin) : null,
        ctc_max_lpa: ctcMax ? Number(ctcMax) : null,
        location: location || null,
        remote_policy: remotePolicy || null,
        max_notice_days: noticeCap ? Number(noticeCap) : null,
      });
      setDetailsDirty(false);
      await mutate();
      flash("ok", "Details saved");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to save details");
    } finally {
      setSaving(false);
    }
  };

  const saveAssignment = async () => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, {
        assignment_brief: assignmentBrief || null,
        assignment_instructions: assignmentInstructions || null,
        assignment_deadline_days: assignmentDeadlineDays ? Number(assignmentDeadlineDays) : null,
        pi_cognitive_link: piCognitiveLink || null,
        pi_personality_link: piPersonalityLink || null,
      });
      setAssignmentDirty(false);
      await mutate();
      flash("ok", "Assignment saved");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to save assignment");
    } finally {
      setSaving(false);
    }
  };

  const saveEvalSpec = async () => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, {
        evaluation_spec: { dimensions: evalSpecDims, knockouts: evalSpecKnockouts },
      });
      setEditingEvalSpec(false);
      setEvalDirty(false);
      await mutate();
      flash("ok", "Evaluation criteria saved");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to save evaluation criteria");
    } finally {
      setSaving(false);
    }
  };

  const addDim = () => {
    setEvalSpecDims((prev) => [
      ...prev,
      {
        key: `dim_${Date.now()}`,
        label: "New dimension",
        weight: 10,
        what_good_looks_like: [""],
        anti_signals: [""],
      },
    ]);
    setEvalDirty(true);
  };

  const updateDim = (i: number, field: string, value: unknown) => {
    setEvalSpecDims((prev) => prev.map((d, idx) => (idx === i ? { ...d, [field]: value } : d)));
    setEvalDirty(true);
  };

  const removeDim = (i: number) => {
    setEvalSpecDims((prev) => prev.filter((_, idx) => idx !== i));
    setEvalDirty(true);
  };

  const saveCtx = async () => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, { company_context: ctxDraft });
      setEditingCtx(false);
      setCtxDirty(false);
      await mutate();
      flash("ok", "Company context saved");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to save company context");
    } finally {
      setSaving(false);
    }
  };

  const changeStatus = async (status: string) => {
    if (!id) return;
    setSaving(true);
    try {
      await api.patch(`/dashboard/roles/${id}`, { status });
      await mutate();
      flash("ok", `Role ${status}`);
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to update status");
    } finally {
      setSaving(false);
    }
  };

  const [publishing, setPublishing] = useState(false);
  const publishAssignment = async () => {
    if (!id) return;
    setPublishing(true);
    try {
      await api.post(`/dashboard/roles/${id}/assignment/publish`, {});
      await mutate();
      flash("ok", "Assignment published — role is live");
    } catch (e: any) {
      flash("err", e?.message ?? "Failed to publish assignment");
    } finally {
      setPublishing(false);
    }
  };

  const [downloading, setDownloading] = useState(false);
  const [showDocViewer, setShowDocViewer] = useState(false);

  const getDocUrl = (inline: boolean) => {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
    return `${base}/dashboard/roles/${id}/problem-doc/download${inline ? "?inline=true" : ""}`;
  };

  const downloadProblemDoc = async () => {
    if (!id) return;
    setDownloading(true);
    try {
      const { getDashboardKey } = await import("@/lib/auth");
      const key = getDashboardKey();
      const headers: Record<string, string> = {};
      if (key) headers["X-Dashboard-Key"] = key;
      const res = await fetch(getDocUrl(false), { headers });
      if (!res.ok) throw new Error(`Download failed: ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = role?.assignment_problem_doc_filename ?? "problem-statement.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      flash("err", e?.message ?? "Download failed");
    } finally {
      setDownloading(false);
    }
  };

  const breadcrumbOverrides: Record<string, string> = {};
  if (id) breadcrumbOverrides[id] = role?.title ?? "...";

  if (!id) return null;

  return (
    <div className="flex h-full flex-col">
      <Topbar
        title={role?.title ?? "Role"}
        subtitle="Role configuration"
        breadcrumbOverrides={breadcrumbOverrides}
      />

      <div className="scrollbar-slim min-h-0 flex-1 overflow-y-auto">
        {/* ── Slim sticky bar — breadcrumb + status + actions ── */}
        <div className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur-sm">
          <div className="mx-auto flex max-w-5xl items-center gap-2.5 px-6 py-2.5">
            <button
              onClick={() => router.back()}
              className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-3 w-3" />
              Roles
            </button>
            <div className="h-3.5 w-px shrink-0 bg-border" />
            <span className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
              {role?.title ?? "…"}
            </span>
            {role && (
              <span className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
                role.status === "open"      && "border-emerald-400 bg-emerald-50 text-emerald-700",
                role.status === "paused"    && "border-amber-400 bg-amber-50 text-amber-700",
                role.status === "filled"    && "border-border bg-muted text-muted-foreground",
                role.status === "cancelled" && "border-red-300 bg-red-50 text-red-600",
              )}>
                <span className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  role.status === "open"      && "bg-emerald-500",
                  role.status === "paused"    && "bg-amber-400",
                  role.status === "filled"    && "bg-muted-foreground",
                  role.status === "cancelled" && "bg-red-400",
                )} />
                {role.status === "open" ? "Active – Open" : role.status}
              </span>
            )}
            {role && (
              <div className="flex shrink-0 items-center gap-1.5">
                {(role.status as string) === "draft" && !!role.assignment_brief && !role.has_problem_doc && (
                  <Button size="sm" className="h-7 text-xs" onClick={publishAssignment} disabled={publishing || saving}>
                    {publishing ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <FileText className="mr-1.5 h-3 w-3" />}
                    Publish
                  </Button>
                )}
                {role.status === "open" && (
                  <Button variant="outline" size="sm"
                    className="h-8 rounded-lg border-amber-300 bg-amber-50 px-4 text-xs font-semibold tracking-wide text-amber-800 shadow-sm hover:border-amber-400 hover:bg-amber-100 hover:text-amber-900"
                    onClick={() => changeStatus("paused")} disabled={saving}>
                    Pause
                  </Button>
                )}
                {role.status === "paused" && (
                  <Button variant="outline" size="sm"
                    className="h-8 rounded-lg border-emerald-300 bg-emerald-50 px-4 text-xs font-semibold tracking-wide text-emerald-800 shadow-sm hover:border-emerald-400 hover:bg-emerald-100 hover:text-emerald-900"
                    onClick={() => changeStatus("open")} disabled={saving}>
                    Reopen
                  </Button>
                )}
                {(role.status === "open" || role.status === "paused") && (
                  <Button variant="outline" size="sm"
                    className="h-8 rounded-lg border-red-300 bg-red-50 px-4 text-xs font-semibold tracking-wide text-red-700 shadow-sm hover:border-red-400 hover:bg-red-100 hover:text-red-900"
                    onClick={() => changeStatus("cancelled")} disabled={saving}>
                    Close Pipeline
                  </Button>
                )}
              </div>
            )}
            {saveMsg && (
              <span className={cn(
                "shrink-0 rounded px-2 py-0.5 text-xs font-medium",
                saveMsg.type === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700",
              )}>
                {saveMsg.text}
              </span>
            )}
          </div>
        </div>

        {/* ── Hero — big title + subtitle + edit ── */}
        <div className="border-b border-border bg-gradient-to-b from-muted/30 to-transparent">
          <div className="mx-auto max-w-5xl px-6 py-6">
            {isLoading ? (
              <div className="space-y-2.5">
                <div className="h-8 w-72 animate-pulse rounded-lg bg-muted" />
                <div className="h-4 w-48 animate-pulse rounded-md bg-muted/70" />
              </div>
            ) : role ? (
              <>
                {editingTitle ? (
                  <div className="flex items-center gap-2">
                    <Input
                      value={titleDraft}
                      onChange={(e) => setTitleDraft(e.target.value)}
                      className="h-10 max-w-md text-xl font-bold"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveTitle();
                        if (e.key === "Escape") { setEditingTitle(false); setTitleDraft(role.title); }
                      }}
                    />
                    <Button size="sm" onClick={saveTitle} disabled={saving || !titleDraft.trim()}>
                      <Check className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => { setEditingTitle(false); setTitleDraft(role.title); }}>
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2.5">
                    <h1 className="text-2xl font-bold tracking-tight text-foreground">{role.title}</h1>
                    <button
                      onClick={() => setEditingTitle(true)}
                      className="shrink-0 rounded-md p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </div>
                )}
                <p className="mt-1.5 text-sm text-muted-foreground">
                  Configure evaluation parameters, compensation tracks, and active pipelines.
                </p>
              </>
            ) : null}
          </div>
        </div>

        {/* ── Top tab bar ── */}
        {role && (
          <div className="border-b border-border bg-background">
            <div className="mx-auto max-w-5xl px-6">
              <div className="flex items-center gap-1.5 py-2.5">
                {TABS.map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      "rounded-lg border px-3.5 py-1.5 text-sm font-medium transition-colors",
                      activeTab === tab
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/50 hover:text-foreground",
                    )}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Content area ── */}
        <div className="mx-auto max-w-5xl px-6 py-8 pb-20">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : !role ? (
            <div className="py-20 text-center">
              <XCircle className="mx-auto h-10 w-10 text-muted-foreground/50" />
              <p className="mt-4 text-sm text-muted-foreground">Role not found or could not be loaded.</p>
              <Button variant="outline" size="sm" className="mt-4" onClick={() => router.push("/roles")}>
                Back to roles
              </Button>
            </div>
          ) : (
            <div className="min-w-0">
                {/* Section blurb */}
                <p className="mb-6 text-sm text-muted-foreground">{SECTION_BLURB[activeTab]}</p>

                {/* ══════════════════════════════════════════ */}
                {/* Section: Overview                         */}
                {/* ══════════════════════════════════════════ */}
                {activeTab === "Overview" && (
                  <div className="max-w-2xl space-y-5">
                    {/* Candidate pipeline stats card */}
                    {candidateData && candidateData.total > 0 && (() => {
                      const items = candidateData.items;
                      const active = items.filter(c => c.current_stage !== "rejected").length;
                      const rejected = items.filter(c => c.current_stage === "rejected").length;
                      const stageCounts: Record<string, number> = {};
                      for (const c of items) {
                        stageCounts[c.current_stage] = (stageCounts[c.current_stage] ?? 0) + 1;
                      }
                      const STAGE_COLOR: Record<string, string> = {
                        rejected: "bg-red-400", needs_hr_review: "bg-amber-400", hired: "bg-emerald-600",
                        offer: "bg-emerald-400", assignment_sent: "bg-blue-400", voice_screen: "bg-violet-400",
                        voice_screen_scheduled: "bg-violet-400", tech_interview: "bg-indigo-400",
                        screening: "bg-sky-400", intake: "bg-slate-300",
                      };
                      const STAGE_LABEL_MAP: Record<string, string> = {
                        rejected: "Rejected", needs_hr_review: "HR Review", hired: "Hired", offer: "Offer",
                        assignment_sent: "Assignment", voice_screen: "Voice", voice_screen_scheduled: "Voice",
                        tech_interview: "Interview", screening: "Screening", intake: "Intake",
                      };
                      const sorted = Object.entries(stageCounts).sort((a, b) => b[1] - a[1]);
                      return (
                        <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
                          <div className="flex items-center justify-between mb-3">
                            <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Candidates</p>
                            <Link href={`/candidates?role_id=${id}`} className="text-[11px] text-primary hover:underline font-medium">View all →</Link>
                          </div>
                          {/* Stats row — always one line */}
                          <div className="flex items-baseline gap-5 mb-3">
                            <div>
                              <span className="text-3xl font-bold tabular-nums">{candidateData.total}</span>
                              <span className="ml-1 text-xs text-muted-foreground">total</span>
                            </div>
                            <span className="text-emerald-600 font-semibold tabular-nums text-sm">{active} active</span>
                            {rejected > 0 && (
                              <span className="text-red-500 font-semibold tabular-nums text-sm">{rejected} rejected</span>
                            )}
                          </div>
                          {/* Pipeline strip — horizontal scroll, never wraps */}
                          <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-none">
                            {sorted.map(([stage, count]) => (
                              <span key={stage} className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/60 bg-muted/30 px-2.5 py-0.5 text-[11px]">
                                <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", STAGE_COLOR[stage] ?? "bg-slate-400")} />
                                <span className="text-muted-foreground">{STAGE_LABEL_MAP[stage] ?? stage.replace(/_/g, " ")}</span>
                                <span className="font-bold tabular-nums text-foreground">{count}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                      );
                    })()}

                    {/* Quick stat chips — location, work mode, notice, created */}
                    <div className="flex flex-wrap gap-2">
                      {role.location && (
                        <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/30 px-3 py-1 text-xs">
                          <MapPin className="h-3 w-3 shrink-0 text-muted-foreground" />
                          <span className="text-muted-foreground">Location:</span>
                          <span className="font-semibold text-foreground">{role.location}</span>
                        </span>
                      )}
                      {role.remote_policy && (
                        <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/30 px-3 py-1 text-xs">
                          <span className="text-muted-foreground">Work Mode:</span>
                          <span className="font-semibold capitalize text-foreground">{role.remote_policy}</span>
                        </span>
                      )}
                      {role.max_notice_days != null && (
                        <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/30 px-3 py-1 text-xs">
                          <span className="text-muted-foreground">Notice:</span>
                          <span className="font-semibold text-foreground">{role.max_notice_days}d cap</span>
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/30 px-3 py-1 text-xs text-muted-foreground">
                        Created {fmtDate(role.created_at)}
                      </span>
                      {(role as any).candidate_count != null && (
                        <span className="inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-primary/5 px-3 py-1 font-mono text-xs font-semibold text-primary">
                          {(role as any).candidate_count} candidates
                        </span>
                      )}
                    </div>

                    {/* Compensation card */}
                    <div className="rounded-xl border border-border bg-card shadow-sm">
                      <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3.5">
                        <span className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Compensation Structure</span>
                        {ctcMin && ctcMax && (
                          <span className="ml-auto text-sm font-semibold text-primary tabular-nums">
                            {ctcMin} – {ctcMax} LPA
                          </span>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-0 divide-x divide-border/60">
                        <div className="p-4">
                          <Field label="Min CTC (LPA)">
                            <input
                              type="number"
                              step="0.5"
                              value={ctcMin}
                              onChange={(e) => { setCtcMin(e.target.value); setDetailsDirty(true); }}
                              placeholder="8"
                              className={inlineArea}
                            />
                          </Field>
                        </div>
                        <div className="p-4">
                          <Field label="Max CTC (LPA)">
                            <input
                              type="number"
                              step="0.5"
                              value={ctcMax}
                              onChange={(e) => { setCtcMax(e.target.value); setDetailsDirty(true); }}
                              placeholder="15"
                              className={inlineArea}
                            />
                          </Field>
                        </div>
                      </div>
                      {ctcMin && ctcMax && (
                        <div className="border-t border-border/60 px-5 py-3">
                          <div className="mb-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
                            <span>Track Range</span>
                            <span className="font-medium text-foreground">{ctcMin} – {ctcMax} LPA</span>
                          </div>
                          <div className="h-1.5 w-full rounded-full bg-muted">
                            <div className="h-1.5 rounded-full bg-gradient-to-r from-primary/70 to-primary" style={{ width: "100%" }} />
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Location & work setup card */}
                    <div className="rounded-xl border border-border bg-card shadow-sm">
                      <div className="border-b border-border/60 px-5 py-3.5">
                        <span className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Work Setup</span>
                      </div>
                      <div className="p-4">
                        <div className="space-y-4">
                          <Field label="Location">
                            <input
                              value={location}
                              onChange={(e) => { setLocation(e.target.value); setDetailsDirty(true); }}
                              placeholder="e.g. Hyderabad"
                              className={inlineArea}
                            />
                          </Field>
                          <Field label="Work mode">
                            <Select
                              value={remotePolicy}
                              onValueChange={(v) => { setRemotePolicy(v); setDetailsDirty(true); }}
                            >
                              <SelectTrigger className="mt-0 h-9 text-sm hover:bg-muted/40">
                                <SelectValue placeholder="Select" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="onsite">Onsite</SelectItem>
                                <SelectItem value="hybrid">Hybrid</SelectItem>
                                <SelectItem value="remote">Remote</SelectItem>
                              </SelectContent>
                            </Select>
                          </Field>
                          <Field label="Max notice period (days)">
                            <input
                              type="number"
                              value={noticeCap}
                              onChange={(e) => { setNoticeCap(e.target.value); setDetailsDirty(true); }}
                              placeholder="60"
                              className={cn(inlineArea, "max-w-[120px]")}
                            />
                          </Field>
                        </div>
                      </div>
                    </div>

                    {/* Screening modality — read-only info chip */}
                    <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-2.5">
                      <span className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Screening</span>
                      <span className="text-sm text-foreground">
                        {(() => {
                          const m = (role?.screening_modality || "").trim();
                          if (!m || m === "none") return "No screening configured";
                          return `${m.charAt(0).toUpperCase()}${m.slice(1)} screen`;
                        })()}
                      </span>
                    </div>

                    {/* Save / actions row */}
                    <div className="flex items-center gap-3 border-t border-border/40 pt-4">
                      {detailsDirty && (
                        <Button size="sm" className="h-8 text-xs" onClick={saveDetails} disabled={saving}>
                          {saving ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <Save className="mr-1.5 h-3 w-3" />}
                          Save details
                        </Button>
                      )}
                      {role.has_problem_doc && (
                        <button
                          type="button"
                          onClick={() => { setActiveTab("Assignment"); setShowDocViewer(true); }}
                          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                        >
                          <Eye className="h-3 w-3" /> View problem doc
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* ══════════════════════════════════════════ */}
                {/* Section: Job Description                  */}
                {/* ══════════════════════════════════════════ */}
                {activeTab === "Job Description" && (
                  <div className="max-w-2xl">
                    {editingJd ? (
                      <div className="space-y-3">
                        <textarea
                          value={jdText}
                          onChange={(e) => { setJdText(e.target.value); setJdDirty(true); }}
                          rows={24}
                          className={cn(inlineArea, "min-h-[480px] font-mono text-xs")}
                          placeholder="Write the job description in Markdown..."
                        />
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setEditingJd(false);
                              setJdText(role.jd_text ?? "");
                              setJdDirty(false);
                            }}
                          >
                            Cancel
                          </Button>
                          <Button size="sm" onClick={saveJd} disabled={saving || !jdDirty}>
                            {saving ? (
                              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Save className="mr-1.5 h-3.5 w-3.5" />
                            )}
                            Save
                          </Button>
                        </div>
                      </div>
                    ) : role.jd_text ? (
                      <div className="space-y-4">
                        <div className="flex justify-end">
                          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setEditingJd(true)}>
                            <Pencil className="mr-1 h-3 w-3" /> Edit
                          </Button>
                        </div>
                        <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed">
                          <MarkdownLite source={role.jd_text} />
                        </div>
                      </div>
                    ) : (
                      <div className="py-12 text-center text-sm text-muted-foreground">
                        <FileText className="mx-auto mb-3 h-8 w-8 text-muted-foreground/40" />
                        <p>No job description set.</p>
                        <Button
                          variant="outline"
                          size="sm"
                          className="mt-3"
                          onClick={() => setEditingJd(true)}
                        >
                          <Pencil className="mr-1.5 h-3.5 w-3.5" /> Add job description
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {/* ══════════════════════════════════════════ */}
                {/* Section: Evaluation — THE RUBRIC LEDGER   */}
                {/* ══════════════════════════════════════════ */}
                {activeTab === "Evaluation" && (
                  <div className="max-w-2xl space-y-8">
                    {/* Rubric dimensions */}
                    {editingEvalSpec ? (
                      /* Edit mode — borderless hairline-divided dimensions */
                      <div className="space-y-0">
                        {evalSpecDims.length === 0 && (
                          <p className="py-4 text-sm italic text-muted-foreground">No dimensions defined</p>
                        )}
                        {evalSpecDims.map((d, i) => (
                          <div key={d.key} className="border-t border-border/40 pt-5 pb-6 first:border-0 first:pt-0">
                            {/* Label + weight row */}
                            <div className="flex items-center gap-3">
                              <input
                                value={d.label}
                                onChange={(e) => updateDim(i, "label", e.target.value)}
                                placeholder="Dimension label"
                                className="min-w-0 flex-1 bg-transparent text-sm font-semibold focus:outline-none placeholder:font-normal placeholder:text-muted-foreground/40"
                              />
                              <div className="flex shrink-0 items-center gap-1">
                                <input
                                  type="number"
                                  value={d.weight}
                                  onChange={(e) => updateDim(i, "weight", parseInt(e.target.value) || 0)}
                                  className="w-12 bg-transparent text-right font-mono text-sm tabular-nums focus:outline-none"
                                />
                                <span className="text-xs text-muted-foreground">%</span>
                              </div>
                              <button
                                type="button"
                                onClick={() => removeDim(i)}
                                className="shrink-0 p-0.5 text-muted-foreground hover:text-destructive"
                                aria-label="Remove dimension"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>

                            {/* Weight bar */}
                            <div className="mt-2">
                              <WeightBar weight={d.weight} />
                            </div>

                            {/* What good / anti-signals — two-column */}
                            <div className="mt-4 grid gap-x-8 gap-y-4 sm:grid-cols-2">
                              {/* Good signals */}
                              <div>
                                <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-emerald-600">
                                  <Check className="h-3.5 w-3.5" /> What good looks like
                                </p>
                                <div>
                                  {(d.what_good_looks_like?.length ? d.what_good_looks_like : [""]).map((s, si) => (
                                    <div key={si} className="group flex items-center gap-2 border-b border-border/40 py-1.5 last:border-0">
                                      <span className="h-1 w-1 shrink-0 rounded-full bg-emerald-500" />
                                      <input
                                        value={s}
                                        onChange={(e) => {
                                          const arr = [...(d.what_good_looks_like || [""])];
                                          arr[si] = e.target.value;
                                          updateDim(i, "what_good_looks_like", arr);
                                        }}
                                        placeholder="Describe what good looks like..."
                                        className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/40"
                                      />
                                      {si > 0 && (
                                        <button
                                          type="button"
                                          className="shrink-0 opacity-0 transition group-hover:opacity-100"
                                          onClick={() => {
                                            const arr = [...(d.what_good_looks_like || [])];
                                            arr.splice(si, 1);
                                            updateDim(i, "what_good_looks_like", arr);
                                          }}
                                        >
                                          <X className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                                        </button>
                                      )}
                                    </div>
                                  ))}
                                  <button
                                    type="button"
                                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                                    onClick={() => updateDim(i, "what_good_looks_like", [...(d.what_good_looks_like || []), ""])}
                                  >
                                    <Plus className="h-3 w-3" /> Add
                                  </button>
                                </div>
                              </div>

                              {/* Anti-signals */}
                              <div>
                                <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-red-500">
                                  <X className="h-3.5 w-3.5" /> Anti-signals
                                </p>
                                <div>
                                  {(d.anti_signals?.length ? d.anti_signals : [""]).map((s, si) => (
                                    <div key={si} className="group flex items-center gap-2 border-b border-border/40 py-1.5 last:border-0">
                                      <span className="h-1 w-1 shrink-0 rounded-full bg-red-400" />
                                      <input
                                        value={s}
                                        onChange={(e) => {
                                          const arr = [...(d.anti_signals || [""])];
                                          arr[si] = e.target.value;
                                          updateDim(i, "anti_signals", arr);
                                        }}
                                        placeholder="Describe anti-signals..."
                                        className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/40"
                                      />
                                      {si > 0 && (
                                        <button
                                          type="button"
                                          className="shrink-0 opacity-0 transition group-hover:opacity-100"
                                          onClick={() => {
                                            const arr = [...(d.anti_signals || [])];
                                            arr.splice(si, 1);
                                            updateDim(i, "anti_signals", arr);
                                          }}
                                        >
                                          <X className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                                        </button>
                                      )}
                                    </div>
                                  ))}
                                  <button
                                    type="button"
                                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                                    onClick={() => updateDim(i, "anti_signals", [...(d.anti_signals || []), ""])}
                                  >
                                    <Plus className="h-3 w-3" /> Add
                                  </button>
                                </div>
                              </div>
                            </div>
                          </div>
                        ))}

                        {/* Knockouts edit */}
                        {evalSpecKnockouts.length > 0 && (
                          <div className="border-t border-border/40 pt-5">
                            <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-destructive">
                              Knockouts
                            </p>
                            <div>
                              {evalSpecKnockouts.map((k, ki) => (
                                <div key={k.key} className="group flex items-center gap-2 border-b border-border/40 py-1.5 last:border-0">
                                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-destructive/60" />
                                  <input
                                    value={k.rule}
                                    onChange={(e) => {
                                      const next = [...evalSpecKnockouts];
                                      next[ki] = { ...k, rule: e.target.value };
                                      setEvalSpecKnockouts(next);
                                      setEvalDirty(true);
                                    }}
                                    className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/40"
                                    placeholder="Knockout rule..."
                                  />
                                  <button
                                    type="button"
                                    className="shrink-0 opacity-0 transition group-hover:opacity-100"
                                    onClick={() => { setEvalSpecKnockouts((prev) => prev.filter((_, j) => j !== ki)); setEvalDirty(true); }}
                                  >
                                    <X className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                                  </button>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <div className="flex items-center gap-2 border-t border-border/40 pt-5">
                          <button
                            type="button"
                            onClick={addDim}
                            className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                          >
                            <Plus className="h-3 w-3" /> Add dimension
                          </button>
                          <div className="flex-1" />
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 text-xs"
                            onClick={() => { setEditingEvalSpec(false); setEvalDirty(false); }}
                          >
                            Cancel
                          </Button>
                          <Button
                            size="sm"
                            className="h-8 text-xs"
                            onClick={saveEvalSpec}
                            disabled={saving || !evalDirty}
                          >
                            {saving ? (
                              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Save className="mr-1.5 h-3.5 w-3.5" />
                            )}
                            Save
                          </Button>
                        </div>
                      </div>
                    ) : role.evaluation_spec ? (
                      /* Read mode — rubric ledger */
                      (() => {
                        const spec = role.evaluation_spec as Record<string, unknown>;
                        const dims = (spec.dimensions ?? []) as Array<{
                          key: string;
                          label: string;
                          weight: number;
                          description?: string | null;
                          what_good_looks_like?: string[];
                          anti_signals?: string[];
                        }>;
                        const knockouts = (spec.knockouts ?? []) as Array<{
                          key: string;
                          rule: string;
                        }>;
                        return (
                          <div className="space-y-0">
                            <div className="mb-5 flex justify-end">
                              <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setEditingEvalSpec(true)}>
                                <Pencil className="mr-1 h-3 w-3" /> Edit
                              </Button>
                            </div>

                            {dims.length === 0 ? (
                              <p className="text-sm italic text-muted-foreground">No dimensions defined</p>
                            ) : (
                              dims.map((d) => (
                                <div key={d.key} className="border-t border-border/40 pt-5 pb-6">
                                  {/* Name + weight */}
                                  <div className="flex items-baseline justify-between gap-2">
                                    <span className="text-sm font-semibold capitalize">{d.label}</span>
                                    <span className="shrink-0 font-mono text-sm tabular-nums text-primary">{d.weight}%</span>
                                  </div>
                                  {/* Weight bar */}
                                  <div className="mt-2">
                                    <WeightBar weight={d.weight} />
                                  </div>
                                  {/* Description */}
                                  {d.description && (
                                    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{d.description}</p>
                                  )}
                                  {/* Two-column signal ledger */}
                                  {((d.what_good_looks_like && d.what_good_looks_like.length > 0) ||
                                    (d.anti_signals && d.anti_signals.length > 0)) && (
                                    <div className="mt-4 grid gap-x-8 gap-y-4 sm:grid-cols-2">
                                      <EvalSignalColumn tone="good" items={d.what_good_looks_like ?? []} />
                                      <EvalSignalColumn tone="bad" items={d.anti_signals ?? []} />
                                    </div>
                                  )}
                                </div>
                              ))
                            )}

                            {/* Knockouts */}
                            {knockouts.length > 0 && (
                              <div className="border-t border-border/40 pt-5">
                                <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-destructive">
                                  Knockouts
                                </p>
                                <div>
                                  {knockouts.map((k) => (
                                    <div key={k.key} className="flex items-center gap-2.5 border-b border-border/40 py-1.5 last:border-0">
                                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-destructive/60" />
                                      <span className="text-sm text-muted-foreground">{k.rule}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })()
                    ) : (
                      <div className="py-8 text-center text-sm text-muted-foreground">
                        <p className="italic">No evaluation criteria set</p>
                        <Button
                          variant="outline"
                          size="sm"
                          className="mt-3"
                          onClick={() => setEditingEvalSpec(true)}
                        >
                          <Plus className="mr-1.5 h-3.5 w-3.5" /> Add rubric
                        </Button>
                      </div>
                    )}

                    {/* Company context — folded below rubric */}
                    <div className="border-t border-border/40 pt-8">
                      <div className="mb-5 flex items-center justify-between">
                        <div>
                          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                            Company context
                          </p>
                          <p className="mt-0.5 text-xs text-muted-foreground/60">Shared with the AI evaluator</p>
                        </div>
                        {!editingCtx && (
                          <button
                            type="button"
                            onClick={() => setEditingCtx(true)}
                            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
                          >
                            <Pencil className="h-3 w-3" /> Edit
                          </button>
                        )}
                      </div>

                      {editingCtx ? (
                        <div className="space-y-5">
                          <Field label="Summary">
                            <textarea
                              value={ctxDraft.summary}
                              onChange={(e) => { setCtxDraft((p) => ({ ...p, summary: e.target.value })); setCtxDirty(true); }}
                              className={cn(inlineArea, "min-h-[80px]")}
                              rows={3}
                              placeholder="Brief description of the company..."
                            />
                          </Field>
                          <Field label="Hiring bar">
                            <textarea
                              value={ctxDraft.hiring_bar}
                              onChange={(e) => { setCtxDraft((p) => ({ ...p, hiring_bar: e.target.value })); setCtxDirty(true); }}
                              className={cn(inlineArea, "min-h-[64px]")}
                              rows={2}
                              placeholder="What the hiring bar looks like for this role..."
                            />
                          </Field>
                          <Field label="What matters here">
                            <div className="space-y-0">
                              {ctxDraft.what_matters_here.map((s, i) => (
                                <div key={i} className="group flex items-center gap-2 border-b border-border/40 py-1.5 last:border-0">
                                  <span className="h-1 w-1 shrink-0 rounded-full bg-primary/60" />
                                  <input
                                    value={s}
                                    onChange={(e) => {
                                      const arr = [...ctxDraft.what_matters_here];
                                      arr[i] = e.target.value;
                                      setCtxDraft((p) => ({ ...p, what_matters_here: arr }));
                                      setCtxDirty(true);
                                    }}
                                    className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/40"
                                    placeholder="e.g. Strong ownership mindset"
                                  />
                                  {ctxDraft.what_matters_here.length > 1 && (
                                    <button
                                      type="button"
                                      className="shrink-0 opacity-0 transition group-hover:opacity-100"
                                      onClick={() => {
                                        setCtxDraft((p) => ({
                                          ...p,
                                          what_matters_here: p.what_matters_here.filter((_, j) => j !== i),
                                        }));
                                        setCtxDirty(true);
                                      }}
                                    >
                                      <X className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
                                    </button>
                                  )}
                                </div>
                              ))}
                              <button
                                type="button"
                                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                                onClick={() => {
                                  setCtxDraft((p) => ({ ...p, what_matters_here: [...p.what_matters_here, ""] }));
                                  setCtxDirty(true);
                                }}
                              >
                                <Plus className="h-3 w-3" /> Add item
                              </button>
                            </div>
                          </Field>
                          <div className="flex items-center justify-end gap-2 pt-1">
                            <Button variant="ghost" size="sm" onClick={() => { setEditingCtx(false); setCtxDirty(false); }}>
                              Cancel
                            </Button>
                            <Button size="sm" onClick={saveCtx} disabled={saving || !ctxDirty}>
                              {saving ? (
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Save className="mr-1.5 h-3.5 w-3.5" />
                              )}
                              Save
                            </Button>
                          </div>
                        </div>
                      ) : role.company_context ? (
                        (() => {
                          const ctx = role.company_context as Record<string, unknown>;
                          const summary = ctx.summary as string | undefined;
                          const hiringBar = ctx.hiring_bar as string | undefined;
                          const whatMatters = ctx.what_matters_here as string[] | undefined;
                          const isEmpty = !summary && !hiringBar && (!whatMatters || whatMatters.length === 0);
                          if (isEmpty) return <p className="text-sm italic text-muted-foreground">No company context set</p>;
                          return (
                            <div className="space-y-4">
                              {summary && <p className="text-sm leading-relaxed text-muted-foreground">{summary}</p>}
                              {hiringBar && (
                                <div>
                                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">Hiring bar</p>
                                  <p className="text-sm text-muted-foreground">{hiringBar}</p>
                                </div>
                              )}
                              {whatMatters && whatMatters.length > 0 && (
                                <div>
                                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">What matters here</p>
                                  <div>
                                    {whatMatters.map((s, i) => (
                                      <div key={i} className="flex items-center gap-2.5 border-b border-border/40 py-1.5 last:border-0">
                                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                                        <span className="text-sm text-muted-foreground">{s}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <p className="text-sm italic text-muted-foreground">No company context set</p>
                      )}
                    </div>
                  </div>
                )}

                {/* ══════════════════════════════════════════ */}
                {/* Section: Pipeline                         */}
                {/* ══════════════════════════════════════════ */}
                {activeTab === "Pipeline" && (
                  <div>
                    <PipelineTimeline
                      stages={
                        role.pipeline && role.pipeline.length > 0
                          ? role.pipeline
                          : (role.pipeline_template ?? []).map((stageKey, i) => ({
                              stage_key: stageKey,
                              stage_type: "",
                              label: humanizeStage(stageKey),
                              position: i,
                              mode: ["parse", "fit_score", "fit", "intake"].includes(stageKey)
                                ? "auto"
                                : "manual",
                              is_enabled: true,
                            }))
                      }
                    />
                  </div>
                )}

                {/* ══════════════════════════════════════════ */}
                {/* Section: Assignment                       */}
                {/* ══════════════════════════════════════════ */}
                {activeTab === "Assignment" && (
                  <div className="max-w-2xl space-y-8">
                    {/* Brief + instructions */}
                    <div className="space-y-6">
                      <Field label="Assignment brief">
                        <textarea
                          value={assignmentBrief}
                          onChange={(e) => { setAssignmentBrief(e.target.value); setAssignmentDirty(true); }}
                          placeholder="Brief description of the take-home assignment..."
                          rows={4}
                          className={cn(inlineArea, "min-h-[96px]")}
                        />
                      </Field>
                      <Field label="Instructions">
                        <textarea
                          value={assignmentInstructions}
                          onChange={(e) => { setAssignmentInstructions(e.target.value); setAssignmentDirty(true); }}
                          placeholder="Step-by-step instructions for completing the assignment..."
                          rows={4}
                          className={cn(inlineArea, "min-h-[96px]")}
                        />
                      </Field>
                      <div className="flex items-center gap-3 border-t border-border/40 pt-5">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                          Deadline
                        </span>
                        <input
                          type="number"
                          value={assignmentDeadlineDays}
                          onChange={(e) => { setAssignmentDeadlineDays(e.target.value); setAssignmentDirty(true); }}
                          placeholder="7"
                          className="w-16 rounded-md bg-transparent px-2 py-1.5 text-center text-sm tabular-nums transition hover:bg-muted/40 focus:bg-muted/50 focus:outline-none focus:ring-1 focus:ring-border"
                        />
                        <span className="text-sm text-muted-foreground">days from invite</span>
                      </div>

                      <div className="space-y-4 border-t border-border/40 pt-5">
                          <div>
                            <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                              PI Assessments
                            </span>
                            <p className="mt-0.5 text-xs text-muted-foreground/60">
                              Links are included in the assignment email when PI tests are enabled org-wide. Both are optional.
                            </p>
                          </div>
                          <Field label="Cognitive test URL">
                            <input
                              type="url"
                              value={piCognitiveLink}
                              onChange={(e) => { setPiCognitiveLink(e.target.value); setAssignmentDirty(true); }}
                              placeholder="https://app.predictiveindex.com/..."
                              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm transition focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-muted-foreground/40"
                            />
                          </Field>
                          <Field label="Behavioural test URL">
                            <input
                              type="url"
                              value={piPersonalityLink}
                              onChange={(e) => { setPiPersonalityLink(e.target.value); setAssignmentDirty(true); }}
                              placeholder="https://app.predictiveindex.com/..."
                              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm transition focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-muted-foreground/40"
                            />
                          </Field>
                        </div>
                    </div>

                    {assignmentDirty && (
                      <div className="flex justify-end">
                        <Button size="sm" className="h-8 text-xs" onClick={saveAssignment} disabled={saving}>
                          {saving ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Save className="mr-1.5 h-3.5 w-3.5" />
                          )}
                          Save changes
                        </Button>
                      </div>
                    )}

                    {/* Problem document */}
                    {role.has_problem_doc && (
                      <div className="border-t border-border/40 pt-6">
                        <p className="mb-4 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                          Problem document
                        </p>
                        <div className="flex items-center gap-3">
                          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                            <FileText className="h-4 w-4 text-primary" />
                          </div>
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">
                            {role.assignment_problem_doc_filename ?? "Problem statement"}
                          </span>
                          <button
                            type="button"
                            onClick={() => setShowDocViewer((v) => !v)}
                            className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                          >
                            <Eye className="h-3 w-3" /> {showDocViewer ? "Hide" : "View"}
                          </button>
                          <button
                            type="button"
                            onClick={downloadProblemDoc}
                            disabled={downloading}
                            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
                          >
                            {downloading ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <Download className="h-3 w-3" />
                            )}
                            Download
                          </button>
                        </div>
                        {showDocViewer && (
                          <div className="mt-4">
                            <DocViewer
                              roleId={id}
                              getDocUrl={getDocUrl}
                              onClose={() => setShowDocViewer(false)}
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {/* Publish action */}
                    {(role.status as string) === "draft" &&
                      !!role.assignment_brief &&
                      !role.has_problem_doc && (
                        <div className="border-t border-border/40 pt-6">
                          <p className="text-sm font-semibold">Ready to publish?</p>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            This will generate a PDF from the brief and make the role live.
                          </p>
                          <Button
                            onClick={publishAssignment}
                            disabled={publishing || saving}
                            className="mt-4 h-8 text-xs"
                          >
                            {publishing ? (
                              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <FileText className="mr-1.5 h-3.5 w-3.5" />
                            )}
                            Publish assignment
                          </Button>
                        </div>
                      )}

                    {/* Screening questions */}
                    {(role as any).screening_questions &&
                      Array.isArray((role as any).screening_questions) &&
                      (role as any).screening_questions.length > 0 && (
                        <div className="border-t border-border/40 pt-6">
                          <p className="mb-4 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                            Screening questions
                          </p>
                          <ol className="space-y-0">
                            {((role as any).screening_questions as string[]).map((q, i) => (
                              <li key={i} className="flex items-start gap-3 border-b border-border/40 py-2.5 last:border-0">
                                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-xs tabular-nums text-muted-foreground">
                                  {i + 1}
                                </span>
                                <span className="text-sm leading-relaxed text-muted-foreground">{q}</span>
                              </li>
                            ))}
                          </ol>
                        </div>
                      )}
                  </div>
                )}
              </div>
          )}
        </div>
      </div>
    </div>
  );
}
