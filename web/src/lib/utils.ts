import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatINR(value: number | string | null | undefined): string {
  if (value == null) return "—";
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "—";
  if (num >= 10_000_000) return `₹${(num / 10_000_000).toFixed(1)}Cr`;
  if (num >= 100_000) return `₹${(num / 100_000).toFixed(1)}L`;
  if (num >= 1_000) return `₹${(num / 1_000).toFixed(1)}K`;
  return `₹${num}`;
}

export function tierColor(tier: string | null | undefined) {
  switch (tier) {
    case "hot": return { bg: "bg-rose-500/15", text: "text-rose-400", border: "border-l-rose-500" };
    case "warm": return { bg: "bg-amber-500/15", text: "text-amber-400", border: "border-l-amber-500" };
    case "watchlist": return { bg: "bg-blue-500/15", text: "text-blue-400", border: "border-l-blue-500" };
    case "park": return { bg: "bg-neutral-500/15", text: "text-neutral-400", border: "border-l-neutral-500" };
    default: return { bg: "bg-neutral-500/10", text: "text-neutral-500", border: "border-l-neutral-600" };
  }
}
