"use client";

import Link from "next/link";
import {
  Briefcase,
  ClipboardList,
  ListChecks,
  Settings,
  MessageCircle,
  Plus,
  Trash2,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/lib/useRecruiterChat";

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onArchive: (id: string) => void;
}

const NAV_LINKS: Array<{ href: string; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { href: "/candidates", label: "Candidates", icon: Users },
  { href: "/roles", label: "Roles", icon: Briefcase },
  { href: "/assessments", label: "Assessments", icon: ListChecks },
  { href: "/meetings", label: "Meetings", icon: ClipboardList },
  { href: "/settings", label: "Settings", icon: Settings },
];

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" });
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onArchive,
}: Props) {
  return (
    <aside className="flex h-dvh w-72 shrink-0 flex-col border-r border-border/60 bg-muted/30">
      <div className="border-b border-border/60 p-3">
        <Button
          onClick={onNew}
          variant="default"
          size="sm"
          className="w-full justify-start gap-2"
        >
          <Plus className="h-4 w-4" /> New chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        <div className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Recent
        </div>
        {conversations.length === 0 ? (
          <div className="px-2 py-3 text-xs text-muted-foreground">
            No conversations yet. Hit <span className="font-mono">New chat</span>.
          </div>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => onSelect(c.id)}
                  className={cn(
                    "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                    activeId === c.id
                      ? "bg-primary/10 text-foreground"
                      : "hover:bg-accent/40 text-foreground/80",
                  )}
                >
                  <MessageCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">{c.title || "Untitled"}</span>
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    {fmtTime(c.updated_at)}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm("Archive this chat?")) onArchive(c.id);
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
        )}
      </div>

      <div className="border-t border-border/60 p-2">
        <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Open in classic view
        </div>
        <ul className="space-y-0.5">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => (
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
    </aside>
  );
}
