"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export interface SlashCommand {
  cmd: string;
  hint: string;
  expand: (rest: string) => string; // turns the slash input into the message Pulse sees
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    cmd: "/candidate",
    hint: "Look up a candidate by name or id",
    expand: (rest) => (rest.trim() ? `Find candidate ${rest.trim()}` : "List recent candidates"),
  },
  {
    cmd: "/role",
    hint: "List or filter open roles",
    expand: (rest) => (rest.trim() ? `Show me roles matching ${rest.trim()}` : "List open roles"),
  },
  {
    cmd: "/metrics",
    hint: "Pipeline overview / funnel",
    expand: () => "Show me the pipeline overview.",
  },
  {
    cmd: "/audit",
    hint: "Recent agent activity",
    expand: (rest) =>
      rest.trim()
        ? `Show audit log for application ${rest.trim()}`
        : "Show me recent agent activity.",
  },
  {
    cmd: "/stuck",
    hint: "Applications stuck > 48h",
    expand: () => "Which applications are stuck?",
  },
  {
    cmd: "/help",
    hint: "Show command reference",
    expand: () => "List the slash commands you support.",
  },
];

interface Props {
  query: string;
  onPick: (cmd: SlashCommand) => void;
  visible: boolean;
}

export function SlashMenu({ query, onPick, visible }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const filtered = SLASH_COMMANDS.filter((c) =>
    c.cmd.toLowerCase().startsWith(query.toLowerCase()),
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!visible) return;
      if (e.key === "Tab" && filtered.length > 0) {
        e.preventDefault();
        onPick(filtered[0]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, filtered, onPick]);

  if (!visible || filtered.length === 0) return null;

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 mb-2 w-72 overflow-hidden rounded-lg border border-border bg-popover shadow-pop"
    >
      <ul className="max-h-72 overflow-y-auto py-1 text-sm">
        {filtered.map((c, i) => (
          <li key={c.cmd}>
            <button
              type="button"
              onClick={() => onPick(c)}
              className={cn(
                "flex w-full items-baseline gap-2 px-3 py-1.5 text-left",
                "hover:bg-accent/40",
                i === 0 && "bg-accent/30",
              )}
            >
              <span className="font-mono text-primary">{c.cmd}</span>
              <span className="truncate text-xs text-muted-foreground">{c.hint}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
