"use client";

/**
 * Presence heartbeat hook (the Jarvis catch-up).
 *
 * Two distinct signals, so the digest fires ONLY when you return after being
 * away — never while you're actively using the app:
 *   - RETURN CHECK (`keepalive=false`): on mount (cold open) and whenever the
 *     tab regains visibility. This is the only path that may create a digest.
 *   - KEEP-ALIVE (`keepalive=true`): the periodic 60s ping while the tab is
 *     visible. It ONLY refreshes last_seen_at server-side; it can never create a
 *     digest. This is what prevents a "Welcome back" chat spawning every minute
 *     while you sit in the app.
 *
 * On a return-check the backend may return the id of a new "welcome back"
 * conversation it created; the hook reports it via `onNewChat`.
 */

import { useEffect, useRef } from "react";
import { getDashboardKey } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const HEARTBEAT_MS = 60_000;

export function useWelcomeBackDigest(onNewChat: (conversationId: string) => void): void {
  const onNewChatRef = useRef(onNewChat);
  onNewChatRef.current = onNewChat;

  useEffect(() => {
    const ping = async (keepalive: boolean) => {
      const k = getDashboardKey();
      if (!k) return;
      try {
        const res = await fetch(
          `${BASE}/v2/recruiter-chat/presence?keepalive=${keepalive}`,
          { method: "POST", headers: { "X-Dashboard-Key": k }, cache: "no-store" },
        );
        if (!res.ok) return;
        const data = (await res.json()) as { conversation_id: string | null };
        // Only the return-check path can yield a conversation; keep-alive never will.
        if (data.conversation_id) {
          onNewChatRef.current(data.conversation_id);
        }
      } catch {
        /* presence is best-effort; never surface errors */
      }
    };

    void ping(false); // cold open = return check
    const onVisible = () => {
      if (document.visibilityState === "visible") void ping(false); // tab-return = return check
    };
    document.addEventListener("visibilitychange", onVisible);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void ping(true); // periodic = keep-alive only
    }, HEARTBEAT_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(interval);
    };
  }, []);
}
