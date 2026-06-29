"use client";

import { useState } from "react";
import { Check, ChevronLeft, ChevronRight } from "lucide-react";
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
 * navigation between questions.
 *
 * Single-select questions commit on click and auto-advance. Multi-select
 * questions (multiSelect) toggle chips and wait for an explicit Next/Send so the
 * recruiter can pick several. Once every question has at least one answer, the
 * combined "Q: ...\nAns: ..." text is sent off.
 */
export function QuickReplies({ questions, onComplete }: QuickRepliesProps) {
  const total = questions.length;
  const [idx, setIdx] = useState(0);
  // One list of chosen values per question (single-select holds 0 or 1).
  const [selections, setSelections] = useState<string[][]>(() =>
    questions.map(() => []),
  );
  const [custom, setCustom] = useState("");

  const q = questions[idx];
  const answeredCount = selections.filter((s) => s.length > 0).length;
  const currentAnswered = selections[idx].length > 0;

  function maybeSend(next: string[][]): boolean {
    if (next.every((s) => s.length > 0)) {
      const combined = questions
        .map((qq, i) => `Q: ${qq.question}\nAns: ${next[i].join(", ")}`)
        .join("\n\n");
      onComplete(combined);
      return true;
    }
    return false;
  }

  function advance(next: string[][]) {
    const nextUnanswered = next.findIndex((s, i) => i > idx && s.length === 0);
    setIdx(nextUnanswered === -1 ? Math.min(idx + 1, total - 1) : nextUnanswered);
  }

  // Single-select: replace the answer and move on (or send when all done).
  function pickSingle(value: string) {
    const v = value.trim();
    if (!v) return;
    const next = selections.map((s, i) => (i === idx ? [v] : s));
    setSelections(next);
    setCustom("");
    if (!maybeSend(next)) advance(next);
  }

  // Multi-select: toggle the value in/out; never auto-advance.
  function toggleMulti(value: string) {
    const v = value.trim();
    if (!v) return;
    setSelections((prev) =>
      prev.map((s, i) =>
        i !== idx ? s : s.includes(v) ? s.filter((x) => x !== v) : [...s, v],
      ),
    );
  }

  function onOptionClick(opt: string) {
    if (q.multiSelect) toggleMulti(opt);
    else pickSingle(opt);
  }

  function onCustom() {
    const v = custom.trim();
    if (!v) return;
    if (q.multiSelect) {
      setSelections((prev) =>
        prev.map((s, i) => (i === idx && !s.includes(v) ? [...s, v] : s)),
      );
      setCustom("");
    } else {
      pickSingle(v);
    }
  }

  // Multi-select footer: commit the current question and continue/send.
  function onContinue() {
    if (!currentAnswered) return;
    if (!maybeSend(selections)) advance(selections);
  }

  const isLast = idx === total - 1;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-foreground">
          {q.question}
          {q.multiSelect && (
            <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">
              (pick any)
            </span>
          )}
        </p>
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
        {q.options.map((opt) => {
          const selected = selections[idx].includes(opt);
          return (
            <button
              key={opt}
              type="button"
              onClick={() => onOptionClick(opt)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md border px-3 py-1.5 text-left text-xs transition-colors",
                selected
                  ? "border-brand-green bg-brand-green/10 text-foreground"
                  : "border-border/60 hover:border-brand-green/60 hover:bg-brand-green/5",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-green/40",
              )}
            >
              {q.multiSelect && (
                <span
                  className={cn(
                    "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
                    selected
                      ? "border-brand-green bg-brand-green text-white"
                      : "border-border/70",
                  )}
                >
                  {selected && <Check className="h-2.5 w-2.5" />}
                </span>
              )}
              {opt}
            </button>
          );
        })}
      </div>

      {q.allowCustom && (
        <div className="flex gap-1.5">
          <input
            type="text"
            className="flex-1 rounded-md border border-border/60 bg-transparent px-2.5 py-1 text-xs outline-none focus:border-brand-green/60"
            placeholder={q.multiSelect ? "Add your own..." : "Or type your own..."}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onCustom();
              }
            }}
          />
          <button
            type="button"
            onClick={onCustom}
            disabled={!custom.trim()}
            className={cn(
              "rounded-md px-3 py-1 text-xs font-medium transition-colors",
              custom.trim()
                ? "bg-brand-green text-white hover:bg-brand-green/90"
                : "bg-muted text-muted-foreground cursor-default",
            )}
          >
            {q.multiSelect ? "Add" : isLast ? "Send" : "Next"}
          </button>
        </div>
      )}

      {q.multiSelect && (
        <button
          type="button"
          onClick={onContinue}
          disabled={!currentAnswered}
          className={cn(
            "self-end rounded-md px-3 py-1 text-xs font-medium transition-colors",
            currentAnswered
              ? "bg-brand-green text-white hover:bg-brand-green/90"
              : "bg-muted text-muted-foreground cursor-default",
          )}
        >
          {isLast ? "Send" : "Next"}
        </button>
      )}
    </div>
  );
}
