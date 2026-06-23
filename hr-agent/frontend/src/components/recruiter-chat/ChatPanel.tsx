"use client";

import { useEffect, useRef } from "react";
import { BarChart3, Bot, Briefcase, Clock, Loader2, Plus, Search, Sparkles, User, Wrench } from "lucide-react";
import { MarkdownLite } from "@/components/markdown-lite";
import { cn } from "@/lib/utils";
import { AttachmentRenderer } from "./Attachments";
import type { RecruiterMessage } from "@/lib/useRecruiterChat";

// The @-mention inserts a hidden "(application_id: <uuid>)" marker so Pulse can
// pass the id straight to its tools. Recruiters shouldn't see that raw id in
// their own message — strip it for display only (the sent text still has it).
function stripInternalTokens(text: string): string {
  return text.replace(/\s*\(application_id:\s*[^)]+\)/gi, "");
}

function PulseAvatar({ size = 28 }: { size?: number }) {
  return (
    <div
      className="pulse-glow-ring mt-0.5 flex shrink-0 items-center justify-center rounded-full bg-brand-green text-white shadow-sm"
      style={{ height: size, width: size }}
    >
      <Sparkles className="h-3.5 w-3.5" />
    </div>
  );
}

const TOOL_LABELS_FULL: Record<string, string> = {
  list_candidates: "Looking up candidates",
  get_candidate: "Reading candidate detail",
  list_roles: "Reading roles",
  pipeline_metrics: "Computing pipeline metrics",
  metrics_period: "Computing metrics",
  stuck_applications: "Finding stuck applications",
  audit_tail: "Reading audit log",
  read_audit: "Reading audit log",
  search_candidates: "Searching candidates",
  smart_defaults_for_role: "Inferring role defaults",
  create_role: "Drafting role",
  create_role_with_assignment: "Drafting role + assignment",
  update_role: "Updating role",
  archive_role: "Archiving role",
  set_role_assignment_brief: "Setting assignment brief",
  generate_assignment_for_role: "Generating assignment",
  override_stage: "Overriding stage",
  send_custom_email: "Drafting email",
  add_candidate_note: "Adding note",
  schedule_interview: "Scheduling interview",
  propose_slots: "Finding interview slots",
  // set_panel_member: "Updating panel",
  parse_attachment: "Parsing attachment",
  remember: "Remembering",
  recall: "Recalling memory",
  update_setting: "Updating setting",
  list_meetings: "Reading meetings",
  list_voice_calls: "Reading voice calls",
  get_journey_report: "Reading journey",
  draft_linkedin_post: "Drafting LinkedIn post",
  publish_linkedin_post: "Publishing to LinkedIn",
};

const PROMPT_TILES: { label: string; query: string; icon: React.ReactNode }[] = [
  { label: "Pipeline overview", query: "Show me the pipeline overview.", icon: <BarChart3 className="h-4 w-4" /> },
  { label: "Stuck applications", query: "Which applications are stuck?", icon: <Clock className="h-4 w-4" /> },
  { label: "Recent applicants", query: "Who applied this week?", icon: <User className="h-4 w-4" /> },
  { label: "Open roles", query: "List open roles.", icon: <Briefcase className="h-4 w-4" /> },
  { label: "Create a role", query: "Create a role for ", icon: <Plus className="h-4 w-4" /> },
  { label: "Search candidates", query: "Find candidates for ", icon: <Search className="h-4 w-4" /> },
];

function ToolRow({
  msg,
  conversationId,
  dispatch,
}: {
  msg: RecruiterMessage;
  conversationId?: string | null;
  dispatch?: (m: string) => void;
}) {
  const label = msg.toolName ? TOOL_LABELS_FULL[msg.toolName] || msg.toolName : "Working";
  const isStreaming = msg.streaming;
  return (
    <div className="space-y-2 pl-10">
      <div className="flex items-center gap-2 text-xs">
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full",
            isStreaming ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
          )}
        >
          {isStreaming ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Wrench className="h-3 w-3" />
          )}
        </span>
        <span className={cn("font-mono text-[12px]", isStreaming ? "text-foreground" : "text-muted-foreground")}>
          {label}
        </span>
      </div>
      {msg.attachments?.map((a, i) => (
        <div key={i} className="ml-7">
          <AttachmentRenderer att={a} conversationId={conversationId ?? null} dispatch={dispatch} />
        </div>
      ))}
    </div>
  );
}

