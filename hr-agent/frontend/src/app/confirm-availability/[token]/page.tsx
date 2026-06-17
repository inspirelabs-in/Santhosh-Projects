"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Calendar,
  CheckCircle2,
  Clock,
  Loader2,
  Send,
  AlertTriangle,
  CalendarCheck,
  ChevronLeft,
  ChevronRight,
  Video,
  X,
  Plus,
} from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

interface AvailabilityContext {
  meeting_session_id: string;
  panel_email: string;
  round: string;
  candidate_name: string | null;
  role_title: string | null;
  already_responded: boolean;
  status: string;
  duration_minutes: number;
  panel_timezone: string;
  required_slots: number;
}

interface SelectedSlot {
  date: string;
  hour: number;
  minute: number;
}

async function fetcher(path: string): Promise<AvailabilityContext> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function formatTzLabel(tz: string): string {
  const map: Record<string, string> = {
    "Asia/Kolkata": "IST",
    "America/New_York": "ET",
    "America/Los_Angeles": "PT",
    "Europe/London": "GMT",
    "Europe/Berlin": "CET",
    "Asia/Singapore": "SGT",
    "Asia/Dubai": "GST",
    UTC: "UTC",
  };
  return map[tz] || tz;
}

function slotToLabel(slot: SelectedSlot, duration: number): string {
  const [y, mo, d] = slot.date.split("-").map(Number);
  const dt = new Date(y, mo - 1, d, slot.hour, slot.minute);
  const endDt = new Date(dt.getTime() + duration * 60_000);
  const fmt = (dd: Date) =>
    dd.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}, ${fmt(dt)} – ${fmt(endDt)}`;
}

function slotKey(s: SelectedSlot) {
  return `${s.date}|${s.hour}|${s.minute}`;
}

export default function ConfirmAvailabilityPage() {
  const { token } = useParams<{ token: string }>();
  const {
    data: ctx,
    error,
    isLoading,
  } = useSWR(
    token ? `/panel-availability/${token}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">Loading...</p>
        </div>
      </div>
    );
  }

  if (error || !ctx) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="mx-auto max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
          <AlertTriangle className="mx-auto h-10 w-10 text-amber-500" />
          <h2 className="mt-4 text-lg font-bold">Link Invalid or Expired</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {error?.message ||
              "This confirmation link is no longer valid. Please contact the hiring team."}
          </p>
        </div>
      </div>
    );
  }

  if (ctx.already_responded || ctx.status === "awaiting_candidate" || ctx.status === "confirmed" || ctx.status === "booked" || ctx.status === "booking") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="mx-auto max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
          <CalendarCheck className="mx-auto h-10 w-10 text-emerald-500" />
          <h2 className="mt-4 text-lg font-bold">
            {ctx.status === "awaiting_candidate"
              ? "Slots Submitted"
              : "Meeting Already Scheduled"}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {ctx.status === "awaiting_candidate"
              ? "Your time slots have been sent to the candidate. You'll receive a calendar invite once they confirm."
              : "This interview has already been booked. Check your calendar for the invite."}
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
            <Calendar className="h-6 w-6 text-primary" />
            <div>
              <h1 className="text-xl font-bold">Schedule Interview</h1>
              <p className="text-sm text-muted-foreground">
                {ctx.round} interview
                {ctx.candidate_name && (
                  <> with <strong>{ctx.candidate_name}</strong></>
                )}
                {ctx.role_title && (
                  <> for <strong>{ctx.role_title}</strong></>
                )}
              </p>
            </div>
          </div>

          <p className="mb-6 text-sm text-muted-foreground">
            Pick <strong>{ctx.required_slots} time slots</strong> within the
            next 15 days. The candidate will choose from your options.
          </p>

          <MultiSlotPicker ctx={ctx} token={token} />
        </div>
      </div>
    </div>
  );
}

