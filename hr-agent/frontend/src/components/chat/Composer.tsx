"use client";

import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface Props {
  onSend: (text: string) => void | Promise<void>;
  disabled?: boolean;
  placeholder?: string;
  isStreaming?: boolean;
}

export function Composer({
  onSend,
  disabled = false,
  placeholder = "Reply to the agent…",
  isStreaming = false,
}: Props) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const adjustHeight = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "0px";
    const next = Math.min(ta.scrollHeight, 220);
    ta.style.height = `${next}px`;
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [value, adjustHeight]);

  const submit = useCallback(async () => {
    const text = value.trim();
    if (!text || disabled) return;
    setValue("");
    await onSend(text);
  }, [value, disabled, onSend]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="border-t border-border/60 bg-background px-4 py-3">
      <div
        className={cn(
          "mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-border/60 bg-card px-3 py-2 shadow-sm",
          "focus-within:ring-2 focus-within:ring-ring/40",
          disabled && "opacity-60",
        )}
      >
        <textarea
          ref={taRef}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
        />
        <Button
          type="button"
          size="icon"
          onClick={() => void submit()}
          disabled={disabled || !value.trim()}
          className="h-8 w-8 shrink-0 rounded-full"
          aria-label="Send"
        >
          {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
        </Button>
      </div>
      <p className="mx-auto mt-2 max-w-3xl text-[11px] text-muted-foreground">
        Press Enter to send · Shift+Enter for newline
      </p>
    </div>
  );
}
