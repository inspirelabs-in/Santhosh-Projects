"use client";

import { useTraceStream } from "@/lib/sse";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  Clock,
  DollarSign,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";

export function ReasoningPanel() {
  const traces = useTraceStream();
  const totalCost = traces.reduce((acc, t) => acc + t.total_cost_cents, 0);

  return (
    <aside className="bg-neutral-950 p-4 text-xs">
      <header className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-neutral-500">
          <Activity size={12} />
          Reasoning
        </h2>
        <span className="flex items-center gap-1 text-neutral-500">
          <DollarSign size={10} />
          {(totalCost / 100).toFixed(2)}
        </span>
      </header>
      <AnimatePresence mode="popLayout">
        <ul className="mt-3 space-y-2">
          {traces.length === 0 && (
            <motion.li
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-neutral-600"
            >
              No agent activity yet. Trigger a workflow to see traces.
            </motion.li>
          )}
          {traces.map((t) => (
            <motion.li
              key={t.id}
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2 }}
              className="rounded-lg border border-neutral-900 bg-neutral-900/40 px-3 py-2"
            >
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-neutral-300">
                  {t.status === "ok" ? (
                    <CheckCircle2 size={12} className="text-emerald-500" />
                  ) : (
                    <AlertCircle size={12} className="text-rose-400" />
                  )}
                  {t.agent}
                </span>
                <span className="flex items-center gap-1 text-neutral-500">
                  <Clock size={10} />
                  {t.duration_ms ?? "--"}ms
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between text-neutral-500">
                <span title={t.workflow_id} className="truncate max-w-[160px]">{t.workflow_id.slice(0, 24)}...</span>
                <span className="flex items-center gap-1">
                  <DollarSign size={10} />
                  {(t.total_cost_cents / 100).toFixed(2)}
                </span>
              </div>
              {t.status !== "ok" && (
                <div className="mt-1.5 rounded bg-rose-950/40 px-2 py-1 text-rose-400">
                  {t.status}
                </div>
              )}
            </motion.li>
          ))}
        </ul>
      </AnimatePresence>
    </aside>
  );
}
