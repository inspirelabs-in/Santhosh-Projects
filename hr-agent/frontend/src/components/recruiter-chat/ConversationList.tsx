"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Briefcase,
  ClipboardCheck,
  MessageCircle,
  PhoneCall,
  Plus,
  Search,
  Settings,
  Trash2,
  Users,
  Video,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/brand/logo";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/lib/useRecruiterChat";

const WORKSPACE_LINKS: Array<{
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { href: "/candidates", label: "Candidates", icon: Users },
  { href: "/roles", label: "Roles", icon: Briefcase },
  { href: "/voice-screens", label: "Voice screens", icon: PhoneCall },
  { href: "/assessments", label: "Assessments", icon: ClipboardCheck },
  { href: "/meetings", label: "Meetings", icon: Video },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onArchive: (id: string) => void;
}

type Bucket = "today" | "yesterday" | "thisWeek" | "older";

const BUCKET_LABEL: Record<Bucket, string> = {
  today: "Today",
  yesterday: "Yesterday",
  thisWeek: "This week",
  older: "Older",
};

function bucketize(iso: string): Bucket {
  const t = new Date(iso);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const tStart = new Date(t.getFullYear(), t.getMonth(), t.getDate());
  const dayDiff = Math.round((start.getTime() - tStart.getTime()) / (1000 * 60 * 60 * 24));
  if (dayDiff <= 0) return "today";
  if (dayDiff === 1) return "yesterday";
  if (dayDiff < 7) return "thisWeek";
  return "older";
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" });
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onArchive,
}: Props) {
  const [q, setQ] = useState("");

  const groups = useMemo(() => {
    const filtered = q.trim()
      ? conversations.filter((c) => (c.title || "").toLowerCase().includes(q.toLowerCase()))
      : conversations;
    const out: Record<Bucket, ConversationSummary[]> = {
      today: [],
      yesterday: [],
      thisWeek: [],
      older: [],
    };
    for (const c of filtered) out[bucketize(c.updated_at)].push(c);
    return out;
  }, [conversations, q]);

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-r border-border/50 bg-muted/15">
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
        <Logo width={28} kind="icon" />
        <div className="flex-1">
          <div className="text-sm font-extrabold leading-tight">GrabOn Hiring</div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Pulse
          </div>
        </div>
      </div>

      <div className="space-y-2 p-3">
        <Button
          onClick={onNew}
          variant="default"
          size="sm"
          className="w-full justify-start gap-2 shadow-sm"
        >
          <Plus className="h-4 w-4" /> New chat
        </Button>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats"
            className="w-full rounded-md border border-border bg-background py-1 pl-7 pr-2 text-xs outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-1.5 py-1 scrollbar-slim">
        {(["today", "yesterday", "thisWeek", "older"] as Bucket[]).map((bucket) => {
          const items = groups[bucket];
          if (items.length === 0) return null;
          return (
            <section key={bucket} className="mb-2">
              <div className="px-2 pb-1 pt-2 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
                {BUCKET_LABEL[bucket]}
              </div>
              <ul className="space-y-0.5">
                {items.map((c) => (
                  <li key={c.id}>
                    <button
                      onClick={() => onSelect(c.id)}
                      className={cn(
                        "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                        activeId === c.id
                          ? "bg-primary/10 text-foreground"
                          : "hover:bg-accent/30 text-foreground/80",
                      )}
                    >
                      <MessageCircle
                        className={cn(
                          "h-3.5 w-3.5 shrink-0",
                          activeId === c.id ? "text-primary" : "text-muted-foreground",
                        )}
                      />
                      <span className="flex-1 truncate">{c.title || "Untitled"}</span>
                      <span className="text-[10px] text-muted-foreground tabular-nums">
                        {fmtTime(c.updated_at)}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm("Archive?")) onArchive(c.id);
                        }}
                        className="opacity-0 group-hover:opacity-100"
                        aria-label="Archive"
                      >
                        <Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                      </button>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}

        {Object.values(groups).every((g) => g.length === 0) && (
          <p className="px-3 py-4 text-xs text-muted-foreground">
            {q ? "No matches." : "No chats yet. Tap New chat."}
          </p>
        )}
      </div>

      {/* Workspace nav -- previously the global app sidebar. Inlined here so
          /dashboard has a single sidebar instead of two. */}
      <div className="border-t border-border/40 p-2">
        <div className="px-2 pb-1 pt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
          Workspace
        </div>
        <ul className="space-y-0.5">
          {WORKSPACE_LINKS.map(({ href, label, icon: Icon }) => (
            <li key={href}>
              <Link
                href={href}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-foreground/75 hover:bg-accent/40 hover:text-foreground"
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{label}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
