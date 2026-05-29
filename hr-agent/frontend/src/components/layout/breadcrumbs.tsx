"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  dashboard: "Overview",
  candidates: "Candidates",
  roles: "Roles",
  settings: "Settings",
  analytics: "Analytics",
  assessments: "Assessments",
  meetings: "Meetings",
  "voice-screens": "Voice screens",
  compare: "Compare",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function Breadcrumbs({ className, overrides }: { className?: string; overrides?: Record<string, string> }) {
  const pathname = usePathname();
  if (!pathname || pathname === "/dashboard") return null;

  const segs = pathname.split("/").filter(Boolean);
  const crumbs: { label: string; href: string }[] = [];
  let path = "";
  for (const s of segs) {
    path += `/${s}`;
    const override = overrides?.[s];
    const label = override ?? LABELS[s] ?? (UUID_RE.test(s) ? undefined : decodeURIComponent(s));
    if (!label) continue;
    crumbs.push({ label, href: path });
  }

  if (crumbs.length <= 1) return null;

  return (
    <nav aria-label="Breadcrumb" className={cn("flex items-center gap-1 text-xs text-muted-foreground", className)}>
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={c.href} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3 w-3" />}
            {isLast ? (
              <span className="font-medium text-foreground">{c.label}</span>
            ) : (
              <Link href={c.href} className="hover:text-foreground transition-colors">
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}
