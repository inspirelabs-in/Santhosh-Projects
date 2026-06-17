"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getDashboardKey } from "@/lib/auth";
import { api } from "@/lib/api";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const MAX_SSE_RETRIES = 2;
const POLL_INTERVAL_MS = 8_000;

export interface ApplicationEvent {
  event: string;
  data: Record<string, unknown>;
  receivedAt: number;
}

export function useApplicationEvents(applicationId: string | null | undefined) {
  const [latest, setLatest] = useState<ApplicationEvent | null>(null);
  const [counter, setCounter] = useState(0);
  const ref = useRef<EventSource | null>(null);
  const retriesRef = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastStageRef = useRef<string | null>(null);

  const startPolling = useCallback((appId: string) => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const data = await api.get<{ current_stage?: string; updated_at?: string }>(
          `/dashboard/v1/applications/${appId}`,
        );
        if (data?.current_stage && data.current_stage !== lastStageRef.current) {
          lastStageRef.current = data.current_stage;
          setLatest({
            event: "stage_changed",
            data: { stage: data.current_stage },
            receivedAt: Date.now(),
          });
          setCounter((c) => c + 1);
        }
      } catch {
        // Polling failure is non-critical.
      }
    }, POLL_INTERVAL_MS);
  }, []);

  const connectSSE = useCallback(
    (appId: string, dashKey: string) => {
      const url = `${BASE}/dashboard/v1/applications/${appId}/events?key=${encodeURIComponent(dashKey)}`;
      const es = new EventSource(url);
      ref.current = es;

      es.onopen = () => {
        retriesRef.current = 0;
      };

      es.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(msg.data) as Pick<ApplicationEvent, "event" | "data">;
          setLatest({ ...parsed, receivedAt: Date.now() });
          setCounter((c) => c + 1);
        } catch {
          // Ignore malformed.
        }
      };

      es.onerror = () => {
        es.close();
        ref.current = null;
        retriesRef.current += 1;
        if (retriesRef.current > MAX_SSE_RETRIES) {
          startPolling(appId);
        }
      };
    },
    [startPolling],
  );

  useEffect(() => {
    if (!applicationId) return;
    const key = getDashboardKey();
    if (!key) return;

    retriesRef.current = 0;
    lastStageRef.current = null;
    connectSSE(applicationId, key);

    return () => {
      ref.current?.close();
      ref.current = null;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [applicationId, connectSSE]);

  return { latest, counter };
}

