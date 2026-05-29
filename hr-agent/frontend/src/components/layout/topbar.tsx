"use client";

import { LogOut, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { clearDashboardKey } from "@/lib/auth";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { NotificationsBell } from "@/components/notifications-bell";
import { ThemeToggle } from "@/components/theme-toggle";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { AgentLiveIndicator } from "@/components/agent-live-indicator";

export function Topbar({ title, subtitle, breadcrumbOverrides }: { title: string; subtitle?: string; breadcrumbOverrides?: Record<string, string> }) {
  const router = useRouter();
  const { data: who } = useSWR<{ role: string }>("/dashboard/settings/whoami", swrFetcher);

  function logout() {
    clearDashboardKey();
    router.push("/login");
  }

  return (
    <header className="relative z-30 flex h-auto min-h-[3.5rem] shrink-0 items-center justify-between gap-4 border-b bg-background/80 px-6 py-2 backdrop-blur">
      <div className="flex flex-col min-w-0">
        <Breadcrumbs overrides={breadcrumbOverrides} />
        <div className="flex items-baseline gap-3 min-w-0">
          <h1 className="truncate text-lg font-bold leading-tight tracking-tight">{title}</h1>
          {subtitle && (
            <span className="hidden truncate text-sm text-muted-foreground sm:inline">
              {subtitle}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <kbd className="hidden rounded border border-border bg-muted px-1.5 py-0.5 font-data text-[10px] text-muted-foreground md:inline-block">
          ⌘K
        </kbd>
        <AgentLiveIndicator />
        <ThemeToggle />
        <NotificationsBell />
        <div className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 font-mono text-[11px]">
          <User className="h-3 w-3 text-muted-foreground" />
          <span className="capitalize">{who?.role ?? "…"}</span>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>
          <LogOut className="mr-1 h-3.5 w-3.5" /> Sign out
        </Button>
      </div>
    </header>
  );
}