function MessageBubble({
  msg,
  conversationId,
  dispatch,
}: {
  msg: RecruiterMessage;
  conversationId?: string | null;
  dispatch?: (m: string) => void;
}) {
  if (msg.role === "tool")
    return <ToolRow msg={msg} conversationId={conversationId} dispatch={dispatch} />;

  const isUser = msg.role === "user";

  return (
    <div className={cn("flex w-full gap-3", isUser ? "justify-end" : "justify-start")}>
      {!isUser && <PulseAvatar />}
      <div
        className={cn(
          "max-w-[78%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isUser ? "pulse-bubble-user" : "pulse-bubble-bot",
          msg.pending && "opacity-60",
        )}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{stripInternalTokens(msg.content)}</div>
        ) : (
          <div className="-my-2">
            <MarkdownLite source={msg.content || ""} />
            {msg.streaming && (
              <span className="ml-0.5 inline-block h-4 w-[3px] animate-pulse rounded-sm bg-primary/70 align-middle" />
            )}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="mt-3 space-y-2">
                {msg.attachments.map((a, i) => (
                  <AttachmentRenderer
                    key={i}
                    att={a}
                    conversationId={conversationId ?? null}
                    dispatch={dispatch}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <User className="h-3.5 w-3.5" />
        </div>
      )}
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex items-start gap-3 pl-0">
      <PulseAvatar />
      <div className="flex items-center gap-2 rounded-2xl pulse-bubble-bot px-4 py-3">
        <div className="flex gap-1">
          <span className="h-2 w-2 animate-bounce rounded-full bg-primary/50" style={{ animationDelay: "0ms" }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-primary/50" style={{ animationDelay: "150ms" }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-primary/50" style={{ animationDelay: "300ms" }} />
        </div>
        <span className="ml-1 text-xs text-muted-foreground">thinking</span>
      </div>
    </div>
  );
}

interface Props {
  messages: RecruiterMessage[];
  isThinking: boolean;
  emptyHint?: string;
  conversationId?: string | null;
  dispatch?: (msg: string) => void;
}

export function ChatMessages({
  messages,
  isThinking,
  emptyHint,
  conversationId,
  dispatch,
}: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isThinking]);

  if (messages.length === 0) {
    return (
      <div className="mx-auto flex h-full w-full max-w-3xl flex-col items-center justify-center gap-6 px-4 py-8 text-center">
        <div className="pulse-glow-ring relative flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-green to-[hsl(85_100%_28%)] text-white shadow-pop">
          <Sparkles className="h-7 w-7" />
        </div>
        <div className="space-y-2">
          <h2 className="bg-gradient-to-br from-foreground to-foreground/70 bg-clip-text text-3xl font-extrabold tracking-tight text-transparent">
            Hi, I&apos;m Pulse.
          </h2>
          <p className="mx-auto max-w-lg text-sm text-muted-foreground leading-relaxed">
            Your autonomous hiring partner. Tell me what you need and I&apos;ll draft it
            instantly. You can edit everything before confirming.
          </p>
        </div>
        {dispatch && (
          <div className="grid w-full max-w-2xl gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {PROMPT_TILES.map((t) => (
              <button
                key={t.label}
                onClick={() => dispatch(t.query)}
                className="group flex items-start gap-3 rounded-xl border border-border bg-card/70 px-4 py-3 text-left text-sm shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:bg-card hover:shadow-card"
              >
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">{t.icon}</span>
                <div className="min-w-0">
                  <div className="font-semibold">{t.label}</div>
                  <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground group-hover:text-foreground/70">
                    {t.query}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
        <div className="text-[11px] text-muted-foreground">
          Tip: I&apos;ll ask quick clarifications only when needed. All drafts are editable before confirm.
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} conversationId={conversationId ?? null} dispatch={dispatch} />
      ))}
      {isThinking && <ThinkingIndicator />}
      <div ref={endRef} />
    </div>
  );
}
