"use client";

import { cn } from "@/lib/utils";

export interface SectionRailItem {
  id: string;
  label: string;
  /** Optional trailing hint — a count, status glyph, etc. */
  hint?: string;
}

/**
 * SectionRail — the persistent left navigator for two-pane workspace pages
 * (role detail, org settings). Clicking an item swaps the section shown in the
 * right pane; only one section is visible at a time. Whitespace + an active
 * accent bar do the work — no boxes.
 */
export function SectionRail({
  items,
  active,
  onSelect,
  className,
}: {
  items: SectionRailItem[];
  active: string;
  onSelect: (id: string) => void;
  className?: string;
}) {
  return (
    <nav className={cn("w-48 shrink-0", className)}>
      <ul className="sticky top-20 space-y-0.5">
        {items.map((it) => {
          const isActive = it.id === active;
          return (
            <li key={it.id}>
              <button
                type="button"
                onClick={() => onSelect(it.id)}
                className={cn(
                  "group flex w-full items-center gap-2.5 rounded-lg py-2 pl-2 pr-3 text-left text-sm transition-colors",
                  isActive
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <span
                  className={cn(
                    "h-4 w-0.5 shrink-0 rounded-full transition-colors",
                    isActive ? "bg-primary" : "bg-transparent group-hover:bg-border",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">{it.label}</span>
                {it.hint && (
                  <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
                    {it.hint}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
