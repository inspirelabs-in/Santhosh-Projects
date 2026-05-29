"use client";

import { CheckCircle2, AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ActionMessage = { kind: "success" | "error" | "info"; text: string } | null;

export function ActionToast({
  message,
  onDismiss,
}: {
  message: ActionMessage;
  onDismiss: () => void;
}) {
  if (!message) return null;

  const Icon = message.kind === "success" ? CheckCircle2 : AlertTriangle;
  const tone =
    message.kind === "success"
      ? "border-success/30 bg-success/5 text-success-foreground"
      : message.kind === "error"
      ? "border-destructive/30 bg-destructive/5 text-destructive"
      : "border-primary/30 bg-primary/5 text-primary";

  const iconTone =
    message.kind === "success"
      ? "text-success"
      : message.kind === "error"
      ? "text-destructive"
      : "text-primary";

  return (
    <div
      className={cn(
        "mb-4 flex items-start gap-3 rounded-md border px-4 py-3 text-sm animate-in fade-in slide-in-from-top-1",
        tone,
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", iconTone)} />
      <p className="flex-1 text-foreground">{message.text}</p>
      <button
        type="button"
        onClick={onDismiss}
        className="text-muted-foreground hover:text-foreground"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
