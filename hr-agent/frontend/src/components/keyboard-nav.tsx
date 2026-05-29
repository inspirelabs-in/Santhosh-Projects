"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

const GO_MAP: Record<string, string> = {
  d: "/dashboard",
  c: "/candidates",
  r: "/roles",
  a: "/analytics",
  s: "/settings",
};

export function KeyboardNav() {
  const router = useRouter();
  const pending = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (pending.current === "g") {
        const dest = GO_MAP[e.key.toLowerCase()];
        if (dest) {
          e.preventDefault();
          router.push(dest);
        }
        pending.current = null;
        clearTimeout(timer.current);
        return;
      }

      if (e.key.toLowerCase() === "g") {
        pending.current = "g";
        timer.current = setTimeout(() => { pending.current = null; }, 800);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      clearTimeout(timer.current);
    };
  }, [router]);

  return null;
}
