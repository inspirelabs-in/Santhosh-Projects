"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getDashboardKey } from "@/lib/auth";
import { LandingPage } from "@/components/landing/landing-page";

export default function RootPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Signed-in devices skip the marketing page and land in the workspace.
    if (getDashboardKey()) router.replace("/dashboard");
    else setReady(true);
  }, [router]);

  if (!ready) return null;
  return <LandingPage />;
}
