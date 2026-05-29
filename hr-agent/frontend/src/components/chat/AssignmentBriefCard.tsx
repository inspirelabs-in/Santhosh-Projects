"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Clock, FileText, Target } from "lucide-react";
import { MarkdownLite } from "@/components/markdown-lite";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AssignmentBrief } from "@/lib/useChatStream";

export function AssignmentBriefCard({ brief }: { brief: AssignmentBrief }) {
  const [expanded, setExpanded] = useState(true);

  const totalMinutes = brief.problems.reduce(
    (sum, p) => sum + (p.estimated_minutes || 0),
    0,
  );

  return (
    <div className="overflow-hidden rounded-2xl border border-amber-300/50 bg-amber-50/50 dark:border-amber-700/40 dark:bg-amber-950/20 shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b border-amber-200/60 bg-amber-100/40 px-4 py-2.5 dark:border-amber-800/40 dark:bg-amber-900/30">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-900 dark:text-amber-100">
          <FileText className="h-4 w-4" />
          Your assignment
        </div>
        <div className="flex items-center gap-3 text-[11px] text-amber-800/80 dark:text-amber-200/80">
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" /> ~{Math.max(totalMinutes, 60)} min
          </span>
          <span className="inline-flex items-center gap-1">
            <Target className="h-3 w-3" /> {brief.problems.length} problem
            {brief.problems.length === 1 ? "" : "s"}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-amber-900 hover:bg-amber-200/40 dark:text-amber-100"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>

      {expanded && (
        <div className="space-y-4 px-4 py-3">
          <div className="-my-2">
            <MarkdownLite source={brief.brief_md} />
          </div>

          <div className="space-y-3">
            {brief.problems.map((p, i) => (
              <div
                key={p.id}
                className="rounded-lg border border-amber-200/60 bg-background/60 p-3 dark:border-amber-800/40"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <h5 className="text-sm font-semibold">
                    {i + 1}. {p.title}
                  </h5>
                  <span className="text-[11px] text-muted-foreground">
                    ~{p.estimated_minutes} min
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-foreground/90">{p.statement}</p>
                {p.expected_artifacts.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {p.expected_artifacts.map((a) => (
                      <span
                        key={a}
                        className="rounded-md border border-border/60 bg-muted/50 px-1.5 py-0.5 font-mono text-[11px]"
                      >
                        {a}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="rounded-lg bg-background/40 p-3 text-xs leading-relaxed">
            <div className="mb-1 font-semibold text-foreground">Submission</div>
            <div className="text-muted-foreground">{brief.submission_format.instructions}</div>
            <div className="mt-1.5 text-muted-foreground">
              Format: <span className="font-mono">{brief.submission_format.type}</span> ·
              Deadline: {brief.submission_format.deadline_days} days
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
