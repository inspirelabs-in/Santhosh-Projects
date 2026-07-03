import { Badge } from "@/components/ui/badge";
import type { FitTier } from "@/lib/types";

export function TierBadge({ tier }: { tier: FitTier | null | undefined }) {
  if (!tier) return <Badge variant="muted">—</Badge>;
  if (tier === "green" || tier === "amber") return <Badge variant="success">{tier === "amber" ? "Pass" : "Pass"}</Badge>;
  return <Badge variant="destructive">Reject</Badge>;
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge variant="muted">—</Badge>;
  const terminal = ["rejected", "withdrawn", "erased"].includes(status);
  const success = ["shortlisted", "offered", "scheduled", "interviewed"].includes(status);
  const warn = ["needs_hr_review", "needs_review", "role_unclear", "awaiting_hr_decision", "cold", "scheduling_stalled"].includes(status);
  const variant = terminal ? "destructive" : success ? "success" : warn ? "warning" : "secondary";
  return <Badge variant={variant as any}>{status.replace(/_/g, " ")}</Badge>;
}

export function ScoreChip({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-sm text-muted-foreground">—</span>;
  const rounded = Math.round(score);
  const tone =
    rounded >= 70
      ? "bg-emerald-500/15 text-emerald-600"
      : rounded >= 40
        ? "bg-amber-500/15 text-amber-600"
        : "bg-destructive/15 text-destructive";
  return (
    <span
      className={`inline-flex h-6 min-w-[2.5rem] items-center justify-center rounded-md px-2 font-mono text-xs font-bold tabular-nums ${tone}`}
    >
      {rounded}
    </span>
  );
}
