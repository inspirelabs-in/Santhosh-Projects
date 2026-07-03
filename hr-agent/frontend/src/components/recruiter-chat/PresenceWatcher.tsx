"use client";

import { useCallback, useRef } from "react";
import { useRecruiterChatCtx } from "@/lib/RecruiterChatProvider";
import { useWelcomeBackDigest } from "@/lib/useWelcomeBackDigest";

/**
 * Headless: on a presence "welcome back" digest, opens the new catch-up chat.
 * Safe-switch — only auto-opens when no conversation is currently active
 * (cold open). If the recruiter is already in a chat, the new one just
 * appears in the sidebar (never yanks them out of their work).
 */
export function PresenceWatcher() {
  const chat = useRecruiterChatCtx();
  const chatRef = useRef(chat);
  chatRef.current = chat;

  const onNewChat = useCallback(async (conversationId: string) => {
    const c = chatRef.current;
    await c.refreshList();
    if (c.conversationId == null) {
      c.selectConversation(conversationId);
    }
  }, []);

  useWelcomeBackDigest(onNewChat);
  return null;
}
