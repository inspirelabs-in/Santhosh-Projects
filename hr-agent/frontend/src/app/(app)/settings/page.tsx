"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowUpRight, Building2 } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { swrFetcher } from "@/lib/api";
import type { Org } from "@/lib/types";

export default function SettingsPage() {
  const { data: org } = useSWR<Org>("/dashboard/settings/org", swrFetcher);

  const initials = org?.name
    ? org.name
        .split(" ")
        .slice(0, 2)
        .map((w) => w[0])
        .join("")
        .toUpperCase()
    : "—";

  return (
    <>
      <Topbar title="Settings" subtitle="organization" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-8 py-12">
          {/* Hero heading */}
          <div className="mb-8">
            <h1 className="font-display text-3xl font-semibold tracking-tight">
              Organization Settings
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Manage your company profile, hiring persona, mission, and culture.
            </p>
          </div>

          {/* Org card */}
          <Link
            href="/settings/org"
            className="group block rounded-xl border border-border bg-card shadow-card transition hover:border-primary/40 hover:shadow-md"
          >
            <div className="flex items-center gap-5 p-6">
              {/* Avatar */}
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-primary text-white font-display text-xl font-semibold select-none">
                {initials}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <p className="font-display text-2xl font-semibold truncate">
                  {org?.name ?? <span className="text-muted-foreground">Loading…</span>}
                </p>
                {org?.slug && (
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground truncate">
                    /{org.slug}
                  </p>
                )}
              </div>

              {/* CTA */}
              <div className="flex shrink-0 items-center gap-2 text-sm font-medium text-primary transition group-hover:gap-3">
                Edit organization profile
                <ArrowUpRight className="h-4 w-4" />
              </div>
            </div>

            <div className="border-t border-border px-6 py-3 flex items-center gap-2">
              <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">
                Hiring persona · Mission · Values · Culture
              </span>
            </div>
          </Link>

          {/* Footer note */}
          <p className="mt-8 text-xs text-muted-foreground leading-relaxed max-w-md">
            System configuration is managed via environment variables. Contact your administrator
            for API keys and integration setup.
          </p>
        </div>
      </div>
    </>
  );
}
