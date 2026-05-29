"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";

const SHORTCUTS = [
  { keys: ["⌘", "K"], label: "Command palette" },
  { keys: ["⌘", "B"], label: "Toggle sidebar" },
  { keys: ["G", "then", "D"], label: "Go to Dashboard" },
  { keys: ["G", "then", "C"], label: "Go to Candidates" },
  { keys: ["G", "then", "R"], label: "Go to Roles" },
  { keys: ["G", "then", "A"], label: "Go to Analytics" },
  { keys: ["G", "then", "S"], label: "Go to Settings" },
  { keys: ["?"], label: "Show this dialog" },
];

export function KeyboardHelpDialog() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "?" && !e.metaKey && !e.ctrlKey && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center" onClick={() => setOpen(false)}>
      <div className="fixed inset-0 bg-foreground/20 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-sm rounded-xl border border-border bg-popover p-5 shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-bold">Keyboard shortcuts</h2>
          <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <ul className="space-y-2">
          {SHORTCUTS.map((s) => (
            <li key={s.label} className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">{s.label}</span>
              <div className="flex items-center gap-1">
                {s.keys.map((k, i) =>
                  k === "then" ? (
                    <span key={i} className="text-[10px] text-muted-foreground">then</span>
                  ) : (
                    <kbd key={i} className="rounded border border-border bg-muted px-1.5 py-0.5 font-data text-[10px]">
                      {k}
                    </kbd>
                  ),
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
