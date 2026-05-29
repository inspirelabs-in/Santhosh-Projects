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
import { api } from "@/lib/api";
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

const WORKSPACE_LINKS = [
  { href: "/dashboard", label: "Overview", icon: Activity },
  { href: "/candidates", label: "Candidates", icon: Users },
  { href: "/roles", label: "Roles", icon: Briefcase },
  { href: "/voice-screens", label: "Voice screens", icon: PhoneCall },
  { href: "/assessments", label: "Assessments", icon: ClipboardCheck },
  { href: "/meetings", label: "Meetings", icon: Video },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

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

  async function wipeAll() {
    const ok = await confirm({
      title: "Wipe all data?",
      description:
        "Removes every candidate, application, role, conversation, and audit entry. Dev-only action and cannot be undone.",
      confirmLabel: "Wipe everything",
      cancelLabel: "Keep data",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api.post("/dashboard/v1/dev/reset", {});
      window.location.reload();
    } catch (e) {
      await confirm({
        title: "Reset failed",
        description: e instanceof Error ? e.message : "Unknown error.",
        confirmLabel: "OK",
        cancelLabel: "Close",
      });
    }
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

  // Avoid CLS on first render: render placeholder until localStorage hydrated.
  if (!hydrated) {
    return <aside className="h-screen w-72 shrink-0 border-r border-border/50 bg-muted/15" />;
  }

  if (collapsed) {
    return (
      <aside className="relative flex h-screen w-16 shrink-0 flex-col border-r border-border/50 bg-muted/15">
        {confirmDialog}
        {/* Edge toggle */}
        <button
          type="button"
          onClick={toggleCollapsed}
          className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent/40 hover:text-foreground"
          aria-label="Expand sidebar"
          title="Expand"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>

        {/* Brand header (icon-only, same vertical rhythm as expanded) */}
        <div className="flex items-center justify-center border-b border-border/40 px-2 py-3">
          <Logo width={28} kind="icon" />
        </div>

        {/* New chat -- matches expanded green button visually */}
        <div className="px-2 py-3">
          <Button
            onClick={() => void chat.newConversation()}
            variant="default"
            size="icon"
            className="h-9 w-full rounded-md shadow-sm"
            title="New chat"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>

        {/* Workspace nav -- icons styled identically to expanded rows */}
        <nav className="scrollbar-slim flex-1 overflow-y-auto px-2 py-1">
          <ul className="space-y-0.5">
            {WORKSPACE_LINKS.map(({ href, label, icon: Icon }) => {
              const active =
                pathname === href ||
                (href !== "/dashboard" && pathname?.startsWith(href));
              return (
                <li key={href}>
                  <Link
                    href={href}
                    title={label}
                    className={cn(
                      "flex h-9 w-full items-center justify-center rounded-md transition-colors",
                      active
                        ? "bg-primary/10 text-primary"
                        : "text-foreground/70 hover:bg-accent/40 hover:text-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* Agent status */}
        <div className="border-t border-border/40">
          <AgentStatusBadge collapsed />
        </div>

        {/* Dev wipe icon -- same red treatment as expanded */}
        <div className="border-t border-border/40 p-2">
          <button
            type="button"
            onClick={() => void wipeAll()}
            className="flex h-8 w-full items-center justify-center rounded-md border border-destructive/30 bg-destructive/5 text-destructive transition hover:bg-destructive/10"
            title="Dev: wipe all data"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Sign out */}
        <div className="border-t border-border/40 p-2">
          <button
            type="button"
            onClick={signOut}
            className="flex h-8 w-full items-center justify-center rounded-md text-muted-foreground hover:bg-accent/40 hover:text-foreground"
            title="Sign out"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="relative flex h-screen w-72 shrink-0 flex-col border-r border-border/50 bg-muted/15">
      {confirmDialog}
      <button
        type="button"
        onClick={toggleCollapsed}
        className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent/40 hover:text-foreground"
        aria-label="Collapse sidebar"
        title="Collapse"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>

      {/* Brand header */}
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
        <Logo width={28} kind="icon" />
        <div className="flex-1">
          <div className="text-sm font-extrabold leading-tight">GrabOn Hiring</div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Pulse
          </div>
        </div>
      </div>

      {/* Workspace nav — always visible at top */}
      <nav className="border-b border-border/40 px-2 py-2">
        <ul className="grid grid-cols-4 gap-1">
          {WORKSPACE_LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== "/dashboard" && pathname?.startsWith(href));
            return (
              <li key={href}>
                <Link
                  href={href}
                  title={label}
                  className={cn(
                    "flex flex-col items-center gap-0.5 rounded-md px-1 py-1.5 transition-colors",
                    active
                      ? "bg-primary/10 text-primary"
                      : "text-foreground/60 hover:bg-accent/30 hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="text-[9px] leading-tight">{label.length > 9 ? label.split(" ")[0] : label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Agent status — compact inline */}
      <AgentStatusBadge />

      {/* Chat section */}
      <div className="flex items-center gap-1.5 px-3 pt-2 pb-1">
        <div className="flex-1 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
          Chats
        </div>
        <Button
          onClick={() => void newConv()}
          variant="default"
          size="icon"
          className="h-6 w-6 rounded-md shadow-sm"
          title="New chat"
        >
          <Plus className="h-3 w-3" />
        </Button>
      </div>
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats"
            className="w-full rounded-md border border-border bg-background py-1 pl-7 pr-2 text-[11px] outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          />
        </div>
      </div>

      {/* Conversation list — scrollable */}
      <div className="scrollbar-slim flex-1 overflow-y-auto px-1.5 py-0.5">
        {(["today", "yesterday", "thisWeek", "older"] as Bucket[]).map((b) => {
          const items = groups[b];
          if (items.length === 0) return null;
          return (
            <section key={b} className="mb-1">
              <div className="px-2 pb-0.5 pt-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
                {BUCKET_LABEL[b]}
              </div>
              <ul className="space-y-px">
                {items.map((c) => (
                  <li key={c.id}>
                    <button
                      onClick={() => pickConv(c.id)}
                      className={cn(
                        "group flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[13px] transition-colors",
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
                        className="opacity-0 group-hover:opacity-100"
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
          <p className="px-3 py-4 text-xs text-muted-foreground">
            {q ? "No matches." : "No chats yet — start one above."}
          </p>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border/40 px-3 py-1.5">
        <button
          type="button"
          onClick={() => void wipeAll()}
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 font-mono text-[10px] text-destructive transition hover:bg-destructive/10"
          title="Dev-only: wipe all data"
        >
          <Trash2 className="h-2.5 w-2.5" />
          wipe all data
        </button>
      </div>
      <div className="flex items-center justify-between border-t border-border/40 px-3 py-1.5 text-[10px] text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <KeyRound className="h-2.5 w-2.5" />
          <span>Admin</span>
        </div>
        <button
          onClick={signOut}
          className="inline-flex items-center gap-1 hover:text-foreground"
          type="button"
        >
          <LogOut className="h-2.5 w-2.5" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
