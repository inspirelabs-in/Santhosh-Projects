"use client";

import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import { Message } from "./Message";
import { AssignmentBriefCard } from "./AssignmentBriefCard";
import type { AssignmentBrief, ChatMessage } from "@/lib/useChatStream";

interface Props {
  messages: ChatMessage[];
  isThinking: boolean;
  brief: AssignmentBrief | null;
  showBrief: boolean;
}

export function MessageList({ messages, isThinking, brief, showBrief }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isThinking, showBrief]);

  return (
    <div className="flex w-full flex-col gap-4 px-4 py-6">
      {messages.map((m) => (
        <Message key={m.id} msg={m} />
      ))}

      {showBrief && brief && (
        <div className="flex w-full justify-start">
          <div className="max-w-[88%]">
            <AssignmentBriefCard brief={brief} />
          </div>
        </div>
      )}

      {isThinking && (
        <div className="flex items-center gap-2 pl-10 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> thinking…
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
