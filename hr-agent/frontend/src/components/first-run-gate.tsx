"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { configApi, type SetupStatus } from "@/lib/configClient";

const ALLOWED_PATHS = ["/setup", "/login"];

export function FirstRunGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data, error } = useSWR<SetupStatus>(
    "config:setup-status",
    () => configApi.setupStatus(),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  useEffect(() => {
    if (error || !data) return;
    if (data.completed) return;
    if (ALLOWED_PATHS.some((p) => pathname?.startsWith(p))) return;
    router.replace("/setup");
  }, [data, error, pathname, router]);

  return <>{children}</>;
}
