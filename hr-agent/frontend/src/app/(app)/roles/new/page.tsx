"use client";

/**
 * Metaview-style role creation.
 *
 * Two-panel layout:
 *   Left   = chat (the agent runs an intake-style conversation).
 *   Right  = live JD document (the work). Each section is its own
 *            editable card. Sections fill in as the chat progresses.
 *            Per-section "edit" + "regenerate with AI" actions.
 *
 * The agent prompt instructs it to write the JD as markdown ``## ``
 * sections in a fixed order, so the frontend can split jd_text into
 * named sections without round-tripping the schema.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowUp,
  Loader2,
  CheckCheck,
  Linkedin,
  ClipboardCopy,
  RotateCcw,
  Settings2,
  Brain,
  Search,
  PenLine,
  Database,
  Wand2,
  Pencil,
  X,
  Save,
  RefreshCw,
  FileText,
  MapPin,
  IndianRupee,
  Upload,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { getDashboardKey } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  roleChat,
  type ChatMessage,
  type LinkedInPost,
  type RoleDefaults,
  type RoleDraftEnvelope,
} from "@/lib/api/agentic";
import type { Role } from "@/lib/types";

// ---------------------------------------------------------------------------
// JD section parsing -- the agent emits ``## Section Name`` headers in this
// fixed order. Frontend splits the blob into a stable array.
// ---------------------------------------------------------------------------

const SECTIONS = [
  "About the role",
  "What you'll do",
  "Must-haves",
  "Nice-to-haves",
  "Compensation & process",
] as const;

type SectionName = (typeof SECTIONS)[number];

function parseJdSections(jd: string | undefined | null): Record<SectionName, string> {
  const out: Record<string, string> = {};
  for (const s of SECTIONS) out[s] = "";
  if (!jd) return out as Record<SectionName, string>;
  const lines = jd.split(/\r?\n/);
  let current: string | null = null;
  let buf: string[] = [];
  const flush = () => {
    if (current && current in out) {
      out[current] = buf.join("\n").trim();
    }
    buf = [];
  };
  for (const line of lines) {
    const m = line.match(/^\s*##\s+(.*?)\s*$/);
    if (m) {
      flush();
      const heading = m[1].trim();
      const matched = (SECTIONS as readonly string[]).find(
        (s) => s.toLowerCase() === heading.toLowerCase(),
      );
      current = matched ?? heading;
    } else if (current) {
      buf.push(line);
    }
  }
  flush();
  return out as Record<SectionName, string>;
}

function rebuildJd(sections: Record<SectionName, string>): string {
  return SECTIONS.map((s) => `## ${s}\n${(sections[s] || "").trim()}`)
    .join("\n\n")
    .trim();
}

const STARTERS = [
  "Hire a Senior Backend Engineer for the GrabOn checkout team. CTC 24-32 LPA, hybrid Hyderabad.",
  "We need a Product Designer (3-5 yrs). Remote within India is fine. Budget up to 20 LPA.",
  "Open a Customer Success Manager in Hyderabad reporting to the COO.",
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function NewRoleChatPage() {
  const router = useRouter();
  const [defaults, setDefaults] = useState<RoleDefaults | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [missing, setMissing] = useState<string[]>([]);
  const [quickReplies, setQuickReplies] = useState<string[]>([]);
  const [readyToSave, setReadyToSave] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkedin, setLinkedin] = useState<LinkedInPost | null>(null);
  const [linkedinBusy, setLinkedinBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [problemDoc, setProblemDoc] = useState<File | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const sections = useMemo(() => parseJdSections(draft.jd_text), [draft.jd_text]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await roleChat.defaults();
        if (!cancelled) setDefaults(d);
      } catch { }
      if (cancelled) return;
      setHistory([
        {
          role: "assistant",
          content:
            "Tell me what role you're hiring for. I'll write the full JD first, then walk you through location, comp, notice period, and how the agent should handle screening + scheduling.",
        },
      ]);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    });
  }, [history.length, busy]);

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setError(null);
    setHistory((h) => [...h, { role: "user", content: message }]);
    setInput("");
    setBusy(true);
    inputRef.current?.focus();
    try {
      const env: RoleDraftEnvelope = await roleChat.send({
        user_message: message,
        history,
        draft,
      });
      setHistory((h) => [...h, { role: "assistant", content: env.message }]);
      if (env.draft && Object.keys(env.draft).length > 0) setDraft(env.draft);
      setMissing(env.missing ?? []);
      setQuickReplies(env.quick_replies ?? []);
      setReadyToSave(!!env.ready_to_save);
    } catch (e: any) {
      setError(e?.message ?? "chat failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveRole() {
    setError(null);
    setBusy(true);
    try {
      const role: Omit<Role, "id" | "created_at"> = {
        title: draft.title ?? "",
        jd_text: draft.jd_text ?? "",
        screening_questions: [],
        scoring_rubric: {
          ...((draft as any).scoring_rubric ?? {}),
          agentic: draft.agentic ?? {},
          scheduling: draft.scheduling ?? {},
        },
        cut_line: draft.cut_line ?? 60,
        interviewer_panel: [],
        status: "open",
        ctc_min_lpa: draft.ctc_min_lpa ?? null,
        ctc_max_lpa: draft.ctc_max_lpa ?? null,
        max_notice_days: draft.max_notice_days ?? null,
        location: draft.location ?? null,
        remote_policy: (draft.remote_policy as Role["remote_policy"]) ?? null,
        assignment_brief: draft.assignment_brief ?? null,
        assignment_instructions: draft.assignment_instructions ?? null,
        assignment_deadline_days: draft.assignment_deadline_days ?? 7,
        screening_modality: draft.screening_modality ?? "voice",
        pipeline_template: draft.pipeline_template ?? null,
        evaluation_spec: draft.evaluation_spec ?? null,
        company_context: draft.company_context ?? null,
      };
      const created = await api.post<Role>("/dashboard/roles", role);
      if (problemDoc) {
        const fd = new FormData();
        fd.append("file", problemDoc);
        const key = getDashboardKey();
        const resp = await fetch(
          `${(process.env.NEXT_PUBLIC_API_BASE_URL ?? "")}/dashboard/roles/${created.id}/problem-doc`,
          {
            method: "POST",
            headers: key ? { "X-Dashboard-Key": key } : {},
            body: fd,
          },
        );
        if (!resp.ok) {
          const b = await resp.json().catch(() => ({}));
          throw new Error(b.detail ?? `problem doc upload failed: ${resp.status}`);
        }
      }
      router.push(`/roles/${created.id}`);
    } catch (e: any) {
      setError(e?.message ?? "save failed");
      setBusy(false);
    }
  }

  function updateSection(name: SectionName, body: string) {
    const next = { ...sections, [name]: body };
    setDraft((d) => ({ ...d, jd_text: rebuildJd(next) }));
  }

  async function regenerateSection(name: SectionName, directive: string) {
    try {
      const result = await roleChat.rewriteSection({
        section: name,
        current_body: sections[name],
        role_context: {
          title: draft.title,
          ctc_min_lpa: draft.ctc_min_lpa,
          ctc_max_lpa: draft.ctc_max_lpa,
          location: draft.location,
          remote_policy: draft.remote_policy,
        },
        user_directive: directive || null,
      });
      updateSection(name, result.body);
    } catch (e: any) {
      setError(e?.message ?? "regenerate failed");
    }
  }

  async function generateLinkedIn() {
    if (!draft.title || !draft.jd_text) return;
    setLinkedinBusy(true);
    try {
      const post = await roleChat.linkedinPost({
        title: draft.title,
        jd_text: draft.jd_text,
        location: draft.location ?? null,
        remote_policy: draft.remote_policy ?? null,
        ctc_min_lpa: draft.ctc_min_lpa ?? null,
        ctc_max_lpa: draft.ctc_max_lpa ?? null,
        apply_url: null,
      });
      setLinkedin(post);
    } catch (e: any) {
      setError(e?.message ?? "linkedin failed");
    } finally {
      setLinkedinBusy(false);
    }
  }

  function copyLinkedIn() {
    if (!linkedin) return;
    navigator.clipboard.writeText(
      `${linkedin.post_text}\n\n${linkedin.hashtags.join(" ")}`,
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function reset() {
    setHistory([
      {
        role: "assistant",
        content: "Cleared. What role are we opening today?",
      },
    ]);
    setDraft({});
    setMissing([]);
    setQuickReplies([]);
    setReadyToSave(false);
    setLinkedin(null);
    setProblemDoc(null);
  }

  async function handleQuickReply(text: string) {
    if (busy) return;
    if (readyToSave && /^save it/i.test(text)) {
      await saveRole();
      return;
    }
    await send(text);
  }

  const showStarters = history.length <= 1 && !busy;
  const populatedCount = SECTIONS.filter((s) => sections[s]).length;

  return (
    <>
      <Topbar
        title="Create role"
        subtitle="intake call · live document on the right"
      />
      <div className="relative flex flex-1 flex-col overflow-hidden bg-muted/20">
        {/* Top status bar */}
        <div className="flex items-center justify-between gap-3 border-b border-border bg-background/70 px-6 py-2.5 backdrop-blur">
          <div className="flex items-center gap-2 min-w-0">
            <BrandAgentAvatar size={28} thinking={busy} />
            <p className="truncate text-sm font-bold">
              {draft.title || "GrabOn intake"}
            </p>
            {readyToSave ? (
              <span className="ml-1 rounded-full bg-success/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-success">
                ready
              </span>
            ) : (
              <span className="ml-1 rounded-full bg-warning/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-foreground">
                {populatedCount}/{SECTIONS.length} sections
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <RotateCcw className="h-3 w-3" /> new chat
            </button>
            <Link
              href="/roles/new/classic"
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <Settings2 className="h-3 w-3" /> classic form
            </Link>
          </div>
        </div>

        {/* Two-panel grid */}
        <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          {/* CHAT PANE */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden border-b border-border bg-card lg:border-b-0 lg:border-r">
            <div className="border-b border-border bg-muted/30 px-4 py-2">
              <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Intake conversation
              </p>
            </div>
            <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-4">
              {history.map((m, i) => (
                <Bubble key={i} message={m} />
              ))}
              {busy ? (
                <WorkingIndicator
                  lastUserMessage={history[history.length - 1]?.content ?? ""}
                />
              ) : null}

              {!busy && quickReplies.length > 0 ? (
                <div className="flex flex-wrap gap-1.5 pl-9">
                  {quickReplies.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => handleQuickReply(q)}
                      className="rounded-full border border-primary/40 bg-primary/5 px-3 py-1 text-[12px] font-medium text-primary transition hover:border-primary hover:bg-primary/10"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              ) : null}

              {showStarters ? (
                <div className="pt-2 space-y-2">
                  <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                    or start with one of these
                  </p>
                  {STARTERS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => send(s)}
                      className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-left text-[13px] text-foreground transition hover:border-primary/40 hover:bg-card hover:shadow-card"
                    >
                      {s}
                    </button>
                  ))}

                </div>
              ) : null}

              {error ? (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
                  {error}
                </div>
              ) : null}
            </div>

            {/* Composer */}
            <div className="border-t border-border bg-background px-3 py-3">
              <div className="flex items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-card focus-within:border-primary/50 focus-within:shadow-pop transition">
                <Textarea
                  ref={inputRef as any}
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder="Reply to the agent"
                  className="min-h-[40px] max-h-32 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 px-2 text-[14px]"
                />
                <Button
                  type="button"
                  size="icon"
                  onClick={() => send()}
                  disabled={!input.trim() || busy}
                  className="h-9 w-9 shrink-0 rounded-xl"
                >
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ArrowUp className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <p className="mt-1.5 text-center text-[10px] text-muted-foreground">
                Enter to send · Shift+Enter for newline
              </p>
            </div>
          </section>

          {/* DOC PANE */}
          <section className="flex h-full min-h-0 flex-col overflow-hidden bg-muted/20">
            <div className="flex items-center justify-between gap-3 border-b border-border bg-background/70 px-6 py-2 backdrop-blur">
              <div className="flex items-center gap-2 min-w-0">
                <FileText className="h-4 w-4 text-primary" />
                <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  Job description · live
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={generateLinkedIn}
                  disabled={!draft.title || !draft.jd_text || linkedinBusy}
                  className="gap-1.5 h-8"
                >
                  {linkedinBusy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Linkedin className="h-3.5 w-3.5" />
                  )}
                  LinkedIn post
                </Button>
                <Button
                  size="sm"
                  onClick={saveRole}
                  disabled={!readyToSave || busy}
                  className="gap-1.5 h-8"
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCheck className="h-3.5 w-3.5" />
                  )}
                  Save role
                </Button>
              </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="mx-auto w-full max-w-3xl px-6 py-6 space-y-4">
                {/* Header card */}
                <div className="rounded-2xl border border-border bg-card p-6 shadow-card">
                  <h1 className="text-3xl font-extrabold tracking-tight leading-tight">
                    {draft.title || (
                      <span className="text-muted-foreground/60">
                        Role title pending
                      </span>
                    )}
                  </h1>
                  <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
                    {draft.location ? (
                      <Pill icon={MapPin}>
                        {draft.location}
                        {draft.remote_policy ? ` · ${draft.remote_policy}` : ""}
                      </Pill>
                    ) : null}
                    {draft.ctc_min_lpa || draft.ctc_max_lpa ? (
                      <Pill icon={IndianRupee}>
                        {draft.ctc_min_lpa ?? "?"} – {draft.ctc_max_lpa ?? "?"}{" "}
                        LPA
                      </Pill>
                    ) : null}
                    {draft.max_notice_days ? (
                      <Pill>{draft.max_notice_days} days notice</Pill>
                    ) : null}
                    {problemDoc ? (
                      <Pill icon={FileText}>doc: {problemDoc.name}</Pill>
                    ) : draft.assignment_brief ? (
                      <Pill icon={FileText}>assignment brief set</Pill>
                    ) : null}
                  </div>
                </div>

                {/* Problem statement uploader -- shows when the agent
                    flags requires_problem_doc_upload, when the user
                    already picked a file, or after assignment_brief is
                    set so HR can attach a supporting doc. */}
                {(draft.requires_problem_doc_upload ||
                  problemDoc ||
                  draft.assignment_brief) ? (
                  <div className="rounded-2xl border border-primary/30 bg-primary/[0.04] p-4">
                    <div className="mb-2 flex items-center gap-2">
                      <FileText className="h-4 w-4 text-primary" />
                      <p className="text-sm font-bold">
                        Problem statement document
                      </p>
                      <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                        optional · sent with the assignment email
                      </span>
                    </div>
                    {problemDoc ? (
                      <div className="flex items-center justify-between rounded-md border border-primary/40 bg-primary/10 p-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <FileText className="h-4 w-4 text-primary shrink-0" />
                          <span className="font-mono text-sm truncate">
                            {problemDoc.name}
                          </span>
                          <span className="font-mono text-[11px] text-muted-foreground shrink-0">
                            {(problemDoc.size / 1024).toFixed(1)} KB
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setProblemDoc(null)}
                          className="text-muted-foreground hover:text-destructive"
                          aria-label="Remove document"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    ) : (
                      <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-primary/40 bg-card px-4 py-2 text-sm transition hover:border-primary hover:bg-primary/5">
                        <Upload className="h-3.5 w-3.5 text-primary" />
                        Upload .docx / .pdf
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
                  </div>
                ) : null}

                {/* Section cards */}
                {SECTIONS.map((name) => (
                  <SectionCard
                    key={name}
                    name={name}
                    body={sections[name]}
                    drafting={busy && !sections[name]}
                    onChange={(b) => updateSection(name, b)}
                    onRegenerate={(directive) => regenerateSection(name, directive)}
                  />
                ))}

                {linkedin ? (
                  <div className="rounded-2xl border border-info/30 bg-info/5 p-4">
                    <div className="flex items-center justify-between">
                      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-info">
                        LinkedIn post draft
                      </p>
                      <button
                        type="button"
                        onClick={copyLinkedIn}
                        className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-[11px] hover:bg-muted"
                      >
                        {copied ? (
                          <>
                            <CheckCheck className="h-3 w-3 text-success" /> copied
                          </>
                        ) : (
                          <>
                            <ClipboardCopy className="h-3 w-3" /> copy
                          </>
                        )}
                      </button>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap rounded-md bg-card p-3 text-[13px] leading-relaxed">
                      {linkedin.post_text}
                    </p>
                    <p className="mt-1 text-[11px] text-info">
                      {linkedin.hashtags.join(" ")}
                    </p>
                  </div>
                ) : null}

                {missing.length > 0 ? (
                  <div className="rounded-2xl border border-warning/40 bg-warning/10 p-4">
                    <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-foreground">
                      Still missing
                    </p>
                    <ul className="mt-1 list-disc pl-4 text-[12px]">
                      {missing.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Header pill
// ---------------------------------------------------------------------------

function Pill({
  icon: Icon,
  children,
}: {
  icon?: typeof MapPin;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 px-2 py-0.5 font-mono text-[11px] text-foreground">
      {Icon ? <Icon className="h-3 w-3 text-muted-foreground" /> : null}
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// SectionCard -- view / edit / AI rewrite states
// ---------------------------------------------------------------------------

function SectionCard({
  name,
  body,
  drafting,
  onChange,
  onRegenerate,
}: {
  name: SectionName;
  body: string;
  drafting: boolean;
  onChange: (body: string) => void;
  onRegenerate: (directive: string) => Promise<void> | void;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "rewrite">("view");
  const [draft, setDraftValue] = useState(body);
  const [directive, setDirective] = useState("");
  const [working, setWorking] = useState(false);

  useEffect(() => {
    setDraftValue(body);
  }, [body]);

  const empty = !body.trim();

  return (
    <div
      className={cn(
        "rounded-2xl border bg-card p-5 shadow-card transition",
        empty ? "border-dashed border-border" : "border-border hover:border-primary/30",
      )}
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-bold tracking-tight">{name}</h2>
          {drafting ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.12em] text-primary">
              <Loader2 className="h-3 w-3 animate-spin" />
              drafting
            </span>
          ) : empty ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
              not yet drafted
            </span>
          ) : null}
        </div>
        {!empty && mode === "view" ? (
          <div className="flex items-center gap-1">
            <IconBtn label="Edit" onClick={() => setMode("edit")} icon={Pencil} />
            <IconBtn
              label="Regenerate with AI"
              onClick={() => setMode("rewrite")}
              icon={Wand2}
            />
          </div>
        ) : null}
      </div>

      {mode === "view" ? (
        empty ? (
          <SectionSkeleton drafting={drafting} />
        ) : (
          <SectionBody body={body} />
        )
      ) : null}

      {mode === "edit" ? (
        <div className="space-y-2">
          <Textarea
            rows={Math.min(12, Math.max(5, draft.split("\n").length + 1))}
            value={draft}
            onChange={(e) => setDraftValue(e.target.value)}
            className="text-[14px] leading-relaxed"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setDraftValue(body);
                setMode("view");
              }}
            >
              <X className="mr-1 h-3.5 w-3.5" /> Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                onChange(draft);
                setMode("view");
              }}
            >
              <Save className="mr-1 h-3.5 w-3.5" /> Save section
            </Button>
          </div>
        </div>
      ) : null}

      {mode === "rewrite" ? (
        <div className="space-y-2">
          <p className="text-[12px] text-muted-foreground">
            Tell me what to change in this section. Leave empty to just
            regenerate from current context.
          </p>
          <Textarea
            rows={3}
            value={directive}
            onChange={(e) => setDirective(e.target.value)}
            placeholder='e.g. "make it shorter" · "add Kubernetes" · "drop the React mention"'
            className="text-[13px]"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={working}
              onClick={() => {
                setDirective("");
                setMode("view");
              }}
            >
              <X className="mr-1 h-3.5 w-3.5" /> Cancel
            </Button>
            <Button
              size="sm"
              disabled={working}
              onClick={async () => {
                setWorking(true);
                try {
                  await onRegenerate(directive);
                  setDirective("");
                  setMode("view");
                } finally {
                  setWorking(false);
                }
              }}
            >
              {working ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
              )}
              Rewrite with AI
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function IconBtn({
  label,
  onClick,
  icon: Icon,
}: {
  label: string;
  onClick: () => void;
  icon: typeof Pencil;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-primary transition"
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}

function SectionSkeleton({ drafting }: { drafting: boolean }) {
  return (
    <div className="space-y-2">
      <div className={cn("h-3 w-3/4 rounded", drafting ? "skeleton" : "bg-muted")} />
      <div className={cn("h-3 w-5/6 rounded", drafting ? "skeleton" : "bg-muted")} />
      <div className={cn("h-3 w-1/2 rounded", drafting ? "skeleton" : "bg-muted")} />
    </div>
  );
}

function SectionBody({ body }: { body: string }) {
  const blocks = useMemo(() => splitBlocks(body), [body]);
  return (
    <div className="space-y-3 text-[14.5px] leading-relaxed text-foreground">
      {blocks.map((b, i) => {
        if (b.kind === "ul") {
          return (
            <ul key={i} className="space-y-1.5 list-disc pl-5">
              {b.items.map((item, j) => (
                <li key={j} className="leading-relaxed">
                  {item}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {b.text}
          </p>
        );
      })}
    </div>
  );
}

type Block = { kind: "p"; text: string } | { kind: "ul"; items: string[] };

function splitBlocks(body: string): Block[] {
  const out: Block[] = [];
  const lines = body.split(/\r?\n/);
  let para: string[] = [];
  let bullets: string[] = [];
  const flushPara = () => {
    if (para.length) {
      out.push({ kind: "p", text: para.join(" ").trim() });
      para = [];
    }
  };
  const flushBullets = () => {
    if (bullets.length) {
      out.push({ kind: "ul", items: [...bullets] });
      bullets = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushBullets();
      continue;
    }
    const m = line.match(/^[-•*]\s+(.*)$/);
    if (m) {
      flushPara();
      bullets.push(m[1]);
    } else {
      flushBullets();
      para.push(line);
    }
  }
  flushPara();
  flushBullets();
  return out;
}

// ---------------------------------------------------------------------------
// Chat helpers (avatar / bubble / working indicator)
// ---------------------------------------------------------------------------

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end rise-in">
        <div className="max-w-[88%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-[13.5px] leading-relaxed text-primary-foreground shadow-card">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2.5 rise-in">
      <BrandAgentAvatar size={26} />
      <div className="flex-1 min-w-0">
        <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-foreground">
          {message.content}
        </p>
      </div>
    </div>
  );
}

function BrandAgentAvatar({
  size = 28,
  thinking = false,
}: {
  size?: number;
  thinking?: boolean;
}) {
  return (
    <span
      className="mt-0.5 inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
      aria-hidden
    >
      <Image
        src="/brand/icon/icon-on-light.svg"
        alt=""
        width={size}
        height={size}
        priority
        className={cn(
          "select-none transition",
          thinking && "animate-pulse",
        )}
      />
    </span>
  );
}

const GENERIC_STEPS = [
  { icon: Search, label: "Reading your message" },
  { icon: Brain, label: "Thinking through the role" },
  { icon: Database, label: "Pulling memory of past roles" },
  { icon: Wand2, label: "Filling sensible defaults" },
];
const JD_STEPS = [
  { icon: Search, label: "Reading the brief" },
  { icon: Brain, label: "Mapping must-haves vs nice-to-haves" },
  { icon: PenLine, label: "Drafting the JD" },
  { icon: Wand2, label: "Polishing tone for GrabOn voice" },
];

function pickSteps(text: string) {
  const s = text.toLowerCase();
  if (
    s.includes("jd") ||
    s.includes("description") ||
    s.includes("write") ||
    s.includes("draft") ||
    s.includes("must-have") ||
    s.includes("nice-to-have")
  ) {
    return JD_STEPS;
  }
  return GENERIC_STEPS;
}

function WorkingIndicator({ lastUserMessage }: { lastUserMessage: string }) {
  const steps = useMemo(() => pickSteps(lastUserMessage), [lastUserMessage]);
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    setIdx(0);
    const t = setInterval(() => setIdx((i) => (i + 1) % steps.length), 1100);
    return () => clearInterval(t);
  }, [steps]);
  const Step = steps[idx];
  return (
    <div className="flex items-start gap-2.5 rise-in">
      <BrandAgentAvatar size={26} thinking />
      <div className="inline-flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-3 py-1.5 shadow-card">
        <Step.icon className="h-3.5 w-3.5 text-primary animate-pulse" />
        <span className="text-[12.5px] font-medium text-foreground">
          {Step.label}
        </span>
        <span className="ml-1 inline-flex items-center gap-0.5">
          <Dot delay="0ms" />
          <Dot delay="150ms" />
          <Dot delay="300ms" />
        </span>
      </div>
    </div>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="inline-block h-1.5 w-1.5 rounded-full bg-primary/60 animate-bounce"
      style={{ animationDelay: delay }}
    />
  );
}

