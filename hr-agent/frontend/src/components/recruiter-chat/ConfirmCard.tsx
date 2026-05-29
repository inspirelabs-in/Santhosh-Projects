"use client";

import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronUp, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarkdownLite } from "@/components/markdown-lite";
import { getDashboardKey } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface ConfirmCardData {
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
  preview: string;
}

function HighlightField({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="rounded-md bg-background/70 p-2">
      <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-medium">{String(value)}</div>
    </div>
  );
}

export function ConfirmCard({
  data,
  conversationId,
  onResolved,
}: {
  data: ConfirmCardData;
  conversationId: string;
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"accepted" | "cancelled" | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  async function send(accept: boolean) {
    setBusy(true);
    try {
      await fetch(
        `${BASE}/v2/recruiter-chat/conversations/${conversationId}/confirm`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Dashboard-Key": getDashboardKey() || "",
          },
          body: JSON.stringify({ request_id: data.request_id, accept }),
        },
      );
      setDone(accept ? "accepted" : "cancelled");
      onResolved?.();
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        {done === "accepted" ? (
          <>
            <Check className="h-3.5 w-3.5 text-emerald-500" /> Confirmed.
          </>
        ) : (
          <>
            <X className="h-3.5 w-3.5 text-muted-foreground" /> Cancelled.
          </>
        )}
      </div>
    );
  }

  // Pretty per-tool field rendering; falls back to raw JSON drawer.
  const args = data.args || {};
  const isCreateRole = data.tool === "create_role" || data.tool === "create_role_with_assignment";
  const isPublishLi = data.tool === "publish_linkedin_post";
  const isAssignment =
    data.tool === "generate_assignment_for_role" ||
    data.tool === "set_role_assignment_brief" ||
    data.tool === "create_role_with_assignment";
  const longText =
    (typeof args.body_markdown === "string" && args.body_markdown) ||
    (typeof args.text === "string" && args.text) ||
    (typeof args.assignment_brief === "string" && args.assignment_brief) ||
    (typeof args.jd_text === "string" && args.jd_text) ||
    "";

  return (
    <div className="space-y-3 rounded-xl border border-amber-300/50 bg-gradient-to-br from-amber-50/70 to-amber-50/30 p-4 shadow-sm dark:border-amber-700/40 dark:from-amber-950/30 dark:to-amber-950/10">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-amber-700 dark:text-amber-300">
        <AlertTriangle className="h-3.5 w-3.5" />
        Confirm action
      </div>
      <div className="text-sm font-semibold text-foreground">{data.preview}</div>

      {/* Pretty per-tool field highlights */}
      <div className="grid grid-cols-2 gap-2">
        {isCreateRole && (
          <>
            <HighlightField label="Title" value={args.title} />
            <HighlightField
              label="CTC"
              value={
                args.ctc_min_lpa && args.ctc_max_lpa
                  ? `${args.ctc_min_lpa}-${args.ctc_max_lpa} LPA`
                  : null
              }
            />
            <HighlightField label="Location" value={args.location} />
            <HighlightField label="Modality" value={args.screening_modality} />
            <HighlightField label="Notice cap" value={args.max_notice_days ? `${args.max_notice_days}d` : null} />
            <HighlightField label="Remote" value={args.remote_policy} />
          </>
        )}
        {isPublishLi && <HighlightField label="Visibility" value={args.visibility} />}
      </div>

      {/* Full text body / brief (rendered as markdown) */}
      {longText && (
        <div className="max-h-72 overflow-y-auto rounded-md border border-border/60 bg-background p-3 text-sm scrollbar-slim">
          <MarkdownLite source={longText} />
        </div>
      )}

      {isAssignment && Array.isArray(args.problems) && (
        <div className="space-y-1.5">
          <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {(args.problems as unknown[]).length} problems
          </div>
          {(args.problems as Array<{ title?: string; statement?: string }>).map((p, i) => (
            <div key={i} className="rounded-md border border-border/60 bg-background/60 p-2 text-xs">
              <div className="font-semibold">
                {i + 1}. {p.title}
              </div>
              <div className="mt-0.5 text-foreground/85">{p.statement}</div>
            </div>
          ))}
        </div>
      )}

      {/* Raw JSON drawer for power users / debugging */}
      <button
        onClick={() => setShowRaw((v) => !v)}
        type="button"
        className="inline-flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground"
      >
        {showRaw ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {data.tool}
      </button>
      {showRaw && (
        <pre className="overflow-x-auto rounded-md bg-background/70 p-2 font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void send(false)}
          disabled={busy}
        >
          Cancel
        </Button>
        <Button size="sm" onClick={() => void send(true)} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Confirm
        </Button>
      </div>
    </div>
  );
}
