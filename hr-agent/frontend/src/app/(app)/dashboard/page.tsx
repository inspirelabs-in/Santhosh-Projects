"use client";

/**
 * Pulse — recruiter chat surface.
 *
 * The unified PulseSidebar (in (app)/layout.tsx) hosts conversations + nav.
 * This page is just chat panel + composer + glass header.
 */

import { Sparkles } from "lucide-react";
import { ChatMessages } from "@/components/recruiter-chat/ChatPanel";
import { PulseComposer } from "@/components/recruiter-chat/PulseComposer";
import { useRecruiterChatCtx } from "@/lib/RecruiterChatProvider";
import { cn } from "@/lib/utils";

export default function DashboardPage() {
  const chat = useRecruiterChatCtx();
  const activeTitle =
    chat.conversations.find((c) => c.id === chat.conversationId)?.title || "Pulse";

  return (
    <div className="pulse-surface flex h-full w-full flex-col overflow-hidden">
      <header className="pulse-glass flex shrink-0 items-center justify-between border-b border-border/40 px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="pulse-glow-ring flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-brand-green to-[hsl(85_100%_28%)] text-white shadow-sm">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-sm font-semibold leading-tight">{activeTitle}</h1>
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span
                className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  chat.connected ? "pulse-online-dot" : "bg-muted-foreground/40",
                )}
              />
              {chat.connected
                ? "Pulse is online"
                : chat.conversationId
                  ? "Reconnecting…"
                  : "Ready when you are"}
            </div>
          </div>
        </div>
        {chat.error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1 text-xs text-destructive">
            {chat.error}
          </div>
        )}
      </header>

      <main className="flex-1 overflow-y-auto">
        <ChatMessages
          messages={chat.messages}
          isThinking={chat.isThinking}
          conversationId={chat.conversationId}
          dispatch={(m) => void chat.send(m)}
        />
      </main>

      <PulseComposer
        onSend={(text, files) => chat.send(text, files)}
        onStop={chat.stop}
        disabled={false}
        isStreaming={chat.isStreaming}
        conversationId={chat.conversationId}
      />
    </div>
  );
}
