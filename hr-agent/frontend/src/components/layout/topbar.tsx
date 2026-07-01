"use client";

import { User } from "lucide-react";
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

  const roleInitial = who?.role ? who.role[0].toUpperCase() : "?";

  return (
    <header className="relative z-30 flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border/60 bg-background/95 px-6 backdrop-blur-sm">
      <div className="flex flex-col justify-center min-w-0">
        <Breadcrumbs overrides={breadcrumbOverrides} className="text-xs text-muted-foreground/70" />
        <div className="flex items-baseline gap-3 min-w-0">
          <h1 className="truncate text-xl font-bold leading-tight tracking-tight">{title}</h1>
          {subtitle && (
            <span className="hidden truncate text-sm text-muted-foreground sm:inline">
              {subtitle}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <AgentLiveIndicator />
        <ThemeToggle />
        <NotificationsBell />
        <div
          className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-white text-xs font-bold cursor-pointer select-none"
          title={who?.role ? `Signed in as ${who.role}` : "User"}
          onClick={logout}
        >
          {roleInitial}
        </div>
      </div>
    </header>
  );
}
