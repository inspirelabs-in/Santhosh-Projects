"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import {
  Activity,
  BarChart3,
  Briefcase,
  ClipboardCheck,
  Moon,
  PhoneCall,
  Search,
  Settings,
  Sun,
  Users,
  Video,
} from "lucide-react";
import { useTheme } from "next-themes";

const PAGES = [
  { label: "Overview", href: "/dashboard", icon: Activity, keywords: "home dashboard" },
  { label: "Candidates", href: "/candidates", icon: Users, keywords: "people applicants" },
  { label: "Roles", href: "/roles", icon: Briefcase, keywords: "jobs positions openings" },
  { label: "Voice screens", href: "/voice-screens", icon: PhoneCall, keywords: "phone call" },
  { label: "Assessments", href: "/assessments", icon: ClipboardCheck, keywords: "tests assignments" },
  { label: "Meetings", href: "/meetings", icon: Video, keywords: "interviews calls" },
  { label: "Analytics", href: "/analytics", icon: BarChart3, keywords: "reports charts stats" },
  { label: "Settings", href: "/settings", icon: Settings, keywords: "config preferences" },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { theme, setTheme } = useTheme();

  const toggle = useCallback(() => setOpen((o) => !o), []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        toggle();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggle]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]" onClick={() => setOpen(false)}>
      <div className="fixed inset-0 bg-foreground/20 backdrop-blur-sm" />
      <div className="relative w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <Command className="rounded-xl border border-border bg-popover shadow-pop overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <Command.Input
              placeholder="Search pages, actions..."
              className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              autoFocus
            />
            <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-data text-[10px] text-muted-foreground">
              ESC
            </kbd>
          </div>
          <Command.List className="max-h-72 overflow-y-auto p-2">
            <Command.Empty className="px-4 py-6 text-center text-sm text-muted-foreground">
              No results.
            </Command.Empty>
            <Command.Group heading="Pages" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.15em] [&_[cmdk-group-heading]]:text-muted-foreground">
              {PAGES.map((p) => (
                <Command.Item
                  key={p.href}
                  keywords={[p.keywords]}
                  onSelect={() => { router.push(p.href); setOpen(false); }}
                  className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm transition data-[selected=true]:bg-accent/40"
                >
                  <p.icon className="h-4 w-4 text-muted-foreground" />
                  {p.label}
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Actions" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.15em] [&_[cmdk-group-heading]]:text-muted-foreground">
              <Command.Item
                keywords={["dark light mode appearance"]}
                onSelect={() => { setTheme(theme === "dark" ? "light" : "dark"); setOpen(false); }}
                className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm transition data-[selected=true]:bg-accent/40"
              >
                {theme === "dark" ? <Sun className="h-4 w-4 text-muted-foreground" /> : <Moon className="h-4 w-4 text-muted-foreground" />}
                Toggle theme
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}
