import { cn } from "@/lib/utils";

type Stage = {
  key: string;
  label: string;
  /** Which backend statuses resolve to this stage. */
  matches: string[];
};

const STAGES: Stage[] = [
  { key: "decision", label: "Awaiting decision", matches: ["classified", "needs_hr_review", "role_unclear", "awaiting_hr_decision"] },
  { key: "screening", label: "Screening", matches: ["shortlisted", "screening_in_progress", "screening_sent"] },
  { key: "scored", label: "Scored", matches: ["screening_completed", "screening_scored"] },
  { key: "slots", label: "Slots proposed", matches: ["scheduling_stalled"] },
  { key: "scheduled", label: "Scheduled", matches: ["scheduled", "interviewed", "offered"] },
];

const TERMINAL = new Set(["rejected", "withdrawn", "erased"]);
const COLD = new Set(["cold"]);

function stageIndex(status: string | null | undefined): number {
  if (!status) return 0;
  if (status === "scheduled" || status === "interviewed" || status === "offered") return 4;
  // Heuristic: shortlisted without screening_score → "Screening"; with score → "Scored"; with slots_proposed audit → "Slots proposed"
  for (let i = 0; i < STAGES.length; i++) {
    if (STAGES[i].matches.includes(status)) return i;
  }
  return 0;
}

export function PipelineStage({
  status,
  screeningScore,
}: {
  status: string | null | undefined;
  screeningScore?: number | null;
}) {
  if (!status) return <span className="text-muted-foreground text-xs">—</span>;
  if (TERMINAL.has(status)) {
    return <span className="text-xs font-medium text-destructive">Terminal · {status}</span>;
  }
  if (COLD.has(status)) {
    return <span className="text-xs font-medium text-info">Cold pool</span>;
  }

  let idx = stageIndex(status);
  // If shortlisted AND screening_score present, upgrade to "Scored".
  if (status === "shortlisted" && screeningScore != null) idx = Math.max(idx, 2);

  return (
    <div className="flex items-center gap-1" title={STAGES[idx]?.label + ` (status: ${status})`}>
      {STAGES.map((s, i) => {
        const active = i <= idx;
        const current = i === idx;
        return (
          <div key={s.key} className="flex items-center">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full transition",
                active ? "bg-primary" : "bg-muted",
                current && "ring-2 ring-primary/30 ring-offset-1 ring-offset-card",
              )}
            />
            {i < STAGES.length - 1 ? (
              <span
                className={cn(
                  "h-[2px] w-4",
                  i < idx ? "bg-primary" : "bg-muted",
                )}
              />
            ) : null}
          </div>
        );
      })}
      <span className="ml-2 text-xs font-medium text-foreground">{STAGES[idx]?.label ?? "—"}</span>
    </div>
  );
}
