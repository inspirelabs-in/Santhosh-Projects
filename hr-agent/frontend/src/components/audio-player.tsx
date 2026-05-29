"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Compact audio player for voice-call recordings.
 * - Brand-green progress bar tracks current time.
 * - Click anywhere on the bar to seek.
 * - Volume + total duration shown on the right.
 *
 * Designed for in-card embedding in candidate detail / CEO journey -- not
 * a full-blown waveform component (that needs server-side waveform JSON).
 */
export function AudioPlayer({
  src,
  className,
}: {
  src: string;
  className?: string;
}) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState<number>(0);
  const [current, setCurrent] = useState<number>(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onMeta = () => setDuration(el.duration || 0);
    const onTime = () => setCurrent(el.currentTime || 0);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("ended", onPause);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("ended", onPause);
    };
  }, []);

  function toggle() {
    const el = ref.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      el.play().catch(() => undefined);
    }
  }

  function seek(clientX: number, container: HTMLDivElement) {
    const el = ref.current;
    if (!el || !duration) return;
    const rect = container.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    el.currentTime = ratio * duration;
  }

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border bg-card p-3",
        className,
      )}
    >
      <audio ref={ref} src={src} preload="metadata" />
      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? "Pause" : "Play"}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm transition hover:opacity-90"
      >
        {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
      </button>

      <div
        className="relative h-2 flex-1 cursor-pointer overflow-hidden rounded-full bg-muted"
        onClick={(e) => seek(e.clientX, e.currentTarget)}
      >
        <div
          className="h-full bg-primary transition-[width]"
          style={{ width: duration ? `${(current / duration) * 100}%` : "0%" }}
        />
      </div>

      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
        {fmt(current)} / {fmt(duration)}
      </span>
      <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
    </div>
  );
}

function fmt(seconds: number): string {
  if (!seconds || !isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
