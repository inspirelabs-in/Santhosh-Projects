"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Calendar,
  CalendarCheck,
  ChevronLeft,
  ChevronRight,
  Clock,
  Loader2,
  AlertTriangle,
  Video,
  CheckCircle2,
  CalendarPlus,
} from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

interface SlotContext {
  meeting_session_id: string;
  round: string;
  candidate_name: string | null;
  role_title: string | null;
  slots: Array<{
    index: number;
    start_utc: string;
    end_utc: string;
    start_ist: string;
    end_ist: string;
    label: string;
  }>;
  status: string;
  duration_minutes: number;
}

async function fetcher(path: string): Promise<SlotContext> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export default function SelectSlotPage() {
  const { token } = useParams<{ token: string }>();
  const {
    data: ctx,
    error,
    isLoading,
  } = useSWR(
    token ? `/panel-availability/candidate/${token}` : null,
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
              "This link is no longer valid. Please contact the hiring team."}
          </p>
        </div>
      </div>
    );
  }

  if (ctx.status === "confirmed" || ctx.status === "booked" || ctx.status === "booking") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="mx-auto max-w-md rounded-xl border bg-card p-8 text-center shadow-sm">
          <CalendarCheck className="mx-auto h-10 w-10 text-emerald-500" />
          <h2 className="mt-4 text-lg font-bold">Interview Already Booked</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            This interview has already been scheduled. Check your email for the calendar invite.
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
              <h1 className="text-xl font-bold">Choose Your Interview Time</h1>
              <p className="text-sm text-muted-foreground">
                {ctx.round} interview
                {ctx.role_title && (
                  <> for <strong>{ctx.role_title}</strong></>
                )}
              </p>
            </div>
          </div>

          <SlotSelector ctx={ctx} token={token} />
        </div>
      </div>
    </div>
  );
}

function SlotSelector({ ctx, token }: { ctx: SlotContext; token: string }) {
  const duration = ctx.duration_minutes || 45;
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [showCustom, setShowCustom] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSelectSlot = async () => {
    if (selectedIndex === null) return;
    setError(null);
    setSubmitting(true);

    try {
      const res = await fetch(
        `${BASE}/panel-availability/candidate/${token}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected_index: selectedIndex }),
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

  if (result?.status === "booked") {
    return (
      <div className="flex flex-col items-center gap-4 py-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/30">
          <Video className="h-8 w-8 text-emerald-600" />
        </div>
        <h2 className="text-xl font-bold">Interview Booked!</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          A Google Meet link has been created and calendar invites sent to
          you and the interviewer.
        </p>
        {result.scheduled_at && (
          <p className="text-sm font-medium">
            {new Date(result.scheduled_at).toLocaleString(undefined, {
              weekday: "long",
              month: "long",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </p>
        )}
      </div>
    );
  }

  if (result?.status === "already_resolved") {
    return (
      <div className="flex flex-col items-center gap-4 py-8 text-center">
        <CalendarCheck className="h-12 w-12 text-blue-500" />
        <h2 className="text-xl font-bold">Already Booked</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          This interview has already been scheduled.
        </p>
      </div>
    );
  }

  if (showCustom) {
    return (
      <CustomTimePicker
        token={token}
        duration={duration}
        onBack={() => setShowCustom(false)}
        onResult={setResult}
      />
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Pick one of the available time slots below, or propose your own time.
      </p>

      <div className="space-y-3">
        {ctx.slots.map((slot) => {
          const isSelected = selectedIndex === slot.index;
          const startDate = new Date(slot.start_utc);
          return (
            <button
              key={slot.index}
              onClick={() => setSelectedIndex(slot.index)}
              className={`flex w-full items-center gap-4 rounded-lg border px-5 py-4 text-left transition ${
                isSelected
                  ? "border-primary bg-primary/5 ring-2 ring-primary/30"
                  : "border-border hover:border-primary/40 hover:bg-accent/50"
              }`}
            >
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full border-2 transition ${
                  isSelected
                    ? "border-primary bg-primary text-white"
                    : "border-muted-foreground/30"
                }`}
              >
                {isSelected && <CheckCircle2 className="h-5 w-5" />}
              </div>
              <div className="flex-1">
                <p className="font-medium">{slot.label}</p>
                <p className="text-xs text-muted-foreground">
                  {startDate.toLocaleDateString(undefined, {
                    weekday: "long",
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                  {" · "}
                  {duration} min
                </p>
              </div>
            </button>
          );
        })}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <button
          onClick={() => setShowCustom(true)}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
        >
          <CalendarPlus className="h-4 w-4" />
          None of these work — propose my own time
        </button>
        <button
          onClick={handleSelectSlot}
          disabled={submitting || selectedIndex === null}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <CheckCircle2 className="h-4 w-4" />
          )}
          Confirm This Time
        </button>
      </div>
    </div>
  );
}

