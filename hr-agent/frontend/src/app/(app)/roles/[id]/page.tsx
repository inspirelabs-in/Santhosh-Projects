"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import {
  ArrowLeft,
  Check,
  Download,
  Eye,
  FileText,
  Loader2,
  MapPin,
  Pause,
  Pencil,
  Play,
  Save,
  X,
  XCircle,
} from "lucide-react";
import { cn, fmtDate } from "@/lib/utils";
import { api, swrFetcher } from "@/lib/api";
import { Topbar } from "@/components/layout/topbar";
import { MarkdownLite } from "@/components/markdown-lite";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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

const STATUS_VARIANT: Record<string, "success" | "warning" | "muted" | "destructive"> = {
  open: "success",
  paused: "warning",
  filled: "muted",
  cancelled: "destructive",
};

function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
}

function PageSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-6 w-48" />
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-4">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
        <Skeleton className="h-80 w-full" />
      </div>
    </div>
  );
}

function DocViewer({ roleId, getDocUrl, onClose }: { roleId: string; getDocUrl: (inline: boolean) => string; onClose: () => void }) {
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
    return () => { cancelled = true; };
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
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
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

export default function RoleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const {
    data: role,
    isLoading,
    mutate,
  } = useSWR<Role>(id ? `/dashboard/roles/${id}` : null, swrFetcher);

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
  const [jdText, setJdText] = useState("");
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [jdDirty, setJdDirty] = useState(false);
  const [editingJd, setEditingJd] = useState(false);

  // Evaluation spec editing
  const [editingEvalSpec, setEditingEvalSpec] = useState(false);
  const [evalSpecDims, setEvalSpecDims] = useState<
    { key: string; label: string; weight: number; what_good_looks_like: string[]; anti_signals: string[] }[]
  >([]);
  const [evalSpecKnockouts, setEvalSpecKnockouts] = useState<{ key: string; rule: string }[]>([]);
  const [evalDirty, setEvalDirty] = useState(false);

  // Company context editing
  const [editingCtx, setEditingCtx] = useState(false);
  const [ctxDraft, setCtxDraft] = useState<{
    intensity: string; summary: string; hiring_bar: string; what_matters_here: string[];
  }>({ intensity: "standard", summary: "", hiring_bar: "", what_matters_here: [] });
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
    setJdText(role.jd_text ?? "");

    const spec = role.evaluation_spec as Record<string, unknown> | undefined;
    if (spec?.dimensions) {
      setEvalSpecDims(spec.dimensions as any[]);
      setEvalSpecKnockouts((spec.knockouts ?? []) as any[]);
    }
    const ctx = role.company_context as Record<string, unknown> | undefined;
    if (ctx) {
      setCtxDraft({
        intensity: (ctx.intensity as string) ?? "standard",
        summary: (ctx.summary as string) ?? "",
        hiring_bar: (ctx.hiring_bar as string) ?? "",
        what_matters_here: ((ctx.what_matters_here as string[]) ?? []),
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
        assignment_brief: assignmentBrief || null,
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
      { key: `dim_${Date.now()}`, label: "New dimension", weight: 10, what_good_looks_like: [""], anti_signals: [""] },
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

  const viewProblemDoc = () => {
    setShowDocViewer((v) => !v);
  };

  const breadcrumbOverrides: Record<string, string> = {};
  if (id) breadcrumbOverrides[id] = role?.title ?? "...";

  if (!id) return null;

  return (
    <>
      <Topbar
        title={role?.title ?? "Role"}
        subtitle="Role configuration"
        breadcrumbOverrides={breadcrumbOverrides}
      />

      <div className="flex-1 overflow-auto px-6 py-5 pb-20">
        <Link
          href="/roles"
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to roles
        </Link>

        {isLoading ? (
          <PageSkeleton />
        ) : !role ? (
          <Card className="p-12 text-center">
            <XCircle className="mx-auto h-10 w-10 text-muted-foreground/50" />
            <p className="mt-4 text-sm text-muted-foreground">
              Role not found or could not be loaded.
            </p>
            <Button variant="outline" size="sm" className="mt-4" onClick={() => router.push("/roles")}>
              Back to roles
            </Button>
          </Card>
        ) : (
          <div className="space-y-5">
            {/* Header */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 flex-1">
                {editingTitle ? (
                  <div className="flex items-center gap-2">
                    <Input
                      value={titleDraft}
                      onChange={(e) => setTitleDraft(e.target.value)}
                      className="max-w-md text-xl font-bold"
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
                  <div className="flex items-center gap-3">
                    <h2 className="font-display text-2xl font-normal tracking-tight">{role.title}</h2>
                    <button
                      onClick={() => setEditingTitle(true)}
                      className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </div>
                )}

                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
                  <Badge variant={STATUS_VARIANT[role.status] ?? "muted"}>{role.status}</Badge>
                  {role.location && (
                    <span className="flex items-center gap-1">
                      <MapPin className="h-3.5 w-3.5" />
                      {role.location}
                      {role.remote_policy && <span className="text-xs">({role.remote_policy})</span>}
                    </span>
                  )}
                  {role.ctc_min_lpa != null && role.ctc_max_lpa != null && (
                    <span className="text-xs font-medium">{role.ctc_min_lpa} - {role.ctc_max_lpa} LPA</span>
                  )}
                  <span>Created {fmtDate(role.created_at)}</span>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {role.status === "open" && (
                  <Button variant="outline" size="sm" onClick={() => changeStatus("paused")} disabled={saving}>
                    <Pause className="mr-1.5 h-3.5 w-3.5" /> Pause
                  </Button>
                )}
                {role.status === "paused" && (
                  <Button variant="outline" size="sm" onClick={() => changeStatus("open")} disabled={saving}>
                    <Play className="mr-1.5 h-3.5 w-3.5" /> Reopen
                  </Button>
                )}
                {(role.status === "open" || role.status === "paused") && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:bg-destructive/10"
                    onClick={() => changeStatus("cancelled")}
                    disabled={saving}
                  >
                    <XCircle className="mr-1.5 h-3.5 w-3.5" /> Close
                  </Button>
                )}
              </div>
            </div>

            {/* Save toast */}
            {saveMsg && (
              <div
                className={cn(
                  "rounded-md border px-4 py-2 text-sm",
                  saveMsg.type === "ok"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                    : "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300",
                )}
              >
                {saveMsg.text}
              </div>
            )}

            {/* Main grid */}
            <div className="grid gap-5 lg:grid-cols-3">
              {/* Left: JD + Assignment */}
              <div className="lg:col-span-2 space-y-5">

                {/* Job Description */}
                <Card>
                  <div className="flex items-center justify-between px-5 pt-4 pb-2">
                    <h3 className="text-sm font-semibold">Job description</h3>
                    {!editingJd && (
                      <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setEditingJd(true)}>
                        <Pencil className="mr-1 h-3 w-3" /> Edit
                      </Button>
                    )}
                  </div>
                  <CardContent className="pt-0">
                    {editingJd ? (
                      <div className="space-y-2">
                        <Textarea
                          value={jdText}
                          onChange={(e) => { setJdText(e.target.value); setJdDirty(true); }}
                          rows={14}
                          className="text-sm font-mono"
                        />
                        <div className="flex items-center gap-2 justify-end">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => { setEditingJd(false); setJdText(role.jd_text ?? ""); setJdDirty(false); }}
                          >
                            Cancel
                          </Button>
                          <Button size="sm" onClick={saveJd} disabled={saving || !jdDirty}>
                            {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />}
                            Save
                          </Button>
                        </div>
                      </div>
                    ) : role.jd_text ? (
                      <MarkdownLite source={role.jd_text} />
                    ) : (
                      <p className="text-sm text-muted-foreground italic py-4">
                        No job description set. Click Edit to add one.
                      </p>
                    )}
                  </CardContent>
                </Card>

                {/* Assignment */}
                {(role.assignment_brief || role.has_problem_doc) && (
                  <Card>
                    <div className="px-5 pt-4 pb-2">
                      <h3 className="text-sm font-semibold">Assignment</h3>
                      <p className="text-xs text-muted-foreground">Take-home challenge for candidates</p>
                    </div>
                    <CardContent className="pt-0 space-y-3">
                      {role.assignment_brief && (
                        <p className="text-sm leading-relaxed whitespace-pre-wrap">{role.assignment_brief}</p>
                      )}
                      {role.has_problem_doc && (
                        <>
                          <div className="flex items-center gap-3 rounded-lg border p-3">
                            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                              <FileText className="h-4 w-4 text-primary" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium truncate">
                                {role.assignment_problem_doc_filename ?? "Problem statement"}
                              </p>
                              <p className="text-[11px] text-muted-foreground">Uploaded problem document</p>
                            </div>
                            <div className="flex gap-1.5">
                              <Button variant="outline" size="sm" onClick={viewProblemDoc}>
                                <Eye className="mr-1.5 h-3.5 w-3.5" />
                                View
                              </Button>
                              <Button variant="outline" size="sm" onClick={downloadProblemDoc} disabled={downloading}>
                                {downloading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1.5 h-3.5 w-3.5" />}
                                Download
                              </Button>
                            </div>
                          </div>
                          {showDocViewer && (
                            <DocViewer roleId={id} getDocUrl={getDocUrl} onClose={() => setShowDocViewer(false)} />
                          )}
                        </>
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>

              {/* Right: Role details (editable) */}
              <div>
                <Card>
                  <div className="flex items-center justify-between px-5 pt-4 pb-2">
                    <h3 className="text-sm font-semibold">Role details</h3>
                    {detailsDirty && (
                      <Button size="sm" className="h-7 text-xs" onClick={saveDetails} disabled={saving}>
                        {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
                        Save
                      </Button>
                    )}
                  </div>
                  <CardContent className="pt-0 space-y-3">
                    {/* CTC */}
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <Label className="text-[11px] text-muted-foreground">CTC min (LPA)</Label>
                        <Input
                          type="number"
                          step="0.5"
                          value={ctcMin}
                          onChange={(e) => { setCtcMin(e.target.value); setDetailsDirty(true); }}
                          placeholder="8"
                          className="mt-1 h-8 text-sm"
                        />
                      </div>
                      <div>
                        <Label className="text-[11px] text-muted-foreground">CTC max (LPA)</Label>
                        <Input
                          type="number"
                          step="0.5"
                          value={ctcMax}
                          onChange={(e) => { setCtcMax(e.target.value); setDetailsDirty(true); }}
                          placeholder="15"
                          className="mt-1 h-8 text-sm"
                        />
                      </div>
                    </div>

                    {/* Location */}
                    <div>
                      <Label className="text-[11px] text-muted-foreground">Location</Label>
                      <Input
                        value={location}
                        onChange={(e) => { setLocation(e.target.value); setDetailsDirty(true); }}
                        placeholder="e.g. Hyderabad"
                        className="mt-1 h-8 text-sm"
                      />
                    </div>

                    {/* Remote policy */}
                    <div>
                      <Label className="text-[11px] text-muted-foreground">Work mode</Label>
                      <Select
                        value={remotePolicy}
                        onValueChange={(v) => { setRemotePolicy(v); setDetailsDirty(true); }}
                      >
                        <SelectTrigger className="mt-1 h-8 text-sm">
                          <SelectValue placeholder="Select" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="onsite">Onsite</SelectItem>
                          <SelectItem value="hybrid">Hybrid</SelectItem>
                          <SelectItem value="remote">Remote</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Notice cap */}
                    <div>
                      <Label className="text-[11px] text-muted-foreground">Max notice (days)</Label>
                      <Input
                        type="number"
                        value={noticeCap}
                        onChange={(e) => { setNoticeCap(e.target.value); setDetailsDirty(true); }}
                        placeholder="60"
                        className="mt-1 h-8 text-sm"
                      />
                    </div>

                    {/* Screening — read-only, reflects the role's screening modality */}
                    <div>
                      <Label className="text-[11px] text-muted-foreground">Screening</Label>
                      <div className="mt-1 flex items-center gap-1.5 rounded-md border bg-muted/30 px-3 py-1.5 text-sm text-muted-foreground">
                        {(() => {
                          const m = (role?.screening_modality || "").trim();
                          if (!m || m === "none") return "No screening";
                          return `${m.charAt(0).toUpperCase()}${m.slice(1)} screen`;
                        })()}
                      </div>
                    </div>

                    {/* Evaluation spec — editable */}
                    <div>
                      <div className="flex items-center justify-between">
                        <Label className="text-[11px] text-muted-foreground">Evaluation criteria</Label>
                        {!editingEvalSpec && (
                          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setEditingEvalSpec(true)}>
                            <Pencil className="mr-1 h-3 w-3" /> Edit
                          </Button>
                        )}
                      </div>
                      <div className="mt-1 space-y-2 rounded-md border bg-muted/30 p-3 text-sm">
                        {editingEvalSpec ? (
                          <div className="space-y-3">
                            {evalSpecDims.length === 0 && (
                              <p className="text-muted-foreground italic text-xs">No dimensions defined</p>
                            )}
                            {evalSpecDims.map((d, i) => (
                              <div key={d.key} className="border-b border-border/40 pb-3 last:border-0 last:pb-0 space-y-1.5">
                                <div className="flex items-center gap-2">
                                  <Input
                                    value={d.label}
                                    onChange={(e) => updateDim(i, "label", e.target.value)}
                                    className="h-7 text-xs flex-1"
                                    placeholder="Label"
                                  />
                                  <Input
                                    type="number"
                                    value={d.weight}
                                    onChange={(e) => updateDim(i, "weight", parseInt(e.target.value) || 0)}
                                    className="h-7 text-xs w-16"
                                    placeholder="Wt"
                                  />
                                  <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-destructive" onClick={() => removeDim(i)}>
                                    <X className="h-3 w-3" />
                                  </Button>
                                </div>
                                <div>
                                  <p className="text-[10px] text-emerald-600 mb-0.5">What good looks like</p>
                                  {(d.what_good_looks_like?.length ? d.what_good_looks_like : [""]).map((s, si) => (
                                    <div key={si} className="flex items-center gap-1 mb-0.5">
                                      <textarea
                                        value={s}
                                        onChange={(e) => {
                                          const arr = [...(d.what_good_looks_like || [""])];
                                          arr[si] = e.target.value;
                                          updateDim(i, "what_good_looks_like", arr);
                                        }}
                                        className="w-full text-[11px] rounded border border-border bg-background px-1.5 py-0.5 resize-none"
                                        rows={2}
                                      />
                                      {si > 0 && (
                                        <button
                                          className="text-destructive hover:text-destructive/80 text-xs"
                                          onClick={() => {
                                            const arr = [...(d.what_good_looks_like || [])];
                                            arr.splice(si, 1);
                                            updateDim(i, "what_good_looks_like", arr);
                                          }}
                                        >
                                          <X className="h-3 w-3" />
                                        </button>
                                      )}
                                    </div>
                                  ))}
                                  <button
                                    className="text-[10px] text-primary hover:underline mt-0.5"
                                    onClick={() => {
                                      updateDim(i, "what_good_looks_like", [...(d.what_good_looks_like || []), ""]);
                                    }}
                                  >
                                    + Add signal
                                  </button>
                                </div>
                                <div>
                                  <p className="text-[10px] text-destructive mb-0.5">Anti-signals</p>
                                  {(d.anti_signals?.length ? d.anti_signals : [""]).map((s, si) => (
                                    <div key={si} className="flex items-center gap-1 mb-0.5">
                                      <textarea
                                        value={s}
                                        onChange={(e) => {
                                          const arr = [...(d.anti_signals || [""])];
                                          arr[si] = e.target.value;
                                          updateDim(i, "anti_signals", arr);
                                        }}
                                        className="w-full text-[11px] rounded border border-border bg-background px-1.5 py-0.5 resize-none"
                                        rows={2}
                                      />
                                      {si > 0 && (
                                        <button
                                          className="text-destructive hover:text-destructive/80 text-xs"
                                          onClick={() => {
                                            const arr = [...(d.anti_signals || [])];
                                            arr.splice(si, 1);
                                            updateDim(i, "anti_signals", arr);
                                          }}
                                        >
                                          <X className="h-3 w-3" />
                                        </button>
                                      )}
                                    </div>
                                  ))}
                                  <button
                                    className="text-[10px] text-primary hover:underline mt-0.5"
                                    onClick={() => {
                                      updateDim(i, "anti_signals", [...(d.anti_signals || []), ""]);
                                    }}
                                  >
                                    + Add anti-signal
                                  </button>
                                </div>
                              </div>
                            ))}
                            <div className="flex items-center gap-2 pt-1">
                              <Button variant="outline" size="sm" className="h-7 text-xs" onClick={addDim}>
                                + Add dimension
                              </Button>
                              <div className="flex-1" />
                              <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => { setEditingEvalSpec(false); setEvalDirty(false); }}>
                                Cancel
                              </Button>
                              <Button size="sm" className="h-7 text-xs" onClick={saveEvalSpec} disabled={saving || !evalDirty}>
                                {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
                                Save
                              </Button>
                            </div>
                          </div>
                        ) : role.evaluation_spec ? (
                          (() => {
                            const spec = role.evaluation_spec as Record<string, unknown>;
                            const dims = (spec.dimensions ?? []) as Array<{
                              key: string; label: string; weight: number;
                              description?: string | null;
                              what_good_looks_like?: string[];
                              anti_signals?: string[];
                            }>;
                            const knockouts = (spec.knockouts ?? []) as Array<{ key: string; rule: string; }>;
                            return (
                              <>
                                {dims.length === 0 ? (
                                  <p className="text-muted-foreground italic">No dimensions defined</p>
                                ) : dims.map((d) => (
                                  <div key={d.key} className="border-b border-border/40 pb-2 last:border-0 last:pb-0">
                                    <div className="flex items-center justify-between">
                                      <span className="font-medium">{d.label}</span>
                                      <span className="text-[11px] text-muted-foreground">{d.weight}%</span>
                                    </div>
                                    {d.description && (
                                      <p className="text-[12px] text-muted-foreground mt-0.5">{d.description}</p>
                                    )}
                                    {Array.isArray(d.what_good_looks_like) && d.what_good_looks_like.length > 0 && (
                                      <div className="mt-1">
                                        <p className="text-[11px] font-medium text-emerald-600">What good looks like</p>
                                        {d.what_good_looks_like.map((s, i) => (
                                          <p key={i} className="text-[12px] text-muted-foreground">• {s}</p>
                                        ))}
                                      </div>
                                    )}
                                    {Array.isArray(d.anti_signals) && d.anti_signals.length > 0 && (
                                      <div className="mt-1">
                                        <p className="text-[11px] font-medium text-destructive">Anti-signals</p>
                                        {d.anti_signals.map((s, i) => (
                                          <p key={i} className="text-[12px] text-muted-foreground">• {s}</p>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                ))}
                                {knockouts.length > 0 && (
                                  <div className="pt-1 border-t border-border/40">
                                    <p className="text-[11px] font-medium text-destructive mb-1">Knockouts</p>
                                    {knockouts.map((k) => (
                                      <p key={k.key} className="text-[12px] text-muted-foreground">• {k.rule}</p>
                                    ))}
                                  </div>
                                )}
                              </>
                            );
                          })()
                        ) : (
                          <p className="text-muted-foreground italic">No evaluation criteria set</p>
                        )}
                      </div>
                    </div>

                    {/* Company context — editable */}
                    <div>
                      <div className="flex items-center justify-between">
                        <Label className="text-[11px] text-muted-foreground">Company context</Label>
                        {!editingCtx && (
                          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setEditingCtx(true)}>
                            <Pencil className="mr-1 h-3 w-3" /> Edit
                          </Button>
                        )}
                      </div>
                      <div className="mt-1 space-y-2 rounded-md border bg-muted/30 p-3 text-sm">
                        {editingCtx ? (
                          <div className="space-y-2">
                            <div>
                              <Label className="text-[10px] text-muted-foreground">Intensity</Label>
                              <Select
                                value={ctxDraft.intensity}
                                onValueChange={(v) => { setCtxDraft((p) => ({ ...p, intensity: v })); setCtxDirty(true); }}
                              >
                                <SelectTrigger className="mt-0.5 h-7 text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="light">Light</SelectItem>
                                  <SelectItem value="standard">Standard</SelectItem>
                                  <SelectItem value="high">High</SelectItem>
                                  <SelectItem value="critical">Critical</SelectItem>
                                </SelectContent>
                              </Select>
                            </div>
                            <div>
                              <Label className="text-[10px] text-muted-foreground">Summary</Label>
                              <textarea
                                value={ctxDraft.summary}
                                onChange={(e) => { setCtxDraft((p) => ({ ...p, summary: e.target.value })); setCtxDirty(true); }}
                                className="mt-0.5 w-full text-xs rounded border border-border bg-background px-2 py-1 resize-none"
                                rows={3}
                              />
                            </div>
                            <div>
                              <Label className="text-[10px] text-muted-foreground">Hiring bar</Label>
                              <textarea
                                value={ctxDraft.hiring_bar}
                                onChange={(e) => { setCtxDraft((p) => ({ ...p, hiring_bar: e.target.value })); setCtxDirty(true); }}
                                className="mt-0.5 w-full text-xs rounded border border-border bg-background px-2 py-1 resize-none"
                                rows={2}
                              />
                            </div>
                            <div>
                              <Label className="text-[10px] text-muted-foreground">What matters here</Label>
                              {ctxDraft.what_matters_here.map((s, i) => (
                                <div key={i} className="flex items-center gap-1 mt-0.5">
                                  <input
                                    value={s}
                                    onChange={(e) => {
                                      const arr = [...ctxDraft.what_matters_here];
                                      arr[i] = e.target.value;
                                      setCtxDraft((p) => ({ ...p, what_matters_here: arr }));
                                      setCtxDirty(true);
                                    }}
                                    className="flex-1 text-xs rounded border border-border bg-background px-1.5 py-0.5"
                                  />
                                  {ctxDraft.what_matters_here.length > 1 && (
                                    <button
                                      className="text-destructive text-xs"
                                      onClick={() => {
                                        setCtxDraft((p) => ({ ...p, what_matters_here: p.what_matters_here.filter((_, j) => j !== i) }));
                                        setCtxDirty(true);
                                      }}
                                    >
                                      <X className="h-3 w-3" />
                                    </button>
                                  )}
                                </div>
                              ))}
                              <button
                                className="text-[10px] text-primary hover:underline mt-0.5"
                                onClick={() => {
                                  setCtxDraft((p) => ({ ...p, what_matters_here: [...p.what_matters_here, ""] }));
                                  setCtxDirty(true);
                                }}
                              >
                                + Add signal
                              </button>
                            </div>
                            <div className="flex items-center gap-2 pt-1">
                              <div className="flex-1" />
                              <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => { setEditingCtx(false); setCtxDirty(false); }}>
                                Cancel
                              </Button>
                              <Button size="sm" className="h-7 text-xs" onClick={saveCtx} disabled={saving || !ctxDirty}>
                                {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
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
                            return (
                              <div className="space-y-1.5">
                                <div className="flex items-center gap-2">
                                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Intensity</span>
                                  <Badge variant="outline" className="text-[10px]">{(ctx.intensity as string) ?? "standard"}</Badge>
                                </div>
                                {summary && <p className="text-xs text-muted-foreground">{summary}</p>}
                                {hiringBar && (
                                  <div>
                                    <p className="text-[10px] font-medium text-muted-foreground">Hiring bar</p>
                                    <p className="text-xs text-muted-foreground">{hiringBar}</p>
                                  </div>
                                )}
                                {whatMatters && whatMatters.length > 0 && (
                                  <div>
                                    <p className="text-[10px] font-medium text-muted-foreground">What matters here</p>
                                    {whatMatters.map((s, i) => (
                                      <p key={i} className="text-xs text-muted-foreground">• {s}</p>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })()
                        ) : (
                          <p className="text-muted-foreground italic">No company context set</p>
                        )}
                      </div>
                    </div>

                    {/* Assignment brief */}
                    <div>
                      <Label className="text-[11px] text-muted-foreground">Assignment brief</Label>
                      <Textarea
                        value={assignmentBrief}
                        onChange={(e) => { setAssignmentBrief(e.target.value); setDetailsDirty(true); }}
                        placeholder="Brief description of the take-home assignment..."
                        rows={3}
                        className="mt-1 text-sm"
                      />
                    </div>

                    {/* Problem doc download */}
                    {role.has_problem_doc && (
                      <div className="flex items-center justify-between pt-1 border-t">
                        <span className="text-[11px] text-muted-foreground">Assignment doc</span>
                        <button
                          onClick={downloadProblemDoc}
                          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                        >
                          <Download className="h-3 w-3" />
                          {role.assignment_problem_doc_filename ?? "Download"}
                        </button>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
