"use client";

import { useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { configApi, type ProbeResult } from "@/lib/configClient";

interface Props {
  integration: string;
  buildCandidate: () => Record<string, unknown>;
  disabled?: boolean;
}

export function TestConnectionButton({ integration, buildCandidate, disabled }: Props) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProbeResult | null>(null);

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      const res = await configApi.test(integration, buildCandidate());
      setResult(res);
    } catch (e) {
      setResult({
        ok: false,
        message: e instanceof Error ? e.message : "Test failed",
        detail: {},
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" variant="outline" size="sm" onClick={run} disabled={disabled || busy}>
        {busy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
        Test connection
      </Button>
      {result && (
        <span
          className={`inline-flex items-center gap-1 font-mono text-[11px] ${
            result.ok ? "text-success" : "text-destructive"
          }`}
        >
          {result.ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
          {result.message}
        </span>
      )}
    </div>
  );
}
