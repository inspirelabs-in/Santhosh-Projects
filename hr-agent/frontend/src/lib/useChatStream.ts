"use client";

/**
 * SSE consumer for the V2 candidate chat stream.
 *
 * Backend pushes events as ``data: {json}\n\n`` lines on
 * ``GET /v2/chat/{token}/stream``. Event types:
 *   - ``thinking``     : agent picked up the message; show indicator
 *   - ``token``        : ``delta`` to append to the current assistant bubble
 *   - ``stage_change`` : ``{from, to}`` -- update StagePill, possibly UI affordance
 *   - ``brief``        : ``{assignment}`` -- AssignmentBrief card payload
 *   - ``done``         : ``{stage}`` -- assistant turn finished; unblock composer
 *   - ``error``        : ``{message}``
 *
 * The hook owns:
 *   * EventSource lifecycle (open / close / reconnect with backoff)
 *   * Buffered assistant message accumulation (one bubble per turn)
 *   * Optimistic user message render on send
 *
 * Usage:
 *
 *   const chat = useChatStream({ token, baseUrl });
 *   chat.messages.map(m => ...)
 *   chat.send("Hi")
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  pending?: boolean; // optimistic user message awaiting server ack
  streaming?: boolean; // assistant message currently being streamed
  createdAt: number;
}

export interface AssignmentBrief {
  brief_md: string;
  problems: Array<{
    id: string;
    title: string;
    statement: string;
    expected_artifacts: string[];
    estimated_minutes: number;
  }>;
  submission_format: {
    type: "github_repo" | "zip_upload" | "text_only" | "url";
    instructions: string;
    deadline_days: number;
  };
  evaluation_rubric?: { criteria: Array<{ name: string; weight: number; description: string }> };
}

export interface UseChatStreamArgs {
  token: string;
  baseUrl: string;
  initialMessages?: ChatMessage[];
  initialStage?: string;
  initialBrief?: AssignmentBrief | null;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  stage: string;
  brief: AssignmentBrief | null;
  isThinking: boolean;
  isStreaming: boolean;
  connected: boolean;
  error: string | null;
  send: (text: string) => Promise<void>;
}

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// Bound the in-memory message list. Server keeps full history; the candidate
// UI only needs recent context for scrollback.
const MAX_RENDERED_MESSAGES = 200;
function clip<T>(arr: T[]): T[] {
  return arr.length > MAX_RENDERED_MESSAGES
    ? arr.slice(-MAX_RENDERED_MESSAGES)
    : arr;
}

export function useChatStream({
  token,
  baseUrl,
  initialMessages = [],
  initialStage = "intake",
  initialBrief = null,
}: UseChatStreamArgs): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [stage, setStage] = useState<string>(initialStage);
  const [brief, setBrief] = useState<AssignmentBrief | null>(initialBrief);
  const [isThinking, setIsThinking] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mutable refs so the SSE handler doesn't re-bind every render.
  const sourceRef = useRef<EventSource | null>(null);
  const reconnectAttemptRef = useRef(0);
  const closingRef = useRef(false);

  // The id of the assistant message currently accumulating tokens.
  const activeAssistantIdRef = useRef<string | null>(null);

  const ensureAssistantBubble = useCallback(() => {
    if (activeAssistantIdRef.current) return activeAssistantIdRef.current;
    const id = uid();
    activeAssistantIdRef.current = id;
    setMessages((prev) =>
      clip([
        ...prev,
        { id, role: "assistant", content: "", streaming: true, createdAt: Date.now() },
      ]),
    );
    return id;
  }, []);

  const appendToken = useCallback(
    (delta: string) => {
      const id = ensureAssistantBubble();
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, content: m.content + delta } : m)),
      );
    },
    [ensureAssistantBubble],
  );

  const finalizeAssistant = useCallback(() => {
    const id = activeAssistantIdRef.current;
    if (!id) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, streaming: false } : m)),
    );
    activeAssistantIdRef.current = null;
  }, []);

  // ---- SSE wiring ----
  useEffect(() => {
    if (!token) return;
    closingRef.current = false;

    const open = () => {
      const url = `${baseUrl}/v2/chat/${token}/stream`;
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
          case "token": {
            setIsThinking(false);
            setIsStreaming(true);
            const delta = (payload.delta as string | undefined) ?? "";
            if (delta) appendToken(delta);
            break;
          }
          case "stage_change": {
            const to = (payload.to as string | undefined) ?? stage;
            setStage(to);
            break;
          }
          case "brief": {
            const a = payload.assignment as AssignmentBrief | undefined;
            if (a) setBrief(a);
            break;
          }
          case "done": {
            const finalStage = payload.stage as string | undefined;
            if (finalStage) setStage(finalStage);
            finalizeAssistant();
            setIsStreaming(false);
            setIsThinking(false);
            // Drop pending flag from any optimistic user msg older than this turn.
            setMessages((prev) =>
              prev.map((m) => (m.pending ? { ...m, pending: false } : m)),
            );
            break;
          }
          case "error": {
            const msg = (payload.message as string | undefined) ?? "stream_error";
            setError(msg);
            finalizeAssistant();
            setIsStreaming(false);
            setIsThinking(false);
            break;
          }
          default:
            break;
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
  }, [token, baseUrl]);

  // ---- send ----
  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const id = uid();
      setMessages((prev) =>
        clip([
          ...prev,
          { id, role: "user", content: trimmed, pending: true, createdAt: Date.now() },
        ]),
      );
      setIsThinking(true);
      // 15s timeout: send is a small POST that should resolve fast. The
      // streamed reply comes via SSE, not the response body of this call.
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 15000);
      try {
        const res = await fetch(`${baseUrl}/v2/chat/${token}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: trimmed }),
          signal: controller.signal,
        });
        if (res.status === 429) {
          throw new Error("Too many messages — please slow down and try again.");
        }
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
        // Mark the optimistic message as failed (drop pending so UI shows retry hint).
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
    [token, baseUrl],
  );

  return useMemo(
    () => ({ messages, stage, brief, isThinking, isStreaming, connected, error, send }),
    [messages, stage, brief, isThinking, isStreaming, connected, error, send],
  );
}
