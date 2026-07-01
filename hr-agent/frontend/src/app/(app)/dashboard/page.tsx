"use client";

/**
 * Pulse — recruiter chat surface.
 *
 * The unified PulseSidebar (in (app)/layout.tsx) hosts conversations + nav.
 * This page is just chat panel + composer + glass header.
 */

import { useState } from "react";
import { FileText, Sparkles } from "lucide-react";
import { ChatMessages } from "@/components/recruiter-chat/ChatPanel";
import { PulseComposer } from "@/components/recruiter-chat/PulseComposer";
import { ArtifactPanel, ARTIFACT_PANEL_WIDTH } from "@/components/recruiter-chat/ArtifactPanel";
import { useRecruiterChatCtx } from "@/lib/RecruiterChatProvider";
import { cn } from "@/lib/utils";

export default function DashboardPage() {
  const chat = useRecruiterChatCtx();
  const [artifactExpanded, setArtifactExpanded] = useState(false);
  const activeTitle =
    chat.conversations.find((c) => c.id === chat.conversationId)?.title || "Pulse";
  const panelOpen = chat.artifactOpen && !!chat.activeArtifact;

  return (
    <div className="pulse-surface relative flex h-full w-full flex-col overflow-hidden">
      {/* Chat surface — reserves right padding for the docked draft panel, and
          is covered (pointer-events off) when the panel is expanded full-width. */}
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-hidden transition-[padding] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]",
          artifactExpanded && "pointer-events-none",
        )}
        style={{ paddingRight: panelOpen && !artifactExpanded ? ARTIFACT_PANEL_WIDTH : 0 }}
        aria-hidden={artifactExpanded || undefined}
      >
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
        <div className="flex items-center gap-2">
          {chat.activeArtifact && (
            <button
              onClick={() => (chat.artifactOpen ? chat.closeArtifact() : chat.openArtifact())}
              className="flex items-center gap-1.5 rounded-md border border-border/50 px-2.5 py-1.5 text-xs hover:bg-muted"
              title={chat.activeArtifact.status === "applied" ? "View published role draft" : "Open role draft"}
            >
              <FileText className="h-3.5 w-3.5 text-brand-green" />
              {chat.activeArtifact.status === "applied" ? "Role" : "Draft"}
            </button>
          )}
          {chat.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1 text-xs text-destructive">
              {chat.error}
            </div>
          )}
        </div>
      </header>

      <div className="flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <ChatMessages
            messages={chat.messages}
            isThinking={chat.isThinking}
            thinkingLabel={chat.thinkingLabel}
            conversationId={chat.conversationId}
            dispatch={(m) => void chat.send(m)}
          />
        </main>

        <PulseComposer
          onSend={(text, files) => chat.send(text, files)}
          onStop={chat.stop}
          disabled={chat.inputDisabled}
          isStreaming={chat.isStreaming}
          conversationId={chat.conversationId}
          quickReplies={chat.pendingQuickReplies}
          onQuickReplySubmit={(combined) => void chat.submitQuickReplies(combined)}
        />
      </div>
      </div>

      {panelOpen && chat.activeArtifact && (
        <ArtifactPanel
          artifact={chat.activeArtifact}
          expanded={artifactExpanded}
          onToggleExpand={() => setArtifactExpanded((v) => !v)}
          onClose={() => {
            setArtifactExpanded(false);
            chat.closeArtifact();
          }}
          onSave={chat.saveArtifact}
          onApply={chat.applyArtifact}
        />
      )}
    </div>
  );
}
