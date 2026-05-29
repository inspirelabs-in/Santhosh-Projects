"use client";

import { useState } from "react";
import { Check, Copy, ExternalLink, Linkedin } from "lucide-react";
import { Button } from "@/components/ui/button";

interface LinkedInPostData {
  ok?: boolean;
  role_id?: string | null;
  role_title?: string | null;
  text?: string;
  char_count?: number;
  hashtags?: string[];
  apply_url?: string;
}

export function LinkedInPostCard({ data }: { data: LinkedInPostData }) {
  const [copied, setCopied] = useState(false);
  const text = (data.text || "").trim();

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* ignore -- some browsers gate clipboard on insecure origins */
    }
  }

  function openLinkedInComposer() {
    const shareUrl = `https://www.linkedin.com/feed/?shareActive=true&shareUrl=${encodeURIComponent(data.apply_url || "https://www.grabon.in/careers")}`;
    window.open(shareUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-card">
      <div className="flex items-center gap-2 border-b border-border/60 bg-gradient-to-r from-[#0a66c2]/10 via-[#0a66c2]/5 to-transparent px-4 py-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#0a66c2] text-white">
          <Linkedin className="h-3.5 w-3.5" />
        </div>
        <div className="flex-1">
          <div className="text-sm font-semibold">LinkedIn post</div>
          {data.role_title && (
            <div className="text-[11px] text-muted-foreground">
              For: {data.role_title}
            </div>
          )}
        </div>
        <span className="text-[10px] font-mono text-muted-foreground">
          {data.char_count ?? text.length} chars
        </span>
      </div>

      <div className="max-h-96 overflow-y-auto whitespace-pre-wrap p-4 text-sm leading-relaxed scrollbar-slim">
        {text}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-border/60 bg-muted/30 px-3 py-2">
        <div className="text-[11px] text-muted-foreground">
          {data.apply_url && (
            <span>
              Apply: <span className="font-mono">{data.apply_url}</span>
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void copyToClipboard()}
            className="gap-1.5"
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={openLinkedInComposer}
            className="gap-1.5 bg-[#0a66c2] hover:bg-[#0a66c2]/90"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open LinkedIn
          </Button>
        </div>
      </div>
    </div>
  );
}
