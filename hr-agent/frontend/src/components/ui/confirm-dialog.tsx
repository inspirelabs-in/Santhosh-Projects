"use client";

import * as React from "react";
import { AlertTriangle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export type ConfirmTone = "danger" | "default";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v && !busy) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-start gap-3">
            {tone === "danger" && (
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
                <AlertTriangle className="h-4 w-4" />
              </div>
            )}
            <div className="flex-1">
              <DialogTitle>{title}</DialogTitle>
              {description ? (
                <DialogDescription className="mt-1.5">{description}</DialogDescription>
              ) : null}
            </div>
          </div>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={tone === "danger" ? "destructive" : "default"}
            disabled={busy}
            onClick={() => void onConfirm()}
          >
            {busy ? "Working..." : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Imperative hook for places that want a quick prompt without local state plumbing. */
export interface ConfirmRequest {
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
}

export function useConfirm() {
  const [req, setReq] = React.useState<ConfirmRequest | null>(null);
  const [busy, setBusy] = React.useState(false);
  const resolverRef = React.useRef<((v: boolean) => void) | null>(null);

  const confirm = React.useCallback((r: ConfirmRequest) => {
    setReq(r);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, []);

  function settle(v: boolean) {
    resolverRef.current?.(v);
    resolverRef.current = null;
    setReq(null);
    setBusy(false);
  }

  const node = req ? (
    <ConfirmDialog
      open={true}
      title={req.title}
      description={req.description}
      confirmLabel={req.confirmLabel}
      cancelLabel={req.cancelLabel}
      tone={req.tone}
      busy={busy}
      onCancel={() => settle(false)}
      onConfirm={() => settle(true)}
    />
  ) : null;

  return { confirm, dialog: node, setBusy } as const;
}
