"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, Link2, Loader2, Paperclip, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface UploadedFile {
  filename: string;
  r2_key: string;
}

interface Props {
  token: string;
  baseUrl: string;
  onSubmitted: () => void;
  onUploaded?: (file: UploadedFile) => void;
}

const ALLOWED_TYPES = new Set([
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/zip",
  "application/x-zip-compressed",
  "text/plain",
  "text/csv",
  "text/markdown",
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "video/mp4",
  "video/webm",
]);

const MAX_FILE_SIZE = 25 * 1024 * 1024; // 25 MB

export function SubmissionPanel({ token, baseUrl, onSubmitted, onUploaded }: Props) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const upload = useCallback(
    async (file: File) => {
      if (file.size > MAX_FILE_SIZE) {
        setError(`File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max 25 MB.`);
        return;
      }
      if (file.type && !ALLOWED_TYPES.has(file.type)) {
        setError(`File type "${file.type}" not allowed. Use PDF, Word, ZIP, images, or plain text.`);
        return;
      }
      setUploading(true);
      setError(null);
      try {
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch(`${baseUrl}/v2/chat/${token}/upload`, {
          method: "POST",
          body: fd,
        });
        if (res.status === 429) {
          throw new Error("Too many uploads — please wait a moment and try again.");
        }
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `HTTP ${res.status}`);
        }
        const json = (await res.json()) as { filename: string; r2_key: string };
        const f = { filename: json.filename, r2_key: json.r2_key };
        setFiles((prev) => [...prev, f]);
        onUploaded?.(f);
      } catch (e) {
        setError(e instanceof Error ? e.message : "upload_failed");
      } finally {
        setUploading(false);
      }
    },
    [token, baseUrl, onUploaded],
  );

  const submit = useCallback(async () => {
    if (!url && !text && files.length === 0) {
      setError("Add a link, text, or file before submitting.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${baseUrl}/v2/chat/${token}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          submission_url: url || null,
          submission_text: text || null,
        }),
      });
      if (res.status === 429) {
        throw new Error("Too many requests — please wait a moment and try again.");
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setDone(true);
      onSubmitted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "submit_failed");
    } finally {
      setSubmitting(false);
    }
  }, [url, text, files, token, baseUrl, onSubmitted]);

  if (done) {
    return (
      <div className="mx-auto flex max-w-3xl items-center gap-2 rounded-xl border border-emerald-300/50 bg-emerald-50/50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-100">
        <CheckCircle2 className="h-4 w-4" />
        Submission received. We&apos;ll be in touch.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-3 rounded-xl border border-border/60 bg-card/60 p-4">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Send className="h-4 w-4 text-primary" /> Submit your assignment
      </div>

      <div className="space-y-1">
        <label className="text-xs text-muted-foreground" htmlFor="sub-url">
          Repo / drive / deployment URL (optional)
        </label>
        <div className="flex items-center gap-2">
          <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            id="sub-url"
            placeholder="https://github.com/you/project"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={submitting}
          />
        </div>
      </div>

      <div className="space-y-1">
        <label className="text-xs text-muted-foreground" htmlFor="sub-text">
          Notes for the reviewer (optional)
        </label>
        <Textarea
          id="sub-text"
          placeholder="Anything you want the reviewer to know — design choices, trade-offs, how to run it."
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          disabled={submitting}
        />
      </div>

      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">Files (optional, ≤ 25 MB each)</div>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".pdf,.doc,.docx,.zip,.txt,.csv,.md,.png,.jpg,.jpeg,.gif,.webp,.mp4,.webm"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
            if (inputRef.current) inputRef.current.value = "";
          }}
        />
        <Button
          variant="outline"
          size="sm"
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading || submitting}
        >
          {uploading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Uploading…
            </>
          ) : (
            <>
              <Paperclip className="h-3.5 w-3.5" /> Attach file
            </>
          )}
        </Button>
        {files.length > 0 && (
          <ul className="mt-2 space-y-1">
            {files.map((f) => (
              <li
                key={f.r2_key}
                className="flex items-center gap-2 rounded-md border border-border/60 bg-background px-2 py-1 text-xs"
              >
                <Paperclip className="h-3 w-3 text-muted-foreground" />
                <span className="flex-1 truncate font-mono">{f.filename}</span>
                <button
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => setFiles((prev) => prev.filter((p) => p.r2_key !== f.r2_key))}
                  aria-label="Remove from list"
                  type="button"
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <Button onClick={() => void submit()} disabled={submitting}>
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Submit assignment
        </Button>
      </div>
    </div>
  );
}
