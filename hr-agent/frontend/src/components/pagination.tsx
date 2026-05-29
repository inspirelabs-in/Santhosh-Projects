"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  pageSizeOptions?: number[];
  onLimitChange?: (limit: number) => void;
  className?: string;
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  pageSizeOptions = [25, 50, 100],
  onLimitChange,
  className,
}: PaginationProps) {
  if (total === 0) return null;
  const page = Math.floor(offset / limit) + 1;
  const lastPage = Math.max(1, Math.ceil(total / limit));
  const start = offset + 1;
  const end = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 font-mono text-[11px]",
        className,
      )}
    >
      <div className="text-muted-foreground">
        <span className="tabular-nums">{start}</span>
        <span className="mx-1 text-muted-foreground/60">–</span>
        <span className="tabular-nums">{end}</span>
        <span className="mx-2 text-muted-foreground/60">of</span>
        <span className="tabular-nums">{total}</span>
      </div>

      <div className="flex items-center gap-2">
        {onLimitChange && (
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <span className="uppercase tracking-[0.15em]">per page</span>
            <select
              value={limit}
              onChange={(e) => {
                onLimitChange(Number(e.target.value));
                onChange(0);
              }}
              className="rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px] tabular-nums"
            >
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}

        <Button
          variant="outline"
          size="sm"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="h-7 px-2"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>

        <span className="tabular-nums text-muted-foreground">
          page <span className="text-foreground">{page}</span>
          <span className="mx-1 text-muted-foreground/60">/</span>
          <span className="text-foreground">{lastPage}</span>
        </span>

        <Button
          variant="outline"
          size="sm"
          disabled={!canNext}
          onClick={() => onChange(offset + limit)}
          className="h-7 px-2"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
