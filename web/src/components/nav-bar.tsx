"use client";

import { useEffect, useState, useRef } from "react";
import { useTheme } from "./theme-provider";
import { NotificationPanel } from "./notification-panel";
import { useNotifications } from "@/lib/use-notifications";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  LayoutDashboard,
  CheckCircle2,
  GitCompare,
  Bot,
  LayoutList,
  Sun,
  Moon,
  Bell,
  Zap,
  Search,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

const NAV_ITEMS = [
  { href: "/", label: "Workspace", icon: MessageSquare },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/pipeline", label: "Pipeline", icon: LayoutList },
  { href: "/agent", label: "Agent", icon: Bot },
  { href: "/approvals", label: "Approvals", icon: CheckCircle2 },
  { href: "/compare", label: "Compare", icon: GitCompare },
];

type AgentLiveStatus = "active" | "running" | "idle";

function useAgentStatus(): AgentLiveStatus {
  const [agentStatus, setAgentStatus] = useState<AgentLiveStatus>("idle");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    async function check() {
      try {
        const res = await fetch(`${API_BASE}/traces?limit=5`, {
          headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        const items = data.items ?? [];
        if (!mountedRef.current) return;
        const hasRunning = items.some((t: { status: string }) => t.status === "running");
        if (hasRunning) { setAgentStatus("running"); return; }
        const hasRecent = items.some((t: { created_at: string }) => {
          const diff = Date.now() - new Date(t.created_at).getTime();
          return diff < 600_000;
        });
        setAgentStatus(hasRecent ? "active" : "idle");
      } catch { /* ignore */ }
    }
    check();
    const interval = setInterval(check, 15_000);
    return () => { mountedRef.current = false; clearInterval(interval); };
  }, []);

  return agentStatus;
}

export function NavBar() {
  const { theme, toggle } = useTheme();
  const { notifications, unreadCount, loading, markRead, markAllRead, reload } = useNotifications();
  const [notifOpen, setNotifOpen] = useState(false);
  const [path, setPath] = useState("/");
  const agentStatus = useAgentStatus();

  useEffect(() => {
    setPath(window.location.pathname);
  }, []);

  return (
    <>
      <nav className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--surface-elevated)]/80 backdrop-blur-xl px-5 h-14 sticky top-0 z-30">
        {/* Left: Logo + Nav */}
        <div className="flex items-center gap-1">
          <a href="/" className="flex items-center gap-2.5 mr-6 group">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-600 shadow-md shadow-blue-600/25 group-hover:shadow-blue-600/40 transition-shadow">
              <Zap size={15} className="text-white" />
            </div>
            <span className="text-sm font-bold text-[var(--text-primary)] tracking-tight">
              Grabon Intel
            </span>
          </a>

          <div className="flex items-center gap-0.5 bg-[var(--surface)]/50 rounded-xl p-1">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = path === item.href;
              return (
                <a
                  key={item.href}
                  href={item.href}
                  className={`relative flex items-center gap-2 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-all ${
                    active
                      ? "text-[var(--text-primary)] bg-[var(--surface-elevated)] shadow-sm"
                      : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-overlay)]/50"
                  }`}
                >
                  <span className="relative">
                    <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
                    {item.label === "Agent" && agentStatus !== "idle" && (
                      <span className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full ${
                        agentStatus === "running" ? "bg-blue-400 agent-heartbeat" : "bg-emerald-400 agent-heartbeat-slow"
                      }`} />
                    )}
                  </span>
                  <span className="hidden lg:inline">{item.label}</span>
                  {active && (
                    <motion.div
                      layoutId="nav-active"
                      className="absolute inset-x-2 -bottom-[13px] h-[2px] rounded-full bg-blue-500"
                      transition={{ type: "spring", stiffness: 500, damping: 35 }}
                    />
                  )}
                </a>
              );
            })}
          </div>
        </div>

        {/* Right: Search + Notifications + Theme + Avatar */}
        <div className="flex items-center gap-1.5">
          <button className="flex items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-xs text-[var(--text-muted)] hover:border-[var(--border-strong)] transition-colors">
            <Search size={13} strokeWidth={2} />
            <span className="hidden sm:inline">Search leads...</span>
            <kbd className="ml-1 rounded-md border border-[var(--border-strong)] bg-[var(--surface-overlay)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-subtle)]">
              ⌘K
            </kbd>
          </button>

          <button
            onClick={() => setNotifOpen(true)}
            className="relative flex items-center justify-center rounded-xl w-9 h-9 text-[var(--text-secondary)] hover:bg-[var(--surface-overlay)] hover:text-[var(--text-primary)] transition-all"
          >
            <Bell size={16} />
            <AnimatePresence>
              {unreadCount > 0 && (
                <motion.span
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  exit={{ scale: 0 }}
                  className="absolute -top-0.5 -right-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white shadow-sm shadow-rose-500/30"
                >
                  {unreadCount > 99 ? "99+" : unreadCount}
                </motion.span>
              )}
            </AnimatePresence>
          </button>

          <button
            onClick={toggle}
            className="flex items-center justify-center rounded-xl w-9 h-9 text-[var(--text-secondary)] hover:bg-[var(--surface-overlay)] hover:text-[var(--text-primary)] transition-all"
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            <AnimatePresence mode="wait" initial={false}>
              {theme === "dark" ? (
                <motion.div key="sun" initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={{ duration: 0.15 }}>
                  <Sun size={16} />
                </motion.div>
              ) : (
                <motion.div key="moon" initial={{ rotate: 90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: -90, opacity: 0 }} transition={{ duration: 0.15 }}>
                  <Moon size={16} />
                </motion.div>
              )}
            </AnimatePresence>
          </button>

          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-xs font-bold text-white shadow-sm cursor-pointer hover:shadow-lg hover:shadow-blue-500/20 transition-all">
            H
          </div>
        </div>
      </nav>

      <NotificationPanel
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        notifications={notifications}
        unreadCount={unreadCount}
        loading={loading}
        onMarkRead={markRead}
        onMarkAllRead={markAllRead}
      />
    </>
  );
}
