"use client";

import { useMemo, useState } from "react";

import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

const NO_TIME = "__none__";

/**
 * Reliable cross-browser date + time picker. A native <input type="date"> plus
 * a 30-minute time <select> — avoids the quirky/unsupported datetime-local.
 * Emits an ISO-8601 (UTC) string built from the picker's LOCAL date+time, or
 * null while the selection is incomplete.
 */
export function QuickDateTime({
  onChange,
  minDate,
}: {
  onChange: (iso: string | null) => void;
  minDate?: string; // yyyy-mm-dd
}) {
  const today = useMemo(() => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }, []);

  const [date, setDate] = useState("");
  const [time, setTime] = useState("");

  const times = useMemo(() => {
    const out: { value: string; label: string }[] = [];
    for (let h = 9; h <= 20; h++) {
      for (const m of [0, 30]) {
        if (h === 20 && m > 0) continue;
        const ampm = h >= 12 ? "PM" : "AM";
        const h12 = h > 12 ? h - 12 : h === 0 ? 12 : h;
        out.push({ value: `${pad(h)}:${pad(m)}`, label: `${h12}:${pad(m)} ${ampm}` });
      }
    }
    return out;
  }, []);

  function emit(d: string, t: string) {
    if (d && t) {
      const [y, mo, da] = d.split("-").map(Number);
      const [hh, mm] = t.split(":").map(Number);
      const local = new Date(y, mo - 1, da, hh, mm);
      onChange(local.toISOString());
    } else {
      onChange(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        type="date"
        value={date}
        min={minDate || today}
        onChange={(e) => {
          setDate(e.target.value);
          emit(e.target.value, time);
        }}
        className="rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
      />
      <Select
        value={time || NO_TIME}
        onValueChange={(nv) => {
          const v = nv === NO_TIME ? "" : nv;
          setTime(v);
          emit(date, v);
        }}
      >
        <SelectTrigger className="rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20">
          <SelectValue placeholder="Pick a time…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_TIME}>Pick a time…</SelectItem>
          {times.map((t) => (
            <SelectItem key={t.value} value={t.value}>
              {t.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
