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

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export type RecruiterMessageRole = "user" | "assistant" | "tool" | "system";

export interface ToolCall {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
}

export interface Attachment {
  kind: string; // candidate-list | role-list | candidate-detail | metrics | stuck-list | audit-list | action-result
  data?: unknown;
  items?: unknown[];
  raw?: unknown;
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
  isStreaming: boolean;
  connected: boolean;
  error: string | null;
  selectConversation: (id: string) => void;
  newConversation: () => Promise<string | null>;
  archiveConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  send: (text: string, attachments?: { file_ref: string; filename: string; size: number }[]) => Promise<void>;
  stop: () => Promise<void>;
  refreshList: () => Promise<void>;
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
  for (const m of detail.messages) {
    if (m.role === "user" || m.role === "assistant") {
      out.push({
        id: `srv-${m.sequence}`,
        role: m.role,
        content: m.content || "",
        toolCalls: m.tool_calls || undefined,
        attachments: m.attachments || undefined,
        createdAt: new Date(m.created_at).getTime(),
      });
    } else if (m.role === "tool") {
      out.push({
        id: `srv-${m.sequence}`,
        role: "tool",
        content: "",
        toolName: m.tool_name,
        attachments: m.attachments || undefined,
        createdAt: new Date(m.created_at).getTime(),
      });
    }
  }
  return out;
}

export function useRecruiterChat(): UseRecruiterChatReturn {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<RecruiterMessage[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const closingRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const activeAssistantIdRef = useRef<string | null>(null);

  const ensureAssistant = useCallback(() => {
    if (activeAssistantIdRef.current) return activeAssistantIdRef.current;
    const id = uid();
    activeAssistantIdRef.current = id;
    setMessages((prev) => [
      ...prev,
      { id, role: "assistant", content: "", streaming: true, createdAt: Date.now() },
    ]);
    return id;
  }, []);

  const finalizeAssistant = useCallback(() => {
    const id = activeAssistantIdRef.current;
    if (!id) return;
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, streaming: false } : m)));
    activeAssistantIdRef.current = null;
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

  const loadConversation = useCallback(async (id: string) => {
    activeAssistantIdRef.current = null;
    setMessages([]);
    try {
      const res = await fetch(`${BASE}/v2/recruiter-chat/conversations/${id}`, {
        headers: authHeader(),
        cache: "no-store",
      });
      if (!res.ok) return;
      const detail = (await res.json()) as ConversationDetailRaw;
      setMessages(hydrate(detail));
    } catch {
      /* ignore */
    }
  }, []);

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
            setIsThinking(true);
            break;
          case "tool_call": {
            // Render a tool-call placeholder message.
            const id = uid();
            setMessages((prev) => [
              ...prev,
              {
                id,
                role: "tool",
                content: "",
                toolName: (payload.name as string) || "tool",
                streaming: true,
                createdAt: Date.now(),
              },
            ]);
            break;
          }
          case "tool_result": {
            // Mark the most recent tool-row with this name as resolved.
            const name = (payload.name as string) || "";
            setMessages((prev) => {
              const idx = [...prev].reverse().findIndex(
                (m) => m.role === "tool" && m.streaming && m.toolName === name,
              );
              if (idx === -1) return prev;
              const real = prev.length - 1 - idx;
              const next = [...prev];
              next[real] = {
                ...next[real],
                streaming: false,
                toolResultPreview: payload.preview as string | undefined,
              };
              return next;
            });
            break;
          }
          case "attachment": {
            const att: Attachment = {
              kind: (payload.kind as string) || "raw",
              data: payload.data,
              raw: payload.raw,
            };
            // Attach to the most recent tool message if it has the same kind family,
            // otherwise add a standalone tool message.
            setMessages((prev) => {
              const idx = [...prev].reverse().findIndex((m) => m.role === "tool");
              if (idx === -1) {
                return [
                  ...prev,
                  {
                    id: uid(),
                    role: "tool",
                    content: "",
                    attachments: [att],
                    createdAt: Date.now(),
                  },
                ];
              }
              const real = prev.length - 1 - idx;
              const next = [...prev];
              const cur = next[real];
              next[real] = { ...cur, attachments: [...(cur.attachments || []), att] };
              return next;
            });
            break;
          }
          case "token": {
            setIsThinking(false);
            setIsStreaming(true);
            const id = ensureAssistant();
            const delta = (payload.delta as string) || "";
            if (delta) {
              setMessages((prev) =>
                prev.map((m) => (m.id === id ? { ...m, content: m.content + delta } : m)),
              );
            }
            break;
          }
          case "done": {
            finalizeAssistant();
            setIsStreaming(false);
            setIsThinking(false);
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
            finalizeAssistant();
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

  return useMemo(
    () => ({
      conversations,
      conversationId,
      messages,
      isThinking,
      isStreaming,
      connected,
      error,
      selectConversation,
      newConversation,
      archiveConversation,
      renameConversation,
      send,
      stop,
      refreshList,
    }),
    [
      conversations,
      conversationId,
      messages,
      isThinking,
      isStreaming,
      connected,
      error,
      selectConversation,
      newConversation,
      archiveConversation,
      renameConversation,
      send,
      stop,
      refreshList,
    ],
  );
}
