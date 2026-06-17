"use client";

import { SWRConfig } from "swr";
import { AuthGate } from "@/components/auth-gate";
import { FirstRunGate } from "@/components/first-run-gate";
import { PulseSidebar } from "@/components/layout/pulse-sidebar";
import { RecruiterChatProvider } from "@/lib/RecruiterChatProvider";
import { CommandPalette } from "@/components/command-palette";
import { KeyboardHelpDialog } from "@/components/keyboard-help-dialog";
import { KeyboardNav } from "@/components/keyboard-nav";
import { AgentToastStack } from "@/components/agent-toast";
import { ApiError } from "@/lib/api";

const swrGlobalConfig = {
  onErrorRetry(err: unknown, _key: string, _config: unknown, revalidate: (opts: { retryCount: number }) => void, { retryCount }: { retryCount: number }) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 404)) return;
    if (retryCount >= 3) return;
    setTimeout(() => revalidate({ retryCount }), Math.min(retryCount * 3000, 15000));
  },
  dedupingInterval: 2000,
  focusThrottleInterval: 10000,
  revalidateOnFocus: false,
};

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig value={swrGlobalConfig}>
      <AuthGate>
        <FirstRunGate>
          <RecruiterChatProvider>
            <div className="flex h-screen w-full overflow-hidden bg-background">
              <PulseSidebar />
              <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
                {children}
              </main>
            </div>
            <CommandPalette />
            <KeyboardHelpDialog />
            <KeyboardNav />
            <AgentToastStack />
          </RecruiterChatProvider>
        </FirstRunGate>
      </AuthGate>
    </SWRConfig>
  );
}
