"use client";

/**
 * Recruiter-side ChatGPT-style hook.
 *
 * Manages:
 *   - conversation list
 *   - active conversation messages
 *   - SSE stream for the active conversation
 *   - send / new / archive / rename
 *
 * Auth: ``X-Dashboard-Key`` header on POST/GET/DELETE/PATCH; ``?key=...``
 * query param on the SSE endpoint (EventSource limitation).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getDashboardKey } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type RecruiterMessageRole = "user" | "assistant" | "tool" | "system";

export interface ToolCall {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
}

// An assistant turn is a single bubble made of ordered parts: streamed text
// segments interleaved with the tool steps that ran mid-turn. Rendering the
// blocks in order keeps text + tool activity inside one consistent bubble
// instead of scattering them across separate rows.
export type TurnBlock =
  | { kind: "text"; text: string }
  | {
      kind: "tool";
      toolName: string;
      streaming: boolean;
      attachments?: Attachment[];
      preview?: string;
    };

export interface Attachment {
  kind: string; // candidate-list | role-list | candidate-detail | metrics | stuck-list | audit-list | action-result
  data?: unknown;
  items?: unknown[];
  raw?: unknown;
}

export interface ArtifactData {
  id: string;
  conversation_id?: string;
  type: string; // role_draft
  status: string; // draft | applied | dismissed
  title: string | null;
  content: Record<string, unknown>;
  version: number;
}

export interface QuickReplyQuestion {
  question: string;
  options: string[];
  allowCustom: boolean;
  // When true, the recruiter can select several options for this question.
  multiSelect: boolean;
}

export interface QuickRepliesData {
  toolUseId: string;
  questions: QuickReplyQuestion[];
}

export interface RecruiterMessage {
  id: string;
  role: RecruiterMessageRole;
  content: string;
  pending?: boolean;
  streaming?: boolean;
  toolCalls?: ToolCall[];
  toolName?: string | null;
  toolResultPreview?: string;
  attachments?: Attachment[];
  // Ordered parts of an assistant turn (text + tool steps). When present the
  // bubble renders these; `content` is kept in sync as a plain-text fallback
  // for non-turn messages (hydrated history, apply follow-ups).
  blocks?: TurnBlock[];
  createdAt: number;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

interface ConversationDetailRaw {
  id: string;
  title: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  messages: Array<{
    sequence: number;
    role: string;
    content: string | null;
    tool_name: string | null;
    tool_calls: ToolCall[] | null;
    tool_result: unknown;
    attachments: Attachment[] | null;
    created_at: string;
  }>;
}

export interface UseRecruiterChatReturn {
  conversations: ConversationSummary[];
  conversationId: string | null;
  messages: RecruiterMessage[];
  isThinking: boolean;
  // Loader copy for the thinking indicator (tool-specific or generic filler).
  thinkingLabel: string;
  isStreaming: boolean;
  connected: boolean;
  error: string | null;
  inputDisabled: boolean;
  // Pending quick-reply questions rendered inside the input bar (null when none).
  pendingQuickReplies: QuickRepliesData | null;
  submitQuickReplies: (combined: string) => Promise<void>;
  selectConversation: (id: string) => void;
  newConversation: () => Promise<string | null>;
  archiveConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  send: (text: string, attachments?: { file_ref: string; filename: string; size: number }[]) => Promise<void>;
  sendToolResult: (toolUseId: string, content: string) => Promise<void>;
  stop: () => Promise<void>;
  refreshList: () => Promise<void>;
  // Artifact side-panel (editable structured output, e.g. role drafts).
  activeArtifact: ArtifactData | null;
  artifactOpen: boolean;
  openArtifact: () => void;
  closeArtifact: () => void;
  saveArtifact: (content: Record<string, unknown>) => Promise<void>;
  applyArtifact: () => Promise<{ ok: boolean; role_url?: string } | null>;
}

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function authHeader(): Record<string, string> {
  const k = getDashboardKey();
  return k ? { "X-Dashboard-Key": k } : {};
}

function hydrate(detail: ConversationDetailRaw): RecruiterMessage[] {
  const out: RecruiterMessage[] = [];
  // The current assistant turn being assembled. A turn spans every assistant
  // + tool message between two user/system boundaries, so reloaded history
  // renders as the same single bubble the live stream produces.
  let turn: RecruiterMessage | null = null;
  const flush = () => {
    if (turn && turn.blocks && turn.blocks.length > 0) out.push(turn);
    turn = null;
  };
  const ensure = (seq: number, createdAt: number): RecruiterMessage => {
    if (!turn) {
      turn = { id: `srv-${seq}`, role: "assistant", content: "", blocks: [], createdAt };
    }
    return turn;
  };

  for (const m of detail.messages) {
    const createdAt = new Date(m.created_at).getTime();
    if (m.role === "assistant") {
      const t = ensure(m.sequence, createdAt);
      const text = (m.content || "").trim();
      if (text) t.blocks!.push({ kind: "text", text: m.content || "" });
      if (m.tool_calls) t.toolCalls = m.tool_calls;
    } else if (m.role === "tool") {
      // Role drafts render in the artifact panel, not as an inline tool step.
      if (m.tool_name === "propose_role_draft") continue;
      const t = ensure(m.sequence, createdAt);
      t.blocks!.push({
        kind: "tool",
        toolName: m.tool_name || "",
        streaming: false,
        attachments: m.attachments || undefined,
      });
    } else if (m.role === "user") {
      flush();
      out.push({
        id: `srv-${m.sequence}`,
        role: "user",
        content: m.content || "",
        attachments: m.attachments || undefined,
        createdAt,
      });
    } else if (m.role === "system") {
      flush();
      out.push({
        id: `srv-${m.sequence}`,
        role: "system",
        content: m.content || "",
        attachments: m.attachments || undefined,
        createdAt,
      });
    }
  }
  flush();
  return out;
}

export function useRecruiterChat(): UseRecruiterChatReturn {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<RecruiterMessage[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingLabel, setThinkingLabel] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [pendingQuickReplies, setPendingQuickReplies] = useState<QuickRepliesData | null>(null);
  const [activeArtifact, setActiveArtifact] = useState<ArtifactData | null>(null);
  const [artifactOpen, setArtifactOpen] = useState(false);

  const sourceRef = useRef<EventSource | null>(null);
  const closingRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const activeAssistantIdRef = useRef<string | null>(null);

  // Create (or return) the current assistant turn — one bubble that will hold
  // this turn's text + tool steps. Reused across all events until the turn is
  // finalized on `done`/`error`.
  const ensureTurn = useCallback(() => {
    if (activeAssistantIdRef.current) return activeAssistantIdRef.current;
    const id = uid();
    activeAssistantIdRef.current = id;
    setMessages((prev) => [
      ...prev,
      { id, role: "assistant", content: "", blocks: [], streaming: true, createdAt: Date.now() },
    ]);
    return id;
  }, []);

  // Mutate the current turn's block list in place.
  const patchTurn = useCallback(
    (fn: (blocks: TurnBlock[]) => TurnBlock[]) => {
      const id = ensureTurn();
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id ? { ...m, blocks: fn(m.blocks ? [...m.blocks] : []) } : m,
        ),
      );
    },
    [ensureTurn],
  );

  const finalizeTurn = useCallback(() => {
    const id = activeAssistantIdRef.current;
    activeAssistantIdRef.current = null;
    if (!id) return;
    setMessages((prev) => {
      const m = prev.find((x) => x.id === id);
      if (!m) return prev;
      // Drop a stray empty bubble (e.g. the thinking placeholder for a turn
      // that produced no text and no tool steps — a give_choice ask, or a
      // role-draft that only opened the side panel).
      const empty =
        (!m.content || !m.content.trim()) && (!m.blocks || m.blocks.length === 0);
      if (empty) return prev.filter((x) => x.id !== id);
      return prev.map((x) =>
        x.id === id
          ? {
              ...x,
              streaming: false,
              blocks: (x.blocks || []).map((b) =>
                b.kind === "tool" ? { ...b, streaming: false } : b,
              ),
            }
          : x,
      );
    });
  }, []);

  // ---- API helpers ----
  const refreshList = useCallback(async () => {
    try {
      const res = await fetch(`${BASE}/v2/recruiter-chat/conversations`, {
        headers: authHeader(),
        cache: "no-store",
      });
      if (!res.ok) return;
      const data = (await res.json()) as ConversationSummary[];
      setConversations(data);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchActiveArtifact = useCallback(async (id: string) => {
    try {
      const res = await fetch(
        `${BASE}/v2/recruiter-chat/conversations/${id}/artifact`,
        { headers: authHeader(), cache: "no-store" },
      );
      if (!res.ok) return;
      const data = (await res.json()) as { artifact: ArtifactData | null };
      setActiveArtifact(data.artifact);
    } catch {
      /* ignore */
    }
  }, []);

  const loadConversation = useCallback(
    async (id: string) => {
      activeAssistantIdRef.current = null;
      setMessages([]);
      setActiveArtifact(null);
      setArtifactOpen(false);
      setPendingQuickReplies(null);
      try {
        const res = await fetch(`${BASE}/v2/recruiter-chat/conversations/${id}`, {
          headers: authHeader(),
          cache: "no-store",
        });
        if (!res.ok) return;
        const detail = (await res.json()) as ConversationDetailRaw;
        setMessages(hydrate(detail));
        // Make any existing draft available behind the icon (do not auto-open
        // on reload; only the live `artifact` event auto-opens).
        void fetchActiveArtifact(id);
      } catch {
        /* ignore */
      }
    },
    [fetchActiveArtifact],
  );

  const selectConversation = useCallback(
    (id: string) => {
      setConversationId(id);
      void loadConversation(id);
    },
    [loadConversation],
  );

  const newConversation = useCallback(async (): Promise<string | null> => {
    const res = await fetch(`${BASE}/v2/recruiter-chat/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      setError(`Failed to create conversation (HTTP ${res.status})`);
      return null;
    }
    const c = (await res.json()) as ConversationSummary;
    setConversations((prev) => [c, ...prev]);
    setConversationId(c.id);
    setMessages([]);
    setActiveArtifact(null);
    setArtifactOpen(false);
    activeAssistantIdRef.current = null;
    return c.id;
  }, []);

  const archiveConversation = useCallback(
    async (id: string) => {
      const res = await fetch(`${BASE}/v2/recruiter-chat/conversations/${id}`, {
        method: "DELETE",
        headers: authHeader(),
      });
      if (!res.ok) return;
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (conversationId === id) {
        setConversationId(null);
        setMessages([]);
      }
    },
    [conversationId],
  );

  const renameConversation = useCallback(async (id: string, title: string) => {
    await fetch(`${BASE}/v2/recruiter-chat/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify({ title }),
    });
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c)),
    );
  }, []);

  // ---- SSE wiring (per active conversation) ----
  useEffect(() => {
    if (!conversationId) return;
    closingRef.current = false;

    const open = () => {
      const k = getDashboardKey() || "";
      const url = `${BASE}/v2/recruiter-chat/conversations/${conversationId}/stream?key=${encodeURIComponent(k)}`;
      const es = new EventSource(url);
      sourceRef.current = es;
      es.onopen = () => {
        setConnected(true);
        setError(null);
        reconnectAttemptRef.current = 0;
      };
      es.onmessage = (ev) => {
        let payload: { type: string; [k: string]: unknown };
        try {
          payload = JSON.parse(ev.data);
        } catch {
          return;
        }
        switch (payload.type) {
          case "thinking":
            setThinkingLabel((payload.label as string) || "");
            setIsThinking(true);
            // Materialize the turn bubble now so the thinking dots render
            // inside the same bubble the text/tools will fill.
            ensureTurn();
            break;
          case "tool_call": {
            // Role-draft writes surface in the artifact panel, not as a tool row.
            if ((payload.name as string) === "propose_role_draft") break;
            // give_choice: render the options inside the input bar (composer),
            // not as a chat row. Supports a multi-question array; tolerates the
            // legacy single-question shape for back-compat.
            if ((payload.name as string) === "give_choice") {
              setIsThinking(false);
              setIsStreaming(false);
              // The agent is asking, not answering — close out the (usually
              // empty) turn bubble so it doesn't linger with spinning dots
              // while the recruiter picks a chip.
              finalizeTurn();
              const args = (payload.arguments as Record<string, unknown>) || {};
              const rawQs = Array.isArray(args.questions)
                ? (args.questions as Record<string, unknown>[])
                : args.question
                  ? [
                      {
                        question: args.question,
                        options: args.options,
                        allow_custom: args.allow_custom,
                        multi_select: args.multi_select,
                      },
                    ]
                  : [];
              const questions = rawQs.map((q) => ({
                question: (q.question as string) || "",
                options: (q.options as string[]) || [],
                allowCustom: (q.allow_custom as boolean) ?? true,
                multiSelect: (q.multi_select as boolean) ?? false,
              }));
              if (questions.length > 0) {
                setPendingQuickReplies({
                  toolUseId: (payload.id as string) || "",
                  questions,
                });
              }
              break;
            }
            // Append a tool step to the current turn bubble.
            patchTurn((blocks) => [
              ...blocks,
              { kind: "tool", toolName: (payload.name as string) || "tool", streaming: true },
            ]);
            break;
          }
          case "tool_result": {
            // Resolve the last still-streaming tool step in the current turn.
            const name = (payload.name as string) || "";
            patchTurn((blocks) => {
              const next = [...blocks];
              for (let i = next.length - 1; i >= 0; i--) {
                const b = next[i];
                if (b.kind === "tool" && b.streaming && (!name || b.toolName === name)) {
                  next[i] = { ...b, streaming: false, preview: payload.preview as string | undefined };
                  break;
                }
              }
              return next;
            });
            break;
          }
          case "attachment": {
            // The artifact marker is handled by the dedicated `artifact` event
            // + the side panel; never render it inline.
            if (((payload.kind as string) || "") === "artifact") break;
            const att: Attachment = {
              kind: (payload.kind as string) || "raw",
              data: payload.data,
              raw: payload.raw,
            };
            // Proactive nudges carry their own text body (e.g. reschedule
            // request + suggested slots). Render them as a standalone system
            // message so the recruiter sees the message AND the card.
            if (payload._nudge) {
              setMessages((prev) => [
                ...prev,
                {
                  id: uid(),
                  role: "system",
                  content: (payload.content as string) || "",
                  attachments: [att],
                  createdAt: Date.now(),
                },
              ]);
              break;
            }
            // Attach to the last tool step of the current turn; if there's no
            // tool step yet, add one to carry the attachment.
            patchTurn((blocks) => {
              const next = [...blocks];
              for (let i = next.length - 1; i >= 0; i--) {
                const b = next[i];
                if (b.kind === "tool") {
                  next[i] = { ...b, attachments: [...(b.attachments || []), att] };
                  return next;
                }
              }
              next.push({ kind: "tool", toolName: "", streaming: false, attachments: [att] });
              return next;
            });
            break;
          }
          case "artifact": {
            // Structured editable output (role draft). Auto-open the panel.
            const art = payload.artifact as ArtifactData | undefined;
            if (art) {
              setActiveArtifact(art);
              setArtifactOpen(true);
            }
            setIsThinking(false);
            break;
          }
          case "token": {
            setIsThinking(false);
            setIsStreaming(true);
            const delta = (payload.delta as string) || "";
            // Guard empty deltas so we never materialize a blank bubble.
            if (!delta) break;
            const id = ensureTurn();
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== id) return m;
                const blocks = m.blocks ? [...m.blocks] : [];
                const last = blocks[blocks.length - 1];
                if (last && last.kind === "text") {
                  // Continue the current text run.
                  blocks[blocks.length - 1] = { ...last, text: last.text + delta };
                } else {
                  // Text after a tool step starts a fresh run, so tools and
                  // text stay in the order they actually happened.
                  blocks.push({ kind: "text", text: delta });
                }
                return { ...m, content: m.content + delta, blocks };
              }),
            );
            break;
          }
          case "done": {
            finalizeTurn();
            setIsStreaming(false);
            setIsThinking(false);
            // Don't re-enable the input when waiting for quick-reply chip click.
            if (!(payload.awaiting_quick_reply as boolean)) {
              setInputDisabled(false);
            }
            // Drop pending flag from optimistic user msgs.
            setMessages((prev) =>
              prev.map((m) => (m.pending ? { ...m, pending: false } : m)),
            );
            void refreshList();
            break;
          }
          case "error": {
            const msg = (payload.message as string) || "stream_error";
            setError(msg);
            finalizeTurn();
            setIsStreaming(false);
            setIsThinking(false);
            break;
          }
        }
      };
      es.onerror = () => {
        setConnected(false);
        if (closingRef.current) return;
        try {
          es.close();
        } catch {
          /* ignore */
        }
        const attempt = reconnectAttemptRef.current + 1;
        reconnectAttemptRef.current = attempt;
        const delay = Math.min(15000, 500 * 2 ** Math.min(attempt, 5));
        setTimeout(() => {
          if (!closingRef.current) open();
        }, delay);
      };
    };

    open();

    return () => {
      closingRef.current = true;
      if (sourceRef.current) {
        try {
          sourceRef.current.close();
        } catch {
          /* ignore */
        }
        sourceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // ---- Initial list load ----
  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const sendToolResult = useCallback(
    async (toolUseId: string, content: string) => {
      const convId = conversationId;
      if (!convId) return;
      await fetch(
        `${BASE}/v2/recruiter-chat/conversations/${convId}/tool-result`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeader() },
          body: JSON.stringify({ tool_use_id: toolUseId, content }),
        },
      );
    },
    [conversationId],
  );

  // Submit the recruiter's answers to the pending quick-reply questions: echo
  // them as a user message, clear the input-bar options, and wake the agent.
  const submitQuickReplies = useCallback(
    async (combined: string) => {
      const qr = pendingQuickReplies;
      if (!qr) return;
      setPendingQuickReplies(null);
      setMessages((prev) => [
        ...prev,
        { id: uid(), role: "user", content: combined, createdAt: Date.now() },
      ]);
      setIsThinking(true);
      await sendToolResult(qr.toolUseId, combined);
    },
    [pendingQuickReplies, sendToolResult],
  );

  const stop = useCallback(async () => {
    if (!conversationId) return;
    await fetch(`${BASE}/v2/recruiter-chat/conversations/${conversationId}/cancel`, {
      method: "POST",
      headers: authHeader(),
    });
  }, [conversationId]);

  const send = useCallback(
    async (
      text: string,
      attachments?: { file_ref: string; filename: string; size: number }[],
    ) => {
      const trimmed = text.trim();
      // If user only attached files (no text), tell Pulse to parse them.
      const finalText =
        trimmed ||
        (attachments && attachments.length > 0
          ? `I attached ${attachments.length} file${attachments.length === 1 ? "" : "s"}. Parse with parse_attachment using these file_refs: ${attachments.map((a) => a.file_ref).join(", ")}.`
          : "");
      if (!finalText) return;
      let convId = conversationId;
      // Auto-create on first send if none selected. Use the returned id
      // directly -- relying on the conversationId state setter would race
      // against the POST below since setState is async.
      let waitForStream = false;
      if (!convId) {
        const created = await newConversation();
        if (!created) return;
        convId = created;
        waitForStream = true;
      }
      // Give the SSE useEffect a tick to mount the EventSource for the new
      // conversation BEFORE we POST the message. Without this, the first
      // turn's events can race with the still-connecting stream and the
      // user sees no reply until they send a second message.
      if (waitForStream) {
        await new Promise((r) => setTimeout(r, 700));
      }
      const id = uid();
      setMessages((prev) => [
        ...prev,
        { id, role: "user", content: finalText, pending: true, createdAt: Date.now() },
      ]);
      setIsThinking(true);
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 15000);
      try {
        const res = await fetch(
          `${BASE}/v2/recruiter-chat/conversations/${convId}/messages`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeader() },
            body: JSON.stringify({ content: finalText }),
            signal: controller.signal,
          },
        );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `HTTP ${res.status}`);
        }
      } catch (e) {
        const message =
          e instanceof DOMException && e.name === "AbortError"
            ? "send_timeout"
            : e instanceof Error
              ? e.message
              : "send_failed";
        setError(message);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === id ? { ...m, pending: false, content: m.content + " · failed" } : m,
          ),
        );
        setIsThinking(false);
      } finally {
        window.clearTimeout(timer);
      }
    },
    [conversationId, conversations, newConversation, refreshList],
  );

  const openArtifact = useCallback(() => setArtifactOpen(true), []);
  const closeArtifact = useCallback(() => setArtifactOpen(false), []);

  const saveArtifact = useCallback(
    async (content: Record<string, unknown>) => {
      const art = activeArtifact;
      if (!art) return;
      try {
        const res = await fetch(`${BASE}/v2/recruiter-chat/artifacts/${art.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", ...authHeader() },
          body: JSON.stringify({ content }),
        });
        if (!res.ok) return;
        const data = (await res.json()) as { artifact: ArtifactData };
        setActiveArtifact(data.artifact);
      } catch {
        /* ignore */
      }
    },
    [activeArtifact],
  );

  const applyArtifact = useCallback(async (): Promise<{ ok: boolean; role_url?: string } | null> => {
    const art = activeArtifact;
    if (!art) return null;
    try {
      const res = await fetch(`${BASE}/v2/recruiter-chat/artifacts/${art.id}/apply`, {
        method: "POST",
        headers: authHeader(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail ?? `apply_failed (HTTP ${res.status})`);
        return null;
      }
      setActiveArtifact((prev) => (prev ? { ...prev, status: "applied" } : prev));
      // Surface the post-apply follow-up (LinkedIn offer + assignment status) in
      // the chat. The apply endpoint runs no agent turn, so without this the
      // conversational LinkedIn offer never appears.
      if (data.follow_up) {
        setMessages((prev) => [
          ...prev,
          {
            id: `apply-${Date.now()}`,
            role: "assistant",
            content: data.follow_up as string,
            createdAt: Date.now(),
          },
        ]);
      }
      void refreshList();
      return { ok: true, role_url: data.role_url };
    } catch {
      setError("apply_failed");
      return null;
    }
  }, [activeArtifact, refreshList]);

  return useMemo(
    () => ({
      conversations,
      conversationId,
      messages,
      isThinking,
      thinkingLabel,
      isStreaming,
      connected,
      error,
      inputDisabled,
      pendingQuickReplies,
      submitQuickReplies,
      selectConversation,
      newConversation,
      archiveConversation,
      renameConversation,
      send,
      sendToolResult,
      stop,
      refreshList,
      activeArtifact,
      artifactOpen,
      openArtifact,
      closeArtifact,
      saveArtifact,
      applyArtifact,
    }),
    [
      conversations,
      conversationId,
      messages,
      isThinking,
      thinkingLabel,
      isStreaming,
      connected,
      error,
      inputDisabled,
      pendingQuickReplies,
      submitQuickReplies,
      selectConversation,
      newConversation,
      archiveConversation,
      renameConversation,
      send,
      sendToolResult,
      submitQuickReplies,
      stop,
      refreshList,
      activeArtifact,
      artifactOpen,
      openArtifact,
      closeArtifact,
      saveArtifact,
      applyArtifact,
    ],
  );
}
