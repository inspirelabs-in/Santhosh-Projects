"use client";

import { useState } from "react";
import { ArrowRight, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

type State = "idle" | "sending" | "done" | "error";

export function LeadsForm() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!EMAIL_RE.test(value)) {
      setError("Please enter a valid email.");
      setState("error");
      return;
    }
    setState("sending");
    setError(null);
    try {
      const res = await fetch(`${BASE}/leads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: value }),
      });
      if (res.ok) {
        setState("done");
        return;
      }
      if (res.status === 503) {
        setError("Lead capture isn't set up yet. Please try again later.");
      } else if (res.status === 422) {
        setError("Please enter a valid email.");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setState("error");
    } catch {
      setError("Couldn't reach the server. Please try again.");
      setState("error");
    }
  }

  if (state === "done") {
    return (
      <div className="mx-auto flex max-w-md items-center justify-center gap-2 rounded-xl bg-brand-blue-deep/10 px-4 py-3 text-sm font-semibold text-brand-blue-deep">
        <Check className="h-4 w-4" />
        Thanks, we&apos;ll reach out shortly.
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="mx-auto w-full max-w-md">
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (state === "error") setState("idle");
          }}
          className="h-12 flex-1 rounded-lg border border-brand-blue-deep/20 bg-white px-4 text-sm text-brand-blue-deep outline-none placeholder:text-brand-blue-deep/40 focus:border-brand-blue-deep/50 focus:ring-2 focus:ring-brand-blue-deep/15"
          aria-label="Work email"
        />
        <Button
          type="submit"
          size="lg"
          disabled={state === "sending"}
          className="h-12 shrink-0 bg-brand-blue-deep px-6 text-base font-semibold text-white hover:bg-brand-blue-deep/90"
        >
          {state === "sending" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <>
              Get in touch
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </Button>
      </div>
      {error ? (
        <p className="mt-2 text-center text-xs font-medium text-brand-blue-deep/70 sm:text-left">
          {error}
        </p>
      ) : null}
    </form>
  );
}
