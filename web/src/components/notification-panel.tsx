"use client";

import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Bell,
  TrendingUp,
  Mail,
  Activity,
  CheckCheck,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Notification } from "@/lib/use-notifications";

function timeAgo(dateStr: string): string {
  const diff = Math.max(0, Date.now() - new Date(dateStr).getTime());
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function typeIcon(type: string) {
  switch (type) {
    case "approval":
      return <Bell className="h-4 w-4 text-amber-400" />;
    case "hot_lead":
      return <TrendingUp className="h-4 w-4 text-rose-400" />;
    case "reply":
      return <Mail className="h-4 w-4 text-blue-400" />;
    case "discovery":
      return <Activity className="h-4 w-4 text-emerald-400" />;
    default:
      return <Sparkles className="h-4 w-4 text-[var(--text-muted)]" />;
  }
}

function typeBg(type: string) {
  switch (type) {
    case "approval": return "bg-amber-500/10";
    case "hot_lead": return "bg-rose-500/10";
    case "reply": return "bg-blue-500/10";
    case "discovery": return "bg-emerald-500/10";
    default: return "bg-[var(--surface-overlay)]";
  }
}

type NotificationPanelProps = {
  open: boolean;
  onClose: () => void;
  notifications: Notification[];
  unreadCount: number;
  loading: boolean;
  onMarkRead: (id: number) => void;
  onMarkAllRead: () => void;
};

export function NotificationPanel({
  open,
  onClose,
  notifications,
  unreadCount,
  loading,
  onMarkRead,
  onMarkAllRead,
}: NotificationPanelProps) {
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />

          <motion.aside
            initial={{ x: "100%", opacity: 0.5 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: "100%", opacity: 0.5 }}
            transition={{ type: "spring", damping: 28, stiffness: 320 }}
            className="fixed top-0 right-0 z-50 h-full w-full max-w-[400px] border-l border-[var(--border)] bg-[var(--surface)] shadow-2xl flex flex-col"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500/10">
                  <Bell className="h-4 w-4 text-blue-400" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                    Notifications
                  </h2>
                  {unreadCount > 0 && (
                    <p className="text-[11px] text-[var(--text-muted)]">
                      {unreadCount} unread
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {unreadCount > 0 && (
                  <button
                    onClick={onMarkAllRead}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-blue-400 hover:bg-blue-500/10 transition-colors"
                  >
                    <CheckCheck className="h-3.5 w-3.5" />
                    Mark all read
                  </button>
                )}
                <button
                  onClick={onClose}
                  className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-[var(--surface-elevated)] text-[var(--text-muted)] transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto">
              {loading && notifications.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 gap-3">
                  <div className="h-6 w-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                  <span className="text-xs text-[var(--text-muted)]">Loading notifications...</span>
                </div>
              ) : notifications.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-64 gap-4 text-[var(--text-muted)]">
                  <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[var(--surface-elevated)]">
                    <Bell className="h-7 w-7 opacity-30" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-[var(--text-secondary)]">All caught up!</p>
                    <p className="text-xs text-[var(--text-muted)] mt-1">No new notifications</p>
                  </div>
                </div>
              ) : (
                <ul className="p-2 space-y-1">
                  {notifications.map((n, idx) => (
                    <motion.li
                      key={n.id}
                      initial={{ opacity: 0, x: 10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: idx * 0.03 }}
                      onClick={() => {
                        if (!n.read) onMarkRead(n.id);
                      }}
                      className={cn(
                        "flex items-start gap-3 px-3 py-3 rounded-xl cursor-pointer transition-all",
                        "hover:bg-[var(--surface-elevated)]",
                        !n.read && "bg-blue-500/[0.04] border border-blue-500/10",
                        n.read && "border border-transparent"
                      )}
                    >
                      <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl mt-0.5", typeBg(n.type))}>
                        {typeIcon(n.type)}
                      </div>

                      <div className="flex-1 min-w-0 space-y-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className={cn(
                            "text-sm truncate",
                            !n.read ? "font-semibold text-[var(--text-primary)]" : "font-medium text-[var(--text-secondary)]"
                          )}>
                            {n.title}
                          </p>
                          {!n.read && (
                            <div className="h-2 w-2 rounded-full bg-blue-500 shrink-0 mt-1.5" />
                          )}
                        </div>
                        <p className="text-xs text-[var(--text-muted)] line-clamp-2 leading-relaxed">
                          {n.message}
                        </p>
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-[var(--text-subtle)]">
                            {timeAgo(n.created_at)}
                          </span>
                          {n.brand_name && (
                            <span className="text-[11px] text-blue-400/80 truncate">
                              {n.brand_name}
                            </span>
                          )}
                        </div>
                      </div>
                    </motion.li>
                  ))}
                </ul>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
