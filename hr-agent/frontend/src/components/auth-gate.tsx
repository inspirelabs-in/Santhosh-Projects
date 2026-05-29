"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getDashboardKey, clearDashboardKey } from "@/lib/auth";
import { verifyKey } from "@/lib/api";
import { Sparkles } from "lucide-react";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const key = getDashboardKey();
    if (!key) {
      router.replace("/login");
      return;
    }
    verifyKey(key).then((res) => {
      if (!res) {
        clearDashboardKey();
        router.replace("/login");
      } else {
        setReady(true);
      }
    });
  }, [router]);

  if (!ready) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-background text-muted-foreground">
        <div className="flex items-center gap-3">
          <Sparkles className="h-5 w-5 animate-pulse text-primary" />
          <span className="text-sm">Signing you in…</span>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