function MultiSlotPicker({
  ctx,
  token,
}: {
  ctx: AvailabilityContext;
  token: string;
}) {
  const tz = ctx.panel_timezone || "Asia/Kolkata";
  const tzLabel = formatTzLabel(tz);
  const duration = ctx.duration_minutes || 45;
  const required = ctx.required_slots || 3;

  const [today] = useState(() => new Date());
  const [currentMonth, setCurrentMonth] = useState(() => new Date().getMonth());
  const [currentYear, setCurrentYear] = useState(() => new Date().getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedHour, setSelectedHour] = useState<number | null>(null);
  const [selectedMinute, setSelectedMinute] = useState<number>(0);

  const [confirmedSlots, setConfirmedSlots] = useState<SelectedSlot[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const [minDate] = useState(() => {
    const d = new Date(today);
    d.setHours(0, 0, 0, 0);
    return d;
  });
  const [maxDate] = useState(() => {
    const d = new Date(today);
    d.setDate(d.getDate() + 15);
    d.setHours(23, 59, 59, 999);
    return d;
  });

  const calendarDays = useMemo(() => {
    const firstDay = new Date(currentYear, currentMonth, 1);
    const lastDay = new Date(currentYear, currentMonth + 1, 0);
    const startPad = firstDay.getDay();

    const days: Array<{ date: Date; inMonth: boolean; enabled: boolean }> = [];

    for (let i = startPad - 1; i >= 0; i--) {
      const d = new Date(currentYear, currentMonth, -i);
      days.push({ date: d, inMonth: false, enabled: false });
    }

    for (let day = 1; day <= lastDay.getDate(); day++) {
      const d = new Date(currentYear, currentMonth, day);
      const isWeekday = d.getDay() !== 0 && d.getDay() !== 6;
      const inRange = d >= minDate && d <= maxDate;
      days.push({ date: d, inMonth: true, enabled: isWeekday && inRange });
    }

    const remaining = 7 - (days.length % 7);
    if (remaining < 7) {
      for (let i = 1; i <= remaining; i++) {
        const d = new Date(currentYear, currentMonth + 1, i);
        days.push({ date: d, inMonth: false, enabled: false });
      }
    }

    return days;
  }, [currentMonth, currentYear, minDate, maxDate]);

  const timeSlots = useMemo(() => {
    const slots: Array<{ hour: number; minute: number; label: string }> = [];
    for (let h = 9; h <= 20; h++) {
      for (const m of [0, 30]) {
        if (h === 20 && m > 0) continue;
        const ampm = h >= 12 ? "PM" : "AM";
        const displayH = h > 12 ? h - 12 : h === 0 ? 12 : h;
        const displayM = m.toString().padStart(2, "0");
        slots.push({
          hour: h,
          minute: m,
          label: `${displayH}:${displayM} ${ampm}`,
        });
      }
    }
    return slots;
  }, []);

  const prevMonth = () => {
    if (currentMonth === 0) {
      setCurrentMonth(11);
      setCurrentYear(currentYear - 1);
    } else {
      setCurrentMonth(currentMonth - 1);
    }
  };

  const nextMonth = () => {
    if (currentMonth === 11) {
      setCurrentMonth(0);
      setCurrentYear(currentYear + 1);
    } else {
      setCurrentMonth(currentMonth + 1);
    }
  };

  const formatDateKey = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  const monthNames = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  const canGoPrev =
    currentYear > today.getFullYear() ||
    (currentYear === today.getFullYear() && currentMonth > today.getMonth());

  const maxMonth = maxDate.getMonth();
  const maxYear = maxDate.getFullYear();
  const canGoNext =
    currentYear < maxYear ||
    (currentYear === maxYear && currentMonth < maxMonth);

  const addSlot = () => {
    if (!selectedDate || selectedHour === null) return;
    const newSlot: SelectedSlot = {
      date: selectedDate,
      hour: selectedHour,
      minute: selectedMinute,
    };
    if (confirmedSlots.some((s) => slotKey(s) === slotKey(newSlot))) {
      setError("This time slot is already selected.");
      return;
    }
    if (confirmedSlots.length >= required) {
      setError(`You can only pick ${required} slots.`);
      return;
    }
    setConfirmedSlots([...confirmedSlots, newSlot]);
    setSelectedHour(null);
    setSelectedMinute(0);
    setError(null);
  };

  const removeSlot = (idx: number) => {
    setConfirmedSlots(confirmedSlots.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (confirmedSlots.length !== required) {
      setError(`Please select exactly ${required} time slots.`);
      return;
    }

    setError(null);
    setSubmitting(true);

    try {
      const slots = confirmedSlots.map((s) => {
        const [year, month, day] = s.date.split("-").map(Number);
        const start = new Date(year, month - 1, day, s.hour, s.minute);
        const end = new Date(start.getTime() + duration * 60_000);
        return {
          start: start.toISOString(),
          end: end.toISOString(),
        };
      });

      const res = await fetch(
        `${BASE}/panel-availability/${token}/slots`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slots }),
        },
      );

      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `Error ${res.status}`);
      }

      const data = await res.json();
      setResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (result?.status === "slots_saved_candidate_notified" || result?.status === "slots_saved") {
    return (
      <div className="flex flex-col items-center gap-4 py-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/30">
          <CheckCircle2 className="h-8 w-8 text-emerald-600" />
        </div>
        <h2 className="text-xl font-bold">Time Slots Submitted!</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Your {required} time slots have been sent to the candidate.
          You&apos;ll receive a Google Meet calendar invite once they choose a time.
        </p>
      </div>
    );
  }

  if (result?.status === "already_resolved") {
    return (
      <div className="flex flex-col items-center gap-4 py-8 text-center">
        <CalendarCheck className="h-12 w-12 text-blue-500" />
        <h2 className="text-xl font-bold">Already Scheduled</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          This interview has already been scheduled.
        </p>
      </div>
    );
  }

  const currentSlotLabel =
    selectedDate && selectedHour !== null
      ? slotToLabel(
          { date: selectedDate, hour: selectedHour, minute: selectedMinute },
          duration,
        )
      : null;

  return (
    <div className="space-y-6">
      {/* Confirmed slots */}
      {confirmedSlots.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold">
            Selected Slots ({confirmedSlots.length}/{required})
          </h3>
          <div className="space-y-2">
            {confirmedSlots.map((slot, i) => (
              <div
                key={slotKey(slot)}
                className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 dark:border-emerald-800 dark:bg-emerald-950/30"
              >
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-600 text-xs font-bold text-white">
                    {i + 1}
                  </span>
                  <span className="text-sm font-medium">
                    {slotToLabel(slot, duration)} ({tzLabel})
                  </span>
                </div>
                <button
                  onClick={() => removeSlot(i)}
                  className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Calendar */}
      {confirmedSlots.length < required && (
        <>
          <div>
            <div className="mb-3 flex items-center justify-between">
              <button
                onClick={prevMonth}
                disabled={!canGoPrev}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <h3 className="text-sm font-semibold">
                {monthNames[currentMonth]} {currentYear}
              </h3>
              <button
                onClick={nextMonth}
                disabled={!canGoNext}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-7 gap-1">
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
                <div
                  key={d}
                  className="py-1 text-center text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
                >
                  {d}
                </div>
              ))}
              {calendarDays.map(({ date, inMonth, enabled }, i) => {
                const key = formatDateKey(date);
                const isSelected = selectedDate === key;
                const isToday = formatDateKey(today) === key;
                return (
                  <button
                    key={i}
                    disabled={!enabled}
                    onClick={() => {
                      setSelectedDate(key);
                      setSelectedHour(null);
                      setSelectedMinute(0);
                    }}
                    className={`relative rounded-lg py-2 text-sm transition ${
                      !inMonth
                        ? "text-muted-foreground/30"
                        : !enabled
                          ? "text-muted-foreground/40 cursor-not-allowed"
                          : isSelected
                            ? "bg-primary text-primary-foreground font-semibold ring-2 ring-primary/30"
                            : "hover:bg-accent/60 text-foreground"
                    }`}
                  >
                    {date.getDate()}
                    {isToday && !isSelected && (
                      <span className="absolute bottom-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-primary" />
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Time picker */}
          {selectedDate && (
            <div>
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Clock className="h-4 w-4 text-muted-foreground" />
                Select Time ({tzLabel})
              </h3>
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
                {timeSlots.map((slot) => {
                  const isSelected =
                    selectedHour === slot.hour && selectedMinute === slot.minute;
                  const alreadyPicked = confirmedSlots.some(
                    (s) =>
                      s.date === selectedDate &&
                      s.hour === slot.hour &&
                      s.minute === slot.minute,
                  );
                  return (
                    <button
                      key={`${slot.hour}:${slot.minute}`}
                      disabled={alreadyPicked}
                      onClick={() => {
                        setSelectedHour(slot.hour);
                        setSelectedMinute(slot.minute);
                      }}
                      className={`rounded-lg border px-3 py-2 text-sm transition ${
                        alreadyPicked
                          ? "border-emerald-300 bg-emerald-50 text-emerald-600 cursor-not-allowed dark:border-emerald-800 dark:bg-emerald-950/30"
                          : isSelected
                            ? "border-primary bg-primary/10 font-semibold text-primary ring-1 ring-primary/30"
                            : "border-border hover:border-primary/40 hover:bg-accent/50"
                      }`}
                    >
                      {slot.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Current selection + add button */}
          {currentSlotLabel && (
            <div className="flex items-center gap-3 rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
              <Video className="h-5 w-5 shrink-0 text-primary" />
              <div className="flex-1 text-sm">
                <p className="font-medium">{currentSlotLabel} ({tzLabel})</p>
                <p className="text-xs text-muted-foreground">
                  Slot {confirmedSlots.length + 1} of {required}
                </p>
              </div>
              <button
                onClick={addSlot}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                <Plus className="h-4 w-4" />
                Add Slot
              </button>
            </div>
          )}
        </>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <p className="text-xs text-muted-foreground">
          {confirmedSlots.length === required
            ? "Ready to submit"
            : confirmedSlots.length > 0
              ? `${confirmedSlots.length}/${required} slots selected`
              : selectedDate
                ? selectedHour !== null
                  ? "Click 'Add Slot' to confirm this time"
                  : "Pick a time slot"
                : "Pick a date"}
        </p>
        <button
          onClick={handleSubmit}
          disabled={submitting || confirmedSlots.length !== required}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
          Submit {required} Slots
        </button>
      </div>
    </div>
  );
}
