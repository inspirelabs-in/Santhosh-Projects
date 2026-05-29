"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getDashboardKey } from "@/lib/auth";

export default function RootRedirect() {
  const router = useRouter();
  useEffect(() => {
    const key = getDashboardKey();
    router.replace(key ? "/dashboard" : "/login");
  }, [router]);
  return null;
}
