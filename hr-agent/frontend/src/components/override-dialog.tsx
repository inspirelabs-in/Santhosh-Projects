"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import type { OverrideDecision } from "@/lib/types";

export function OverrideDialog({
  open,
  onOpenChange,
  applicationId,
  candidateName,
  defaultDecision = "approve",
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  applicationId: string;
  candidateName?: string | null;
  defaultDecision?: OverrideDecision;
  onDone?: () => void;
}) {
  const [decision, setDecision] = useState<OverrideDecision>(defaultDecision);
  const [reason, setReason] = useState("");
  const [hrEmail, setHrEmail] = useState(
    typeof window !== "undefined" ? localStorage.getItem("hr_email") ?? "" : "",
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDecision(defaultDecision);
      setErr(null);
    }
  }, [open, defaultDecision]);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.post(`/dashboard/override/${applicationId}`, {
        decision,
        reason,
        hr_email: hrEmail,
      });
      if (typeof window !== "undefined") {
        localStorage.setItem("hr_email", hrEmail);
      }
      onOpenChange(false);
      setReason("");
      onDone?.();
    } catch (e: any) {
      setErr(e?.message ?? "Override failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Override decision</DialogTitle>
          <DialogDescription>
            {candidateName ? (
              <>For <span className="font-medium">{candidateName}</span>. </>
            ) : null}
            Your action is logged to the audit trail and will signal the running workflow.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Decision</Label>
            <Select value={decision} onValueChange={(v) => setDecision(v as OverrideDecision)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="approve">Approve (move to shortlist)</SelectItem>
                <SelectItem value="force_shortlist">Force shortlist (bypass score)</SelectItem>
                <SelectItem value="reject">Reject</SelectItem>
                <SelectItem value="change_role">Change role (requires role ID)</SelectItem>
                <SelectItem value="withdraw">Withdraw application</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="reason">Reason <span className="text-muted-foreground">(required, logged)</span></Label>
            <Textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you making this change?"
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="hr_email">Your email</Label>
            <Input
              id="hr_email"
              type="email"
              value={hrEmail}
              onChange={(e) => setHrEmail(e.target.value)}
              placeholder="you@grabon.in"
            />
          </div>

          {err ? <p className="text-sm text-destructive">{err}</p> : null}
          {!reason.trim() || !hrEmail.trim() ? (
            <p className="text-xs text-muted-foreground">
              Reason + your email are required before Confirm becomes active (both are logged to audit).
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !reason.trim() || !hrEmail.trim()}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Confirm {decision.replace(/_/g, " ")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
