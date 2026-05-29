"use client";

import Link from "next/link";
import { Bell, ExternalLink } from "lucide-react";

interface NudgeCardData {
  application_id: string;
  event: string;
  candidate_name: string | null;
  role_title: string | null;
  ts: string;
}

export function NudgeCard({ data }: { data: NudgeCardData }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-blue-300/50 bg-blue-50/50 p-3 text-xs dark:border-blue-700/40 dark:bg-blue-950/20">
      <Bell className="h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">
          {data.candidate_name || "Candidate"} ·{" "}
          <span className="text-muted-foreground">{data.role_title || "role"}</span>
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wider text-blue-600 dark:text-blue-400">
          {data.event.replaceAll("_", " ")}
        </div>
      </div>
      <Link
        href={`/candidates/${data.application_id}`}
        className="text-muted-foreground hover:text-foreground"
        title="Open detail"
      >
        <ExternalLink className="h-3.5 w-3.5" />
      </Link>
    </div>
  );
}
