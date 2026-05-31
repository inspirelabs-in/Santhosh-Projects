"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2,
  XCircle,
  Clock,
  ArrowLeft,
  Filter,
  Loader2,
  Inbox,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

type Approval = {
  id: number;
  entity_type: string;
  entity_id: number;
  brand_id: number | null;
  status: string;
  reviewer: string | null;
  notes: string | null;
  created_at: string;
  decided_at: string | null;
};

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json", ...(init?.headers as Record<string, string> ?? {}) },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch { return null; }
}

const FILTERS = [
  { key: "pending", label: "Pending", icon: Clock },
  { key: "approved", label: "Approved", icon: CheckCircle2 },
  { key: "rejected", label: "Rejected", icon: XCircle },
  { key: "all", label: "All", icon: Filter },
] as const;

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "approved" | "rejected" | "all">("pending");

  useEffect(() => {
    loadApprovals();
  }, [filter]);

  async function loadApprovals() {
    setLoading(true);
    const qs = filter === "all" ? "" : `?status=${filter}`;
    const data = await fetchApi<{ items: Approval[] }>(`/approvals${qs}`);
    setApprovals(data?.items ?? []);
    setLoading(false);
  }

  async function decide(id: number, status: "approved" | "rejected", notes?: string) {
    await fetchApi(`/approvals/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ status, reviewer: "web_user", notes }),
    });
    await loadApprovals();
  }

  return (
    <main className="overflow-y-auto max-h-[calc(100vh-44px)] bg-neutral-950 p-6">
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-6 flex items-center justify-between"
      >
        <div>
          <h1 className="text-lg font-semibold text-neutral-100">Approval Queue</h1>
          <p className="text-xs text-neutral-500">Review and approve/reject lead actions</p>
        </div>
        <a href="/" className="flex items-center gap-1.5 rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-300 hover:bg-neutral-700 transition-colors">
          <ArrowLeft size={14} />
          Workspace
        </a>
      </motion.header>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="mb-4 flex gap-2"
      >
        {FILTERS.map((f) => {
          const Icon = f.icon;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
                filter === f.key ? "bg-blue-600 text-white" : "bg-neutral-800 text-neutral-400 hover:bg-neutral-700"
              }`}
            >
              <Icon size={12} />
              {f.label}
            </button>
          );
        })}
      </motion.div>

      {loading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-neutral-500">
          <Loader2 size={14} className="animate-spin" />
          Loading...
        </div>
      ) : approvals.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center py-12 text-neutral-500"
        >
          <Inbox size={32} className="mb-2 text-neutral-600" />
          <p className="text-sm">No approvals matching filter.</p>
          <p className="text-xs text-neutral-600 mt-1 max-w-sm text-center">
            Approvals appear when outreach sequences or brand status changes need human review before execution.
          </p>
        </motion.div>
      ) : (
        <div className="space-y-2">
          <AnimatePresence mode="popLayout">
            {approvals.map((a) => (
              <motion.div
                key={a.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: -20 }}
                layout
                className="flex items-center justify-between rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 hover:border-neutral-700 transition-colors"
              >
                <div>
                  <div className="text-sm text-neutral-200">
                    {a.entity_type} #{a.entity_id}
                    {a.brand_id && <span className="ml-2 text-neutral-500">Brand #{a.brand_id}</span>}
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-neutral-500">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${statusColor(a.status)}`}>
                      {statusIcon(a.status)}
                      {a.status}
                    </span>
                    <span>{timeAgo(a.created_at)}</span>
                    {a.reviewer && <span>by {a.reviewer}</span>}
                    {a.notes && <span className="italic">"{a.notes}"</span>}
                  </div>
                </div>
                {a.status === "pending" && (
                  <div className="flex gap-2">
                    <motion.button
                      whileTap={{ scale: 0.95 }}
                      onClick={() => decide(a.id, "approved")}
                      className="flex items-center gap-1.5 rounded-lg bg-emerald-700 px-3 py-1.5 text-xs text-white hover:bg-emerald-600 transition-colors"
                    >
                      <CheckCircle2 size={12} />
                      Approve
                    </motion.button>
                    <motion.button
                      whileTap={{ scale: 0.95 }}
                      onClick={() => {
                        const n = prompt("Rejection reason (optional):");
                        decide(a.id, "rejected", n ?? undefined);
                      }}
                      className="flex items-center gap-1.5 rounded-lg bg-rose-700 px-3 py-1.5 text-xs text-white hover:bg-rose-600 transition-colors"
                    >
                      <XCircle size={12} />
                      Reject
                    </motion.button>
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </main>
  );
}

function statusColor(status: string): string {
  switch (status) {
    case "pending": return "bg-amber-600/20 text-amber-400";
    case "approved": return "bg-emerald-600/20 text-emerald-400";
    case "rejected": return "bg-rose-600/20 text-rose-400";
    default: return "bg-neutral-700/20 text-neutral-400";
  }
}

function statusIcon(status: string) {
  switch (status) {
    case "pending": return <Clock size={10} />;
    case "approved": return <CheckCircle2 size={10} />;
    case "rejected": return <XCircle size={10} />;
    default: return null;
  }
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
