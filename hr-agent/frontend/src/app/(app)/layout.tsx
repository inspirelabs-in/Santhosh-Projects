"use client";

import { AuthGate } from "@/components/auth-gate";
import { FirstRunGate } from "@/components/first-run-gate";
import { PulseSidebar } from "@/components/layout/pulse-sidebar";
import { RecruiterChatProvider } from "@/lib/RecruiterChatProvider";
import { CommandPalette } from "@/components/command-palette";
import { KeyboardHelpDialog } from "@/components/keyboard-help-dialog";
import { KeyboardNav } from "@/components/keyboard-nav";
import { AgentToastStack } from "@/components/agent-toast";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <FirstRunGate>
        <RecruiterChatProvider>
          <div className="flex h-screen w-full overflow-hidden bg-background">
            <PulseSidebar />
            <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
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
  );
}
