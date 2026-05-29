"use client";

import { useEffect, useRef } from "react";
import { Loader2, Sparkles, User, Wrench } from "lucide-react";
import { MarkdownLite } from "@/components/markdown-lite";
import { cn } from "@/lib/utils";
import { AttachmentRenderer } from "./Attachments";
import type { RecruiterMessage } from "@/lib/useRecruiterChat";

function PulseAvatar({ size = 28 }: { size?: number }) {
  return (
    <div
      className="pulse-glow-ring mt-1 flex shrink-0 items-center justify-center rounded-full bg-brand-green text-white"
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
  trigger_chat_invite: "Sending chat invite",
  audit_tail: "Reading audit log",
  read_audit: "Reading audit log",
  search_candidates: "Searching candidates",
  smart_defaults_for_role: "Inferring role defaults",
  create_role: "Drafting role",
  update_role: "Updating role",
  archive_role: "Archiving role",
  set_role_assignment_brief: "Setting assignment brief",
  override_stage: "Overriding stage",
  send_custom_email: "Drafting email",
  add_candidate_note: "Adding note",
  schedule_interview: "Scheduling interview",
  propose_slots: "Finding interview slots",
  set_panel_member: "Updating panel",
  parse_attachment: "Parsing attachment",
  remember: "Remembering",
  recall: "Recalling memory",
  update_setting: "Updating setting",
  list_meetings: "Reading meetings",
  list_voice_calls: "Reading voice calls",
  get_journey_report: "Reading journey",
};

const PROMPT_TILES: { label: string; query: string }[] = [
  { label: "Show me the pipeline", query: "Show me the pipeline overview." },
  { label: "Who's stuck?", query: "Which applications are stuck?" },
  { label: "Recent applicants", query: "Who applied this week?" },
  { label: "Open roles", query: "List open roles." },
];

const TOOL_LABELS = TOOL_LABELS_FULL;

function ToolRow({
  msg,
  conversationId,
  dispatch,
}: {
  msg: RecruiterMessage;
  conversationId?: string | null;
  dispatch?: (m: string) => void;
}) {
  const label = msg.toolName ? TOOL_LABELS[msg.toolName] || msg.toolName : "Working";
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted">
          {msg.streaming ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Wrench className="h-3 w-3" />
          )}
        </span>
        <span className="font-mono">{label}</span>
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
          msg.pending && "opacity-70",
        )}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{msg.content}</div>
        ) : (
          <div className="-my-2">
            <MarkdownLite source={msg.content || ""} />
            {msg.streaming && (
              <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-foreground/60 align-middle" />
            )}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="mt-3 space-y-2">
                {msg.attachments.map((a, i) => (
                  <AttachmentRenderer key={i} att={a} conversationId={conversationId ?? null} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  );
}

interface Props {
  messages: RecruiterMessage[];
  isThinking: boolean;
  emptyHint?: string;
  conversationId?: string | null;
  /** Sends a templated message to Pulse when card actions are clicked. */
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
          <p className="mx-auto max-w-lg text-sm text-muted-foreground">
            Your autonomous hiring partner. Tell me what you want, I&apos;ll draft + execute.
            Drop a JD or resume, ask for the pipeline, or send an invite. I act, then confirm.
          </p>
        </div>
        {dispatch && (
          <div className="grid w-full max-w-2xl gap-2 sm:grid-cols-2">
            {PROMPT_TILES.map((t) => (
              <button
                key={t.label}
                onClick={() => dispatch(t.query)}
                className="group rounded-xl border border-border bg-card/70 px-4 py-3 text-left text-sm shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:bg-card hover:shadow-card"
              >
                <div className="font-semibold">{t.label}</div>
                <div className="mt-0.5 text-[11px] font-mono text-muted-foreground group-hover:text-foreground/70">
                  {t.query}
                </div>
              </button>
            ))}
          </div>
        )}
        <div className="text-[11px] text-muted-foreground">
          Type <span className="font-mono">/</span> for slash commands · Drop files to attach · Tab to autocomplete
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} conversationId={conversationId ?? null} dispatch={dispatch} />
      ))}
      {isThinking && (
        <div className="flex items-center gap-2 pl-10 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> thinking…
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
