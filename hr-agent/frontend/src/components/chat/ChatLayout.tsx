"use client";

import { useMemo, useState } from "react";
import { Logo } from "@/components/brand/logo";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { StagePill } from "./StagePill";
import { SubmissionPanel } from "./SubmissionPanel";
import type { AssignmentBrief, ChatMessage } from "@/lib/useChatStream";

interface Props {
  token: string;
  baseUrl: string;
  roleTitle: string;
  candidateName: string | null;
  stage: string;
  messages: ChatMessage[];
  brief: AssignmentBrief | null;
  isThinking: boolean;
  isStreaming: boolean;
  connected: boolean;
  error: string | null;
  onSend: (text: string) => void | Promise<void>;
}

export function ChatLayout({
  token,
  baseUrl,
  roleTitle,
  candidateName,
  stage,
  messages,
  brief,
  isThinking,
  isStreaming,
  connected,
  error,
  onSend,
}: Props) {
  const [submitted, setSubmitted] = useState(stage === "submitted" || stage === "completed");
  const showBrief = (stage === "assignment" || stage === "submitted") && brief !== null;
  const showSubmission = stage === "assignment" && brief !== null && !submitted;

  const composerDisabled = useMemo(
    () => isStreaming || stage === "submitted" || stage === "completed" || stage === "rejected",
    [isStreaming, stage],
  );
  const placeholder = useMemo(() => {
    if (stage === "assignment") return "Ask anything about the assignment, or scroll down to submit.";
    if (stage === "submitted" || stage === "completed") return "Submission received.";
    if (stage === "rejected") return "This conversation is closed.";
    return "Reply to the agent…";
  }, [stage]);

  return (
    <div className="flex h-dvh w-full flex-col bg-background text-foreground">
      <header className="sticky top-0 z-10 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <Logo width={28} className="shrink-0" />
            <div>
              <div className="text-sm font-semibold">{roleTitle}</div>
              <div className="text-[11px] text-muted-foreground">
                {candidateName ? `Hi ${candidateName.split(" ")[0]}` : "Application"}
                {!connected && " · reconnecting…"}
              </div>
            </div>
          </div>
          <StagePill stage={stage} />
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl">
          <MessageList
            messages={messages}
            isThinking={isThinking}
            brief={brief}
            showBrief={showBrief}
          />
          {error && (
            <div className="mx-4 my-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
          {showSubmission && (
            <div className="px-4 pb-6">
              <SubmissionPanel
                token={token}
                baseUrl={baseUrl}
                onSubmitted={() => setSubmitted(true)}
              />
            </div>
          )}
        </div>
      </main>

      <Composer
        onSend={onSend}
        disabled={composerDisabled}
        placeholder={placeholder}
        isStreaming={isStreaming}
      />
    </div>
  );
}
