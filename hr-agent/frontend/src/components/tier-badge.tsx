import { Badge } from "@/components/ui/badge";
import type { FitTier } from "@/lib/types";

export function TierBadge({ tier }: { tier: FitTier | null | undefined }) {
  if (!tier) return <Badge variant="muted">—</Badge>;
  if (tier === "green") return <Badge variant="success">Green</Badge>;
  if (tier === "amber") return <Badge variant="warning">Amber</Badge>;
  return <Badge variant="destructive">Red</Badge>;
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge variant="muted">—</Badge>;
  const terminal = ["rejected", "withdrawn", "erased"].includes(status);
  const success = ["shortlisted", "offered", "scheduled", "interviewed"].includes(status);
  const warn = ["needs_hr_review", "role_unclear", "awaiting_hr_decision", "cold", "scheduling_stalled"].includes(status);
  const variant = terminal ? "destructive" : success ? "success" : warn ? "warning" : "secondary";
  return <Badge variant={variant as any}>{status.replace(/_/g, " ")}</Badge>;
}

export function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-muted-foreground">—</span>;
  const color = score >= 70 ? "bg-success" : score >= 50 ? "bg-warning" : "bg-destructive";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
        <div className={`h-full ${color}`} style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </div>
      <span className="text-xs font-medium tabular-nums">{score}</span>
    </div>
  );
}
