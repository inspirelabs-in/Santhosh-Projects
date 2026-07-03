"use client";

import { useState } from "react";
import { CalendarClock, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { api } from "@/lib/api";

/**
 * Manual scheduling override. Used when:
 *   - Auto-scheduler couldn't find a slot (panel emailed for options, HR
 *     coordinated reply, picks slot here).
 *   - Recruiter wants to override the auto-pick before it fires.
 *
 * POSTs /agentic/meeting/manual-schedule which mints the Teams link, persists
 * the meeting_session row, and advances stage.
 */
export function ManualScheduleModal({
  applicationId,
  defaultRound = "technical",
  onClose,
  onSaved,
}: {
  applicationId: string;
  defaultRound?: "technical" | "ceo" | "hr";
  onClose: () => void;
  onSaved: () => void;
}) {
  const [round, setRound] = useState<"technical" | "ceo" | "hr">(
    (defaultRound as any) ?? "technical",
  );
  const [whenLocal, setWhenLocal] = useState("");
  const [duration, setDuration] = useState(45);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (!whenLocal) throw new Error("pick a date + time");
      // datetime-local is local timezone; convert to ISO with offset.
      const local = new Date(whenLocal);
      const iso = local.toISOString();
      const out = await api.post<{
        ok: boolean;
        meeting_session_id: string;
        join_url: string;
        scheduled_at: string;
      }>("/agentic/meeting/manual-schedule", {
        application_id: applicationId,
        round,
        scheduled_at: iso,
        duration_minutes: duration,
        panel_emails: [],
      });
      if (!out.ok) throw new Error("backend rejected request");
      onSaved();
    } catch (e: any) {
      setError(e?.detail?.detail ?? e?.message ?? "Schedule failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider">
            <CalendarClock className="h-4 w-4 text-primary" />
            Schedule meeting manually
          </h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          Use when auto-scheduling failed or you've coordinated a slot
          out-of-band. Mints the Teams link + sends invites immediately.
        </p>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Round</Label>
              <Select
                value={round}
                onValueChange={(nv) => setRound(nv as any)}
              >
                <SelectTrigger className="mt-1 w-full text-sm">
                  <SelectValue placeholder="Select round" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="technical">Technical</SelectItem>
                  <SelectItem value="ceo">CEO</SelectItem>
                  <SelectItem value="hr">HR</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Duration (min)</Label>
              <Input
                type="number"
                min={15}
                max={240}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                className="mt-1"
              />
            </div>
          </div>
          <div>
            <Label>Date + time (your local timezone)</Label>
            <Input
              type="datetime-local"
              value={whenLocal}
              onChange={(e) => setWhenLocal(e.target.value)}
              className="mt-1"
            />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}

          <div className="flex justify-end gap-2 pt-2">
            <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={busy}>
              {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
              Book + send invites
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
