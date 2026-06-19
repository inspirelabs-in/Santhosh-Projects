"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import {
  CalendarClock,
  CheckCircle2,
  Clock,
  Loader2,
  AlertTriangle,
  CalendarCheck,
} from "lucide-react";
import { QuickDateTime } from "@/components/quick-datetime";

function fmtLocal(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// Calls go through the same-origin Next route handler (see
// app/api/meeting-reschedule/[token]/route.ts), which proxies to the backend
// server-side. This avoids mixed-content / unreachable-localhost failures when
// the candidate opens the link over a public HTTPS origin (ngrok, etc.).
interface RescheduleContext {
  candidate_name: string | null;
  role_title: string | null;
  round: string;
  round_label: string;
  current_scheduled_at: string | null;
  current_scheduled_label: string | null;
  suggested_slots: Array<{ scheduled_at: string; label: string }>;
}

async function fetcher(path: string): Promise<RescheduleContext> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export default function RescheduleRequestPage() {
  const { token } = useParams<{ token: string }>();
  const { data: ctx, error, isLoading } = useSWR(
    token ? `/api/meeting-reschedule/${token}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">Loading…</p>
        </div>
      </div>
    );
  }

  if (error || !ctx) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="mx-auto max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
          <AlertTriangle className="mx-auto h-10 w-10 text-amber-500" />
          <h2 className="mt-4 text-lg font-bold">Link invalid or expired</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {error?.message || "This link is no longer valid. Please contact the hiring team."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-2xl px-4 py-12">
        <div className="rounded-xl border bg-card p-8 shadow-sm">
          <div className="mb-6 flex items-center gap-3">
            <CalendarClock className="h-6 w-6 text-primary" />
            <div>
              <h1 className="text-xl font-bold">Request a different time</h1>
              <p className="text-sm text-muted-foreground">
                {ctx.round_label} interview
                {ctx.role_title && (
                  <>
                    {" "}for <strong>{ctx.role_title}</strong>
                  </>
                )}
              </p>
            </div>
          </div>

          {ctx.current_scheduled_label && (
            <div className="mb-6 flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
              <Clock className="h-4 w-4 text-muted-foreground" />
              <span>
                Currently scheduled for <strong>{ctx.current_scheduled_label}</strong>
              </span>
            </div>
          )}

          <RescheduleForm ctx={ctx} token={token} />
        </div>
      </div>
    </div>
  );
}

function RescheduleForm({ ctx, token }: { ctx: RescheduleContext; token: string }) {
  const [picked, setPicked] = useState<string | null>(null); // ISO from a suggested slot
  const [custom, setCustom] = useState(""); // datetime-local value
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      // `custom` is already an ISO string from QuickDateTime; `picked` is a
      // suggested slot's ISO. Custom takes precedence when set.
      const requested_at: string | null = custom || picked || null;

      const res = await fetch(`/api/meeting-reschedule/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requested_at, reason: reason.trim() || null }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `Error ${res.status}`);
      }
      setDone(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (done) {
    return (
      <div className="flex flex-col items-center gap-4 py-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/30">
          <CalendarCheck className="h-8 w-8 text-emerald-600" />
        </div>
        <h2 className="text-xl font-bold">Request sent</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Thanks! The hiring team will confirm a new time and email you an updated invite shortly.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Pick one of the suggested times below, or propose your own. You can add a short note for the
        hiring team.
      </p>

      {ctx.suggested_slots.length > 0 && (
        <div className="space-y-3">
          {ctx.suggested_slots.map((slot) => {
            const isSelected = picked === slot.scheduled_at && !custom;
            return (
              <button
                key={slot.scheduled_at}
                type="button"
                onClick={() => {
                  setPicked(slot.scheduled_at);
                  setCustom("");
                }}
                className={`flex w-full items-center gap-4 rounded-lg border px-5 py-3 text-left transition ${
                  isSelected
                    ? "border-primary bg-primary/5 ring-2 ring-primary/30"
                    : "border-border hover:border-primary/40 hover:bg-accent/50"
                }`}
              >
                <div
                  className={`flex h-7 w-7 items-center justify-center rounded-full border-2 transition ${
                    isSelected ? "border-primary bg-primary text-white" : "border-muted-foreground/30"
                  }`}
                >
                  {isSelected && <CheckCircle2 className="h-4 w-4" />}
                </div>
                <span className="font-medium">{slot.label}</span>
              </button>
            );
          })}
        </div>
      )}

      <div>
        <label className="mb-1.5 block text-sm font-medium">Or propose your own time</label>
        <QuickDateTime
          onChange={(iso) => {
            setCustom(iso || "");
            if (iso) setPicked(null);
          }}
        />
        {custom && (
          <p className="mt-2 text-xs font-medium text-primary">
            Proposed: {fmtLocal(custom)}
          </p>
        )}
      </div>

      <div>
        <label className="mb-1.5 block text-sm font-medium">
          Note <span className="font-normal text-muted-foreground">(optional)</span>
        </label>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="e.g. I have a conflict that morning — afternoons work better."
          className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
        />
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      <div className="flex justify-end pt-2">
        <button
          type="button"
          onClick={submit}
          disabled={submitting}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
          Send request
        </button>
      </div>
    </div>
  );
}
