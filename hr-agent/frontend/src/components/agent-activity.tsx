"use client";

import useSWR from "swr";
import Link from "next/link";
import {
  Activity,
  PhoneCall,
  ClipboardCheck,
  Video,
  CheckCircle2,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { swrFetcher } from "@/lib/api";
import { fmtRelative } from "@/lib/utils";

interface AgentJob {
  kind: "voice_call" | "assessment" | "meeting";
  status: string;
  application_id: string;
  candidate_name: string | null;
  role_title: string | null;
  started_at: string | null;
  detail?: Record<string, unknown> | null;
}

interface RecentActivity {
  application_id: string;
  candidate_name: string | null;
  action: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

interface Snapshot {
  in_flight: AgentJob[];
  recent: RecentActivity[];
  counts: { voice: number; assessment: number; meeting: number };
}

const ICON: Record<AgentJob["kind"], typeof PhoneCall> = {
  voice_call: PhoneCall,
  assessment: ClipboardCheck,
  meeting: Video,
};

export function AgentActivityCard() {
  const { data, isLoading } = useSWR<Snapshot>(
    "/dashboard/v1/agent/snapshot",
    swrFetcher,
    { refreshInterval: 8_000 },
  );

  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-primary" />
            </span>
            <h2 className="text-sm font-bold uppercase tracking-[0.12em]">
              Agent activity
            </h2>
          </div>
          <span className="text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
            {data?.in_flight.length ?? 0} in flight
          </span>
        </div>

        {isLoading ? (
          <div className="h-20 rounded-md skeleton" />
        ) : (data?.in_flight.length ?? 0) === 0 ? (
          <div className="flex items-center gap-2 rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
            <Activity className="h-4 w-4" />
            Idle. No active phone calls, assessments, or meetings right now.
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {data!.in_flight.slice(0, 8).map((j) => {
              const Icon = ICON[j.kind];
              return (
                <li key={`${j.kind}-${j.application_id}-${j.started_at}`}>
                  <Link
                    href={`/candidates/${j.application_id}`}
                    className="flex items-center gap-3 py-2.5 transition hover:bg-muted/40"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">
                        {j.candidate_name ?? "Unnamed"}
                        <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
                          {j.kind.replace("_", " ")} · {j.status.replace("_", " ")}
                        </span>
                      </p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {j.role_title ?? "—"}
                        {j.started_at ? ` · ${fmtRelative(j.started_at)}` : null}
                      </p>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        {data?.recent && data.recent.length > 0 ? (
          <div>
            <p className="mb-2 mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              recent (last 30 min)
            </p>
            <ul className="space-y-1.5">
              {data.recent.slice(0, 5).map((r, idx) => (
                <li
                  key={`${r.application_id}-${r.created_at}-${idx}`}
                  className="flex items-start gap-2 text-[11px] text-muted-foreground"
                >
                  <CheckCircle2 className="h-3 w-3 mt-0.5 text-primary" />
                  <span className="truncate">
                    <Link
                      href={`/candidates/${r.application_id}`}
                      className="font-semibold text-foreground hover:underline"
                    >
                      {r.candidate_name ?? "—"}
                    </Link>
                    {" · "}
                    <span className="font-mono">{r.action}</span>
                    {" · "}
                    {fmtRelative(r.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
