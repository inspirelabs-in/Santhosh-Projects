"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Megaphone,
  Play,
  Pause,
  XCircle,
  CheckCircle2,
  Loader2,
  RotateCw,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  campaigns,
  type CampaignDetailResponse,
  type CallKind,
  CALL_KIND_LABELS,
} from "@/lib/api/agentic";
import { fmtRelative } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  dispatching: "bg-info/15 text-info",
  paused: "bg-warning/15 text-foreground",
  completed: "bg-success/15 text-success",
  cancelled: "bg-destructive/10 text-destructive",
};

export default function CampaignsPage() {
  // For now, campaigns are fetched individually by ID.
  // A list endpoint can be added later.
  const [campaignId, setCampaignId] = useState("");
  const [loading, setLoading] = useState(false);
  const [campaign, setCampaign] = useState<CampaignDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadCampaign() {
    if (!campaignId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await campaigns.get(campaignId.trim());
      setCampaign(data);
    } catch (e: any) {
      setError(e?.message ?? "Campaign not found");
      setCampaign(null);
    } finally {
      setLoading(false);
    }
  }

  async function doAction(action: "start" | "pause" | "cancel") {
    if (!campaign) return;
    setLoading(true);
    try {
      if (action === "start") await campaigns.start(campaign.id);
      else if (action === "pause") await campaigns.pause(campaign.id);
      else await campaigns.cancel(campaign.id);
      const refreshed = await campaigns.get(campaign.id);
      setCampaign(refreshed);
    } catch (e: any) {
      setError(e?.message ?? "Action failed");
    } finally {
      setLoading(false);
    }
  }

  const progress = campaign?.progress ?? {};
  const totalProgress = Object.values(progress).reduce((a, b) => a + b, 0);

  return (
    <>
      <Topbar title="Voice campaigns" subtitle="bulk outbound call management" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24 space-y-6">
        {/* Campaign lookup */}
        <Card>
          <CardContent className="flex items-end gap-3 p-4">
            <div className="flex-1">
              <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Campaign ID
              </label>
              <input
                type="text"
                value={campaignId}
                onChange={(e) => setCampaignId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && loadCampaign()}
                placeholder="paste campaign UUID"
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <Button
              onClick={loadCampaign}
              disabled={loading || !campaignId.trim()}
              size="sm"
            >
              {loading ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
              Load
            </Button>
          </CardContent>
        </Card>

        {error ? (
          <Card className="border-destructive/30 bg-destructive/5">
            <CardContent className="p-3 text-sm text-destructive">{error}</CardContent>
          </Card>
        ) : null}

        {campaign ? (
          <div className="space-y-4">
            {/* Campaign header */}
            <Card>
              <CardContent className="p-5 space-y-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-lg font-bold">{campaign.name}</h2>
                    <p className="text-sm text-muted-foreground">
                      {CALL_KIND_LABELS[campaign.call_kind as CallKind] ?? campaign.call_kind}
                      {" · "}
                      {campaign.total_calls} candidates
                      {" · "}
                      max {campaign.max_concurrent} concurrent
                    </p>
                  </div>
                  <span
                    className={`rounded-full px-3 py-1 font-mono text-[11px] uppercase tracking-[0.1em] ${
                      STATUS_COLORS[campaign.status] ?? STATUS_COLORS.draft
                    }`}
                  >
                    {campaign.status}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex gap-2">
                  {campaign.status === "draft" || campaign.status === "paused" ? (
                    <Button
                      size="sm"
                      onClick={() => doAction("start")}
                      disabled={loading}
                    >
                      <Play className="mr-1 h-3 w-3" />
                      {campaign.status === "paused" ? "Resume" : "Start"}
                    </Button>
                  ) : null}
                  {campaign.status === "dispatching" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => doAction("pause")}
                      disabled={loading}
                    >
                      <Pause className="mr-1 h-3 w-3" /> Pause
                    </Button>
                  ) : null}
                  {campaign.status !== "completed" && campaign.status !== "cancelled" ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => doAction("cancel")}
                      disabled={loading}
                    >
                      <XCircle className="mr-1 h-3 w-3" /> Cancel
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={loadCampaign}
                    disabled={loading}
                  >
                    <RotateCw className="mr-1 h-3 w-3" /> Refresh
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* Progress */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <ProgressCard label="Completed" value={campaign.completed_calls} total={campaign.total_calls} accent="success" />
              <ProgressCard label="Failed" value={campaign.failed_calls} total={campaign.total_calls} accent="danger" />
              <ProgressCard label="In flight" value={progress.dialing ?? 0 + (progress.in_progress ?? 0)} total={campaign.total_calls} accent="info" />
              <ProgressCard label="Pending" value={progress.pending ?? 0} total={campaign.total_calls} accent="warn" />
            </div>

            {/* Detailed status breakdown */}
            {totalProgress > 0 ? (
              <Card>
                <CardContent className="p-4">
                  <h3 className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                    Status breakdown
                  </h3>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                    {Object.entries(progress)
                      .sort(([, a], [, b]) => b - a)
                      .map(([status, count]) => (
                        <div
                          key={status}
                          className="flex items-center justify-between rounded-md bg-muted/30 px-3 py-2"
                        >
                          <span className="text-xs">
                            {status.replace(/_/g, " ")}
                          </span>
                          <span className="font-mono text-sm font-bold tabular-nums">
                            {count}
                          </span>
                        </div>
                      ))}
                  </div>
                </CardContent>
              </Card>
            ) : null}

            {/* Progress bar */}
            <Card>
              <CardContent className="p-4">
                <div className="mb-2 flex justify-between text-xs text-muted-foreground">
                  <span>
                    {campaign.completed_calls + campaign.failed_calls} / {campaign.total_calls} processed
                  </span>
                  <span>
                    {campaign.total_calls > 0
                      ? Math.round(((campaign.completed_calls + campaign.failed_calls) / campaign.total_calls) * 100)
                      : 0}%
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-success transition-all"
                    style={{
                      width: `${campaign.total_calls > 0 ? (campaign.completed_calls / campaign.total_calls) * 100 : 0}%`,
                    }}
                  />
                </div>
              </CardContent>
            </Card>
          </div>
        ) : !error ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <Megaphone className="h-8 w-8 text-primary" />
              <p className="text-base font-bold">Voice campaigns</p>
              <p className="max-w-md text-sm text-muted-foreground">
                Create campaigns via the API to bulk-call candidates with status
                updates, meeting confirmations, or joining details. Enter a campaign
                ID above to track progress.
              </p>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </>
  );
}

function ProgressCard({
  label,
  value,
  total,
  accent,
}: {
  label: string;
  value: number;
  total: number;
  accent: "success" | "danger" | "info" | "warn";
}) {
  const colour =
    accent === "success"
      ? "text-success"
      : accent === "danger"
      ? "text-destructive"
      : accent === "info"
      ? "text-info"
      : "text-warning";
  return (
    <Card>
      <CardContent className="p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </p>
        <p className={`text-2xl font-extrabold tabular-nums ${colour}`}>
          {value}
        </p>
        <p className="text-[10px] text-muted-foreground">
          of {total}
        </p>
      </CardContent>
    </Card>
  );
}
