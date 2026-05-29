"use client";

import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Loader2,
  Paperclip,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getDashboardKey } from "@/lib/auth";
import { SLASH_COMMANDS, SlashMenu, type SlashCommand } from "./SlashMenu";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface UploadedFile {
  file_ref: string;
  filename: string;
  size: number;
}

interface Props {
  onSend: (text: string, attachments?: UploadedFile[]) => void | Promise<void>;
  onStop?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  conversationId: string | null;
  placeholder?: string;
}

export function PulseComposer({
  onSend,
  onStop,
  disabled = false,
  isStreaming = false,
  conversationId,
  placeholder = "Ask Pulse anything. Type / for commands.",
}: Props) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showSlash, setShowSlash] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const adjust = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "0px";
    ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`;
  }, []);

  useEffect(() => {
    adjust();
    setShowSlash(value.startsWith("/") && !value.includes("\n"));
  }, [value, adjust]);

  const submit = useCallback(async () => {
    const text = value.trim();
    if (!text && files.length === 0) return;
    if (disabled) return;
    setValue("");
    const attached = files;
    setFiles([]);
    await onSend(text, attached);
  }, [value, files, disabled, onSend]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      void submit();
    }
  };

  const pickSlash = (c: SlashCommand) => {
    const rest = value.slice(c.cmd.length).trim();
    setValue(c.expand(rest));
    setShowSlash(false);
    taRef.current?.focus();
  };

  const upload = useCallback(
    async (f: File) => {
      if (!conversationId) return;
      setUploading(true);
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await fetch(
          `${BASE}/v2/recruiter-chat/conversations/${conversationId}/upload`,
          {
            method: "POST",
            headers: { "X-Dashboard-Key": getDashboardKey() || "" },
            body: fd,
          },
        );
        if (res.ok) {
          const j = (await res.json()) as UploadedFile;
          setFiles((prev) => [...prev, j]);
        }
      } finally {
        setUploading(false);
      }
    },
    [conversationId],
  );

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    Array.from(e.dataTransfer.files).forEach((f) => void upload(f));
  };

  const slashQuery = value.startsWith("/") ? value.split(/\s/)[0] : "";

  return (
    <div
      className={cn(
        "pulse-glass border-t border-border/40 px-4 py-4",
        dragOver && "bg-primary/5",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      {files.length > 0 && (
        <div className="mx-auto mb-2 flex max-w-3xl flex-wrap gap-1.5">
          {files.map((f) => (
            <span
              key={f.file_ref}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-0.5 text-xs"
            >
              <Paperclip className="h-3 w-3 text-muted-foreground" />
              {f.filename}
              <button
                onClick={() => setFiles((p) => p.filter((x) => x.file_ref !== f.file_ref))}
                className="text-muted-foreground hover:text-foreground"
                aria-label="Remove"
                type="button"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="relative mx-auto max-w-3xl">
        <SlashMenu query={slashQuery} onPick={pickSlash} visible={showSlash} />
        <div
          className={cn(
            "flex items-end gap-2 rounded-2xl border border-border/60 bg-card px-3 py-2",
            "shadow-card transition-all",
            "focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/20 focus-within:shadow-pop",
            disabled && "opacity-60",
            dragOver && "border-primary/60 ring-2 ring-primary/30",
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            multiple
            onChange={(e) => {
              const list = e.target.files ? Array.from(e.target.files) : [];
              list.forEach((f) => void upload(f));
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={!conversationId || uploading}
            className="h-8 w-8 shrink-0"
            title="Attach file"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
          </Button>
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
          {isStreaming && onStop ? (
            <Button
              type="button"
              size="icon"
              variant="destructive"
              onClick={onStop}
              className="h-8 w-8 shrink-0 rounded-full"
              aria-label="Stop"
            >
              <Square className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              onClick={() => void submit()}
              disabled={disabled || (!value.trim() && files.length === 0)}
              className="h-8 w-8 shrink-0 rounded-full"
              aria-label="Send"
            >
              <ArrowUp className="h-4 w-4" />
            </Button>
          )}
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Enter to send · Shift+Enter for newline · Tab to accept slash command · Drop files to attach
        </p>
      </div>
    </div>
  );
}
