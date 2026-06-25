"use client";

import { useEffect, useRef, useState } from "react";
import { User } from "lucide-react";
import { cn } from "@/lib/utils";
import { getDashboardKey } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export interface MentionCandidate {
  application_id: string;
  candidate_id?: string;
  name: string | null;
  email: string | null;
  role_title?: string | null;
}

interface Props {
  query: string;
  visible: boolean;
  onPick: (c: MentionCandidate) => void;
  onResults?: (rows: MentionCandidate[]) => void;
}

export function MentionMenu({ query, visible, onPick, onResults }: Props) {
  const [rows, setRows] = useState<MentionCandidate[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!visible) {
      setRows([]);
      onResults?.([]);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const url = `${BASE}/dashboard/v1/candidates-mention?q=${encodeURIComponent(query)}&limit=8`;
        const res = await fetch(url, {
          headers: { "X-Dashboard-Key": getDashboardKey() || "" },
        });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as MentionCandidate[];
        if (!cancelled) {
          setRows(data);
          onResults?.(data);
        }
      } catch {
        if (!cancelled) {
          setRows([]);
          onResults?.([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, visible]);

  if (!visible) return null;
  if (!loading && rows.length === 0) return null;

  return (
    <div className="absolute bottom-full left-0 mb-2 w-80 overflow-hidden rounded-lg border border-border bg-popover shadow-pop">
      <div className="border-b border-border/60 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        Mention a candidate
      </div>
      <ul className="max-h-72 overflow-y-auto py-1 text-sm">
        {loading && rows.length === 0 ? (
          <li className="px-3 py-2 text-xs text-muted-foreground">Searching…</li>
        ) : (
          rows.map((c, i) => (
            <li key={c.application_id}>
              <button
                type="button"
                onClick={() => onPick(c)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left",
                  "hover:bg-accent/40",
                  i === 0 && "bg-accent/30",
                )}
              >
                <User className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-foreground">{c.name || "Unnamed"}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">
                    {c.email}
                  </span>
                  <span className="block text-[10px] text-muted-foreground">
                    {c.role_title ?? "No role"}
                  </span>
                </span>
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
