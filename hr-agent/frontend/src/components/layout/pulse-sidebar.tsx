"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  BarChart3,
  Briefcase,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  KeyRound,
  LogOut,
  MessageCircle,
  PhoneCall,
  Plus,
  Search,
  Settings,
  Trash2,
  Users,
  Video,
} from "lucide-react";
import { Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useRecruiterChatCtx } from "@/lib/RecruiterChatProvider";
import { clearDashboardKey } from "@/lib/auth";
import type { ConversationSummary } from "@/lib/useRecruiterChat";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { AgentStatusBadge } from "@/components/agent-status-badge";

const COLLAPSE_KEY = "pulse_sidebar_collapsed";

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
  const dayDiff = Math.round((start.getTime() - tStart.getTime()) / 86400000);
  if (dayDiff <= 0) return "today";
  if (dayDiff === 1) return "yesterday";
  if (dayDiff < 7) return "thisWeek";
  return "older";
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" });
}

type NavItem = { href: string; label: string; icon: typeof Activity };
type NavGroup = { section: string; items: NavItem[] };

const NAV_GROUPS: NavGroup[] = [
  {
    section: "workspace",
    items: [
      { href: "/dashboard", label: "Overview", icon: Activity },
      { href: "/candidates", label: "Candidates", icon: Users },
      { href: "/roles", label: "Roles", icon: Briefcase },
    ],
  },
  {
    section: "engage",
    items: [
      { href: "/voice-screens", label: "Voice", icon: PhoneCall },
      { href: "/assessments", label: "Assessments", icon: ClipboardCheck },
      { href: "/meetings", label: "Meetings", icon: Video },
      // Panels hidden — meeting/panel scheduling is now chat-driven via Pulse.
      // { href: "/panels", label: "Panels", icon: UserCheck },
    ],
  },
  {
    section: "intelligence",
    items: [
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      // Supervisor tab hidden.
      // { href: "/supervisor", label: "Supervisor", icon: Bot },
    ],
  },
  {
    section: "system",
    items: [
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

const ALL_LINKS = NAV_GROUPS.flatMap((g) => g.items);

export function PulseSidebar() {
  const chat = useRecruiterChatCtx();
  const pathname = usePathname();
  const router = useRouter();
  const isDashboard = pathname === "/dashboard";
  const { confirm, dialog: confirmDialog } = useConfirm();

  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const v = window.localStorage.getItem(COLLAPSE_KEY);
      if (v === "1") setCollapsed(true);
    } catch {
      /* ignore */
    }
    setHydrated(true);
  }, []);

  function toggleCollapsed() {
    setCollapsed((v) => {
      const next = !v;
      try {
        window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  async function askDelete(conv: ConversationSummary) {
    const ok = await confirm({
      title: "Delete this chat?",
      description: (
        <>
          <span className="font-medium text-foreground">
            {conv.title || "Untitled chat"}
          </span>
          {" "}will be archived and removed from your sidebar. You can still find it
          in audit logs.
        </>
      ),
      confirmLabel: "Delete chat",
      cancelLabel: "Keep",
      tone: "danger",
    });
    if (ok) await chat.archiveConversation(conv.id);
  }

  const groups = useMemo(() => {
    const filtered = q.trim()
      ? chat.conversations.filter((c) =>
          (c.title || "").toLowerCase().includes(q.toLowerCase()),
        )
      : chat.conversations;
    const out: Record<Bucket, ConversationSummary[]> = {
      today: [],
      yesterday: [],
      thisWeek: [],
      older: [],
    };
    for (const c of filtered) out[bucketize(c.updated_at)].push(c);
    return out;
  }, [chat.conversations, q]);

  function pickConv(id: string) {
    chat.selectConversation(id);
    if (!isDashboard) router.push("/dashboard");
  }

  async function newConv() {
    await chat.newConversation();
    if (!isDashboard) router.push("/dashboard");
  }

  function signOut() {
    clearDashboardKey();
    router.push("/login");
  }

  if (!hydrated) {
    return <aside className="h-screen w-[268px] shrink-0 border-r border-border/50 bg-card/50" />;
  }

  // ── Collapsed state ──────────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className="relative flex h-screen w-[56px] shrink-0 flex-col border-r border-border/50 bg-card/50">
        {confirmDialog}
        <button
          type="button"
          onClick={toggleCollapsed}
          className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent/40 hover:text-foreground transition-colors"
          aria-label="Expand sidebar"
          title="Expand"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>

        <div className="flex items-center justify-center border-b border-border/40 px-2 py-3">
          <Logo width={26} kind="icon" />
        </div>

        <div className="px-1.5 py-2">
          <Button
            onClick={() => void newConv()}
            variant="default"
            size="icon"
            className="h-8 w-full rounded-lg shadow-sm"
            title="New chat"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>

        <nav className="scrollbar-slim flex-1 overflow-y-auto px-1.5 py-1">
          {NAV_GROUPS.map((group) => (
            <div key={group.section} className="mb-2">
              <div className="mx-auto my-1 h-px w-6 bg-border/60" />
              <ul className="space-y-0.5">
                {group.items.map(({ href, label, icon: Icon }) => {
                  const active =
                    pathname === href ||
                    (href !== "/dashboard" && pathname?.startsWith(href));
                  return (
                    <li key={href}>
                      <Link
                        href={href}
                        title={label}
                        className={cn(
                          "flex h-8 w-full items-center justify-center rounded-lg transition-colors",
                          active
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                        )}
                      >
                        <Icon className="h-4 w-4" />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-t border-border/40">
          <AgentStatusBadge collapsed />
        </div>

        <div className="border-t border-border/40 p-1.5">
          <button
            type="button"
            onClick={signOut}
            className="flex h-8 w-full items-center justify-center rounded-lg text-muted-foreground hover:bg-accent/40 hover:text-foreground transition-colors"
            title="Sign out"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </aside>
    );
  }

  // ── Expanded state ───────────────────────────────────────────────────
  return (
    <aside className="relative flex h-screen w-[268px] shrink-0 flex-col overflow-hidden border-r border-border/50 bg-card/50">
      {confirmDialog}
      <button
        type="button"
        onClick={toggleCollapsed}
        className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent/40 hover:text-foreground transition-colors"
        aria-label="Collapse sidebar"
        title="Collapse"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>

      {/* Brand */}
      <div className="flex items-center gap-2.5 border-b border-border/40 px-4 py-3">
        <Logo width={26} kind="icon" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-bold leading-tight tracking-tight">GrabOn Hiring</div>
          <div className="text-[9px] font-medium uppercase tracking-[0.2em] text-primary/70">
            Pulse
          </div>
        </div>
      </div>

      {/* Navigation — capped so the chat list always keeps room on short screens */}
      <nav className="scrollbar-slim max-h-[40vh] shrink-0 overflow-y-auto border-b border-border/40 px-2 py-2">
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.section} className={gi > 0 ? "mt-3" : ""}>
            <div className="px-2 pb-1 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground/70">
              {group.section}
            </div>
            <ul className="space-y-px">
              {group.items.map(({ href, label, icon: Icon }) => {
                const active =
                  pathname === href ||
                  (href !== "/dashboard" && pathname?.startsWith(href));
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors",
                        active
                          ? "bg-primary/10 text-primary"
                          : "text-foreground/70 hover:bg-accent/30 hover:text-foreground",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span>{label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Agent status */}
      <AgentStatusBadge />

      {/* Chat header */}
      <div className="px-3 pt-2 pb-1">
        <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
          Chats
        </div>
      </div>
      <div className="flex items-center gap-1.5 px-3 pb-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats"
            className="w-full rounded-lg border border-border bg-background py-1 pl-7 pr-2 text-[11px] outline-none placeholder:text-muted-foreground/60 focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          />
        </div>
        <Button
          onClick={() => void newConv()}
          variant="default"
          size="icon"
          className="h-6 w-6 shrink-0 rounded-md shadow-sm"
          title="New chat"
        >
          <Plus className="h-3 w-3" />
        </Button>
      </div>

      {/* Conversation list */}
      <div className="scrollbar-slim min-h-0 flex-1 overflow-y-auto px-1.5 py-0.5">
        {(["today", "yesterday", "thisWeek", "older"] as Bucket[]).map((b) => {
          const items = groups[b];
          if (items.length === 0) return null;
          return (
            <section key={b} className="mb-1">
              <div className="px-2 pb-0.5 pt-2 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground/70">
                {BUCKET_LABEL[b]}
              </div>
              <ul className="space-y-px">
                {items.map((c) => (
                  <li key={c.id}>
                    <button
                      onClick={() => pickConv(c.id)}
                      className={cn(
                        "group flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors",
                        chat.conversationId === c.id && isDashboard
                          ? "bg-primary/10 text-foreground"
                          : "hover:bg-accent/30 text-foreground/80",
                      )}
                    >
                      <MessageCircle
                        className={cn(
                          "h-3.5 w-3.5 shrink-0",
                          chat.conversationId === c.id && isDashboard
                            ? "text-primary"
                            : "text-muted-foreground",
                        )}
                      />
                      <span className="flex-1 truncate">{c.title || "Untitled"}</span>
                      <span className="text-[9px] tabular-nums text-muted-foreground">
                        {fmtTime(c.updated_at)}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void askDelete(c);
                        }}
                        className="opacity-0 group-hover:opacity-100 transition-opacity"
                        aria-label="Delete chat"
                        title="Delete chat"
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
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {q ? "No matches." : "No chats yet — start one above."}
          </p>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border/40 px-3 py-2 space-y-1.5">
        <button
          type="button"
          onClick={async () => {
            const ok = await confirm({
              title: "Wipe all data?",
              description: "This will permanently delete all candidates, applications, roles, and audit entries. Dev only — cannot be undone.",
              confirmLabel: "Wipe everything",
              cancelLabel: "Cancel",
              tone: "danger",
            });
            if (!ok) return;
            try {
              const { api } = await import("@/lib/api");
              await api.post("/dashboard/v1/dev/reset");
              window.location.reload();
            } catch (e: unknown) {
              alert(`Wipe failed: ${e instanceof Error ? e.message : e}`);
            }
          }}
          className="flex w-full items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 font-mono text-[10px] text-destructive transition hover:bg-destructive/10"
        >
          <Trash2 className="h-3 w-3" />
          Wipe all data
        </button>
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <KeyRound className="h-3 w-3" />
            <span className="font-medium">Admin</span>
          </div>
          <button
            onClick={signOut}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-accent/40 hover:text-foreground transition-colors"
            type="button"
          >
            <LogOut className="h-3 w-3" />
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
