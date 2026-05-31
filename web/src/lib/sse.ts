// Lightweight SSE hook. EventSource doesn't support headers, so we proxy
// SSE through /api/stream/traces which injects the API key.

"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentTrace } from "./types";

export function useTraceStream(brandId?: number): AgentTrace[] {
  const [traces, setTraces] = useState<AgentTrace[]>([]);
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    const qs = brandId ? `?brand_id=${brandId}` : "";
    const es = new EventSource(`/api/stream/traces${qs}`);
    es.addEventListener("trace", (e) => {
      try {
        const t: AgentTrace = JSON.parse((e as MessageEvent).data);
        if (seen.current.has(t.id)) return;
        seen.current.add(t.id);
        setTraces((prev) => [t, ...prev].slice(0, 200));
      } catch {
        /* ignore parse errors */
      }
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [brandId]);

  return traces;
}
