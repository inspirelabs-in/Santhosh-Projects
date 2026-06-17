"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  Users,
  Briefcase,
  ScrollText,
  Settings,
  ChevronLeft,
  ChevronRight,
  Trash2,
  PhoneCall,
  ClipboardCheck,
  Video,
  Crown,
  HeartHandshake,
  Brain,
} from "lucide-react";
import useSWR from "swr";
import { cn } from "@/lib/utils";
import { api, swrFetcher } from "@/lib/api";
import { Logo } from "@/components/brand/logo";

type NavItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  roles?: Array<"admin" | "recruiter" | "viewer">;
};
type NavGroup = { section: string; items: NavItem[] };

const nav: NavGroup[] = [
  { section: "workspace", items: [
    { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
    { href: "/candidates", label: "Candidates", icon: Users },
    { href: "/roles", label: "Roles", icon: Briefcase },
  ]},
  { section: "agentic", items: [
    { href: "/voice-screens", label: "Voice calls", icon: PhoneCall },
    { href: "/assessments", label: "Assessments", icon: ClipboardCheck },
    { href: "/meetings", label: "Meetings", icon: Video },
  ]},
  { section: "ceo", items: [
    { href: "/ceo", label: "CEO journey", icon: Crown, roles: ["admin"] },
  ]},
  { section: "hr", items: [
    { href: "/hr", label: "HR journey", icon: HeartHandshake },
  ]},
  { section: "intelligence", items: [
    { href: "/supervisor", label: "Supervisor", icon: Brain, roles: ["admin"] },
  ]},
  { section: "admin", items: [
    { href: "/audit", label: "Audit log", icon: ScrollText },
    { href: "/settings/panels", label: "Panels", icon: Users },
    { href: "/settings", label: "Settings", icon: Settings },
  ]},
];

const STORAGE_KEY = "hiring-agent:sidebar-collapsed";

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const { data: who } = useSWR<{ role: string }>(
    "/dashboard/settings/whoami",
    swrFetcher,
  );
  const role = (who?.role ?? "viewer") as "admin" | "recruiter" | "viewer";

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "1") setCollapsed(true);
  }, []);

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  };

  return (
    <aside
      className={cn(
        "relative flex h-screen shrink-0 flex-col border-r bg-card transition-[width] duration-200 ease-in-out",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div
        className={cn(
          "flex h-16 items-center border-b",
          collapsed ? "justify-center px-2" : "px-5",
        )}
      >
        {!collapsed ? (
          <Link href="/dashboard" className="flex items-center gap-3" aria-label="GrabOn Hiring home">
            <Logo variant="light" kind="wordmark" width={120} height={32} priority />
            <span className="rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-primary">
              hiring
            </span>
          </Link>
        ) : (
          <Link href="/dashboard" aria-label="GrabOn Hiring home">
            <Logo variant="light" kind="icon" width={32} height={32} priority />
          </Link>
        )}
      </div>

      <button
        type="button"
        onClick={toggle}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="absolute -right-3 top-5 z-10 flex h-6 w-6 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm hover:text-foreground"
      >
        {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
      </button>

      <nav className="flex-1 overflow-y-auto p-2">
        {nav.map((group) => {
          const visibleItems = group.items.filter(
            (i) => !i.roles || i.roles.includes(role),
          );
          if (visibleItems.length === 0) return null;
          return (
          <div key={group.section} className="mb-6">
            {!collapsed && (
              <div className="px-3 pb-1.5 pt-2 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                {group.section}
              </div>
            )}
            <div className="space-y-0.5">
              {visibleItems.map((item) => {
                const active =
                  pathname === item.href ||
                  (item.href !== "/dashboard" && pathname.startsWith(item.href));
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "group flex items-center rounded-md text-sm font-medium transition",
                      collapsed ? "justify-center px-2 py-2" : "gap-3 px-3 py-2",
                      active
                        ? "bg-primary/10 text-primary border-l-2 border-primary -ml-px"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {!collapsed && <span>{item.label}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
          );
        })}
      </nav>

      {!collapsed && (
        <div className="space-y-3 border-t p-4">
          <button
            type="button"
            onClick={async () => {
              if (!confirm("Wipe all candidates, applications, roles, and audit entries? Dev only.")) return;
              try {
                await api.post("/dashboard/v1/dev/reset");
                window.location.reload();
              } catch (e: any) {
                alert(`Reset failed: ${e?.message ?? "unknown"}`);
              }
            }}
            className="flex w-full items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 font-mono text-[11px] text-destructive transition hover:bg-destructive/10"
            title="Dev-only: wipe all data"
          >
            <Trash2 className="h-3.5 w-3.5" />
            wipe all data
          </button>
          <div className="font-mono text-[10px] text-muted-foreground">
            <p>Inspirelabs Solutions</p>
            <p className="mt-0.5">agentic hiring · v2</p>
          </div>
        </div>
      )}
    </aside>
  );
}
