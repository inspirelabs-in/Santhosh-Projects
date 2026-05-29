"use client";

import { createContext, useContext } from "react";
import { useRecruiterChat, type UseRecruiterChatReturn } from "@/lib/useRecruiterChat";

const Ctx = createContext<UseRecruiterChatReturn | null>(null);

export function RecruiterChatProvider({ children }: { children: React.ReactNode }) {
  const value = useRecruiterChat();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Read the recruiter chat state from context.
 *
 * Lives in the workspace layout so the unified sidebar can render the
 * conversation list on every route (Candidates, Roles, etc.) -- not just
 * /dashboard. The dashboard page consumes the same context for messages +
 * send + stop, so chat state stays coherent across navigation.
 */
export function useRecruiterChatCtx(): UseRecruiterChatReturn {
  const v = useContext(Ctx);
  if (!v)
    throw new Error("useRecruiterChatCtx must be used inside RecruiterChatProvider");
  return v;
}
