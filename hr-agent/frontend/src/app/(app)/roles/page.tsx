"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Plus, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Topbar } from "@/components/layout/topbar";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { swrFetcher } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Role } from "@/lib/types";

const STATUS_VARIANT: Record<string, "success" | "warning" | "muted" | "destructive"> = {
  open: "success",
  paused: "warning",
  filled: "muted",
  cancelled: "destructive",
};

export default function RolesListPage() {
  const { data, isLoading, isValidating, mutate } = useSWR<Role[]>("/dashboard/roles", swrFetcher);
  const [refreshing, setRefreshing] = useState(false);
  const refreshingNow = refreshing || isValidating;
  async function handleRefresh() {
    setRefreshing(true);
    try { await mutate(); } finally { setRefreshing(false); }
  }

  return (
    <>
      <Topbar title="Roles" subtitle="Job descriptions, scoring rubrics, and interviewer panels" />
      <div className="flex-1 overflow-auto p-6">
        <PageHeader
          title="Open roles"
          description="Pause or close roles to stop new applications flowing into them. Scoring rubrics live on each role."
          actions={
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshingNow}>
                <RefreshCw className={cn("mr-1 h-3.5 w-3.5", refreshingNow && "animate-spin")} />
                {refreshingNow ? "Refreshing" : "Refresh"}
              </Button>
              <Link href="/roles/new">
                <Button size="sm">
                  <Plus className="mr-1 h-3.5 w-3.5" /> New role
                </Button>
              </Link>
            </div>
          }
        />

        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>CTC band</TableHead>
                <TableHead>Notice cap</TableHead>
                <TableHead>Location</TableHead>
                <TableHead>Created</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableEmpty message="Loading…" />
              ) : !data || data.length === 0 ? (
                <TableEmpty message="No roles yet. Create one to start accepting applications." />
              ) : (
                data.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.title}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[r.status] ?? "muted"}>{r.status}</Badge>
                    </TableCell>
                    <TableCell>
                      {r.ctc_min_lpa != null && r.ctc_max_lpa != null
                        ? `${r.ctc_min_lpa}–${r.ctc_max_lpa} LPA`
                        : "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-sm">
                      {r.max_notice_days != null ? `${r.max_notice_days} days` : "—"}
                    </TableCell>
                    <TableCell>
                      {r.location ?? "—"}
                      {r.remote_policy ? (
                        <span className="ml-2 text-xs text-muted-foreground">· {r.remote_policy}</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fmtDate(r.created_at)}</TableCell>
                    <TableCell>
                      <Link href={`/roles/${r.id}`} className="text-sm font-medium text-primary hover:underline">
                        Edit →
                      </Link>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </Card>
      </div>
    </>
  );
}
