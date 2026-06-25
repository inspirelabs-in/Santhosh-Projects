"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { QuickReplyQuestion } from "@/lib/useRecruiterChat";

interface QuickRepliesProps {
  questions: QuickReplyQuestion[];
  // Called with the recruiter's answers once every question is answered.
  onComplete: (combined: string) => void;
}

/**
 * Claude-style in-composer options picker. Renders the LLM's questions inside
 * the input bar: option chips + an optional "type your own" field, with
 * navigation between questions. Once every question has an answer, the combined
 * "question -> answer" text is sent off.
 */
export function QuickReplies({ questions, onComplete }: QuickRepliesProps) {
  const total = questions.length;
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState<string[]>(() => questions.map(() => ""));
  const [custom, setCustom] = useState("");

  const q = questions[idx];
  const answeredCount = answers.filter((a) => a.trim()).length;

  function commit(value: string) {
    const v = value.trim();
    if (!v) return;
    const next = answers.map((a, i) => (i === idx ? v : a));
    setAnswers(next);
    setCustom("");
    // Every question answered -> send off (matches "once selected, send it").
    if (next.every((a) => a.trim())) {
      const combined = questions
        .map((qq, i) => `${qq.question} ${next[i]}`)
        .join("\n");
      onComplete(combined);
      return;
    }
    // Otherwise advance to the next still-unanswered question.
    const nextUnanswered = next.findIndex((a, i) => i > idx && !a.trim());
    setIdx(nextUnanswered === -1 ? Math.min(idx + 1, total - 1) : nextUnanswered);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-foreground">{q.question}</p>
        {total > 1 && (
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <button
              type="button"
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
              disabled={idx === 0}
              className="rounded p-0.5 hover:bg-muted disabled:opacity-30"
              aria-label="Previous question"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span>
              {idx + 1} / {total}
              {answeredCount > 0 ? ` · ${answeredCount} answered` : ""}
            </span>
            <button
              type="button"
              onClick={() => setIdx((i) => Math.min(total - 1, i + 1))}
              disabled={idx === total - 1}
              className="rounded p-0.5 hover:bg-muted disabled:opacity-30"
              aria-label="Next question"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        {q.options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => commit(opt)}
            className={cn(
              "w-full rounded-md border px-3 py-1.5 text-left text-xs transition-colors",
              answers[idx] === opt
                ? "border-brand-green bg-brand-green/10 text-foreground"
                : "border-border/60 hover:border-brand-green/60 hover:bg-brand-green/5",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-green/40",
            )}
          >
            {opt}
          </button>
        ))}
      </div>

      {q.allowCustom && (
        <div className="flex gap-1.5">
          <input
            type="text"
            className="flex-1 rounded-md border border-border/60 bg-transparent px-2.5 py-1 text-xs outline-none focus:border-brand-green/60"
            placeholder="Or type your own..."
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit(custom);
              }
            }}
          />
          <button
            type="button"
            onClick={() => commit(custom)}
            disabled={!custom.trim()}
            className={cn(
              "rounded-md px-3 py-1 text-xs font-medium transition-colors",
              custom.trim()
                ? "bg-brand-green text-white hover:bg-brand-green/90"
                : "bg-muted text-muted-foreground cursor-default",
            )}
          >
            {idx === total - 1 ? "Send" : "Next"}
          </button>
        </div>
      )}
    </div>
  );
}
