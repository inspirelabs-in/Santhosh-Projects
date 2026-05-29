"use client";

import { cn, initials } from "@/lib/utils";

const COLORS = [
  "bg-primary/15 text-primary",
  "bg-secondary/15 text-secondary",
  "bg-warning/15 text-warning",
  "bg-info/15 text-info",
  "bg-accent/20 text-accent-foreground",
  "bg-destructive/10 text-destructive",
];

function colorFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return COLORS[Math.abs(h) % COLORS.length];
}

interface AvatarProps {
  name: string | null | undefined;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE = { sm: "h-7 w-7 text-[10px]", md: "h-9 w-9 text-xs", lg: "h-12 w-12 text-sm" };

export function Avatar({ name, size = "md", className }: AvatarProps) {
  const display = name ?? "?";
  return (
    <div
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-bold uppercase",
        SIZE[size],
        colorFor(display),
        className,
      )}
      title={display}
    >
      {initials(display)}
    </div>
  );
}
