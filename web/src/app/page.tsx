"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { ChatWorkspace } from "@/components/chat-workspace";
import { PipelineRail } from "@/components/pipeline-rail";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

export default function Workspace() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const router = useRouter();

  const handleSelectBrand = useCallback((id: number) => {
    router.push(`/brands/${id}`);
  }, [router]);

  return (
    <main className="flex h-full bg-[var(--bg-primary)] relative">
      {/* Sidebar */}
      <aside
        className={`hidden md:flex shrink-0 border-r border-[var(--border)] transition-all duration-200 ease-in-out overflow-hidden ${
          sidebarOpen ? "w-[300px]" : "w-0 border-r-0"
        }`}
      >
        <PipelineRail onSelectBrand={handleSelectBrand} />
      </aside>

      {/* Toggle button */}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="hidden md:flex absolute left-0 top-1/2 -translate-y-1/2 z-10 items-center justify-center w-5 h-10 rounded-r-lg bg-[var(--surface-elevated)] border border-l-0 border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-overlay)] transition-all"
        style={{ left: sidebarOpen ? "300px" : "0px" }}
        title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
      >
        {sidebarOpen ? <PanelLeftClose size={12} /> : <PanelLeftOpen size={12} />}
      </button>

      <div className="flex-1 min-w-0">
        <ChatWorkspace />
      </div>
    </main>
  );
}
