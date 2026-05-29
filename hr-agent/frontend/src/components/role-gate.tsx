"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import useSWR from "swr";
import { Sparkles, ShieldOff } from "lucide-react";

import { swrFetcher } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";

type Role = "admin" | "recruiter" | "viewer";

/**
 * Wrap a route subtree to gate it on dashboard role.
 *
 * Usage:
 *   <RoleGate allow={["admin"]}>{children}</RoleGate>
 *
 * The sidebar already hides admin-only links from non-admins, but this is
 * defence in depth: a recruiter who shares a /ceo URL still hits the wall
 * if they paste it into their browser.
 */
export function RoleGate({
  allow,
  children,
}: {
  allow: Role[];
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { data, error, isLoading } = useSWR<{ role: Role }>(
    "/dashboard/settings/whoami",
    swrFetcher,
  );

  const role = data?.role;
  const allowed = !!role && allow.includes(role);

  useEffect(() => {
    // Optional: redirect after a beat so the user sees why they were blocked.
    if (!isLoading && role && !allow.includes(role)) {
      const t = setTimeout(() => router.replace("/dashboard"), 2500);
      return () => clearTimeout(t);
    }
  }, [isLoading, role, allow, router]);

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <div className="flex items-center gap-3">
          <Sparkles className="h-5 w-5 animate-pulse text-primary" />
          <span className="text-sm">Checking access…</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="p-4 text-sm text-destructive">
            {error.message}
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!allowed) {
    return (
      <div className="flex h-full w-full items-center justify-center p-8">
        <Card className="max-w-md">
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <ShieldOff className="h-8 w-8 text-destructive" />
            <p className="text-base font-bold">Restricted area</p>
            <p className="text-sm text-muted-foreground">
              This page is gated to:{" "}
              <span className="font-mono text-foreground">{allow.join(", ")}</span>.
              Your role is{" "}
              <span className="font-mono text-foreground">{role ?? "unknown"}</span>.
            </p>
            <p className="text-xs text-muted-foreground">
              Redirecting you to the overview…
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return <>{children}</>;
}