function CustomTimePicker({
  token,
  duration,
  onBack,
  onResult,
}: {
  token: string;
  duration: number;
  onBack: () => void;
  onResult: (r: any) => void;
}) {
  const [today] = useState(() => new Date());
  const [currentMonth, setCurrentMonth] = useState(() => new Date().getMonth());
  const [currentYear, setCurrentYear] = useState(() => new Date().getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedHour, setSelectedHour] = useState<number | null>(null);
  const [selectedMinute, setSelectedMinute] = useState<number>(0);
  const [submitting, setSubmitting] = useState(false);
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

  const handleSubmit = async () => {
    if (!selectedDate || selectedHour === null) {
      setError("Please select both a date and time.");
      return;
    }

    setError(null);
    setSubmitting(true);

    try {
      const [year, month, day] = selectedDate.split("-").map(Number);
      const start = new Date(year, month - 1, day, selectedHour, selectedMinute);

      const res = await fetch(
        `${BASE}/panel-availability/candidate/${token}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ custom_datetime: start.toISOString() }),
        },
      );

      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `Error ${res.status}`);
      }

      const data = await res.json();
      onResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const summaryLine = (() => {
    if (!selectedDate || selectedHour === null) return null;
    const [y, mo, d] = selectedDate.split("-").map(Number);
    const dt = new Date(y, mo - 1, d, selectedHour, selectedMinute);
    const endDt = new Date(dt.getTime() + duration * 60_000);
    const fmt = (dd: Date) =>
      dd.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return `${dt.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}, ${fmt(dt)} – ${fmt(endDt)} (${duration} min)`;
  })();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Propose Your Own Time</h3>
        <button
          onClick={onBack}
          className="text-sm text-primary hover:underline"
        >
          Back to offered slots
        </button>
      </div>

      <p className="text-sm text-muted-foreground">
        Pick a date and time within the next 15 days (weekdays only).
      </p>

      {/* Calendar */}
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
            Select Time
          </h3>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
            {timeSlots.map((slot) => {
              const isSelected =
                selectedHour === slot.hour && selectedMinute === slot.minute;
              return (
                <button
                  key={`${slot.hour}:${slot.minute}`}
                  onClick={() => {
                    setSelectedHour(slot.hour);
                    setSelectedMinute(slot.minute);
                  }}
                  className={`rounded-lg border px-3 py-2 text-sm transition ${
                    isSelected
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

      {/* Summary */}
      {summaryLine && (
        <div className="flex items-center gap-3 rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
          <Video className="h-5 w-5 shrink-0 text-primary" />
          <div className="text-sm">
            <p className="font-medium">{summaryLine}</p>
            <p className="text-xs text-muted-foreground">
              Google Meet link will be generated automatically
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      <div className="flex justify-end pt-2">
        <button
          onClick={handleSubmit}
          disabled={submitting || !selectedDate || selectedHour === null}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <CheckCircle2 className="h-4 w-4" />
          )}
          Book This Time
        </button>
      </div>
    </div>
  );
}
