"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronRight, Sparkles } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { ConfigForm } from "@/components/config/ConfigForm";
import { cn } from "@/lib/utils";

interface Step {
  title: string;
  description: string;
  group: string;
  keys: string[];
  helpUrl?: string;
}

const STEPS: Step[] = [
  {
    title: "AI engine",
    description:
      "Pick your LLM provider and paste an API key. The system uses two models — a fast one for screening generation and a smarter one for evaluation.",
    group: "LLM Engine",
    keys: [
      "LLM_PROVIDER",
      "OPENAI_API_KEY",
      "ANTHROPIC_API_KEY",
      "GROQ_API_KEY",
      "LLM_MODEL_FAST",
      "LLM_MODEL_SMART",
    ],
    helpUrl: "https://platform.openai.com/api-keys",
  },
  {
    title: "Email delivery",
    description:
      "Candidate emails go through Resend. Add your API key and a verified sender address. SMTP fallback is in the advanced section.",
    group: "Email",
    keys: ["RESEND_API_KEY", "RESEND_FROM_EMAIL", "RESEND_FROM_NAME", "RESEND_REPLY_TO"],
    helpUrl: "https://resend.com/api-keys",
  },
  {
    title: "Calendar",
    description:
      "Pick one — Microsoft Graph (for Teams meetings) or a Google service account. Skip if you'll schedule manually.",
    group: "Calendar",
    keys: [
      "GRAPH_TENANT_ID",
      "GRAPH_CLIENT_ID",
      "GRAPH_CLIENT_SECRET",
      "GRAPH_ORGANISER_EMAIL",
      "GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON",
      "GOOGLE_CALENDAR_IMPERSONATE_USER",
    ],
  },
  {
    title: "Voice screening (optional)",
    description:
      "Have ElevenLabs auto-call candidates for the screening round. You can flip this on later from Settings → Voice Screening.",
    group: "Voice Screening",
    keys: [
      "ENABLE_VOICE_SCREENING",
      "ELEVENLABS_API_KEY",
      "ELEVENLABS_AGENT_ID",
      "ELEVENLABS_PHONE_NUMBER_ID",
    ],
  },
  {
    title: "Branding",
    description:
      "What candidates see in emails and call openings.",
    group: "Branding",
    keys: ["APP_BASE_URL", "FRONTEND_BASE_URL"],
  },
  {
    title: "Compliance",
    description:
      "How long to keep candidate data and who handles privacy requests (DPDP).",
    group: "Compliance",
    keys: ["DATA_RETENTION_DAYS_DEFAULT", "DATA_RETENTION_DAYS_TALENT_POOL", "DPO_CONTACT_EMAIL"],
  },
];

export default function SetupWizardPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [completed, setCompleted] = useState<Set<number>>(new Set());

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  function next() {
    setCompleted((c) => new Set(c).add(step));
    if (isLast) {
      router.push("/settings");
    } else {
      setStep((s) => s + 1);
    }
  }

  return (
    <>
      <Topbar title="Setup" subtitle="first-run configuration" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl px-8 py-6 pb-24">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
              welcome
            </span>
          </div>
          <h1 className="mt-3 font-display text-[40px] leading-tight">
            Let&apos;s get your hiring agent running
          </h1>
          <p className="mt-2 max-w-2xl font-mono text-[12px] text-muted-foreground">
            6 steps. Each one tests itself before saving so you know it works. You can
            change anything later from Settings.
          </p>

          <ol className="mt-8 flex flex-wrap gap-2">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <button
                  type="button"
                  onClick={() => setStep(i)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
                    i === step
                      ? "border-foreground bg-foreground text-background"
                      : completed.has(i)
                        ? "border-success/40 bg-success/10 text-success"
                        : "border-border bg-card text-muted-foreground hover:text-foreground",
                  )}
                >
                  {completed.has(i) && <Check className="h-3 w-3" />}
                  {i + 1}. {s.title}
                </button>
              </li>
            ))}
          </ol>

          <div className="mt-10 rounded-xl border border-border bg-card p-6">
            <h2 className="font-display text-2xl">{current.title}</h2>
            <p className="mt-2 max-w-2xl font-mono text-[12px] text-muted-foreground">
              {current.description}
            </p>
            {current.helpUrl && (
              <a
                href={current.helpUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block font-mono text-[11px] text-primary hover:underline"
              >
                where to get this →
              </a>
            )}

            <div className="mt-6">
              <ConfigForm
                group={current.group}
                onlyKeys={current.keys}
                onSaved={() => setCompleted((c) => new Set(c).add(step))}
              />
            </div>
          </div>

          <div className="mt-6 flex items-center justify-between">
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={step === 0}
                onClick={() => setStep((s) => Math.max(0, s - 1))}
              >
                Back
              </Button>
              <Link
                href="/settings"
                className="font-mono text-[11px] text-muted-foreground hover:text-foreground self-center ml-2"
              >
                skip wizard →
              </Link>
            </div>
            <Button onClick={next}>
              {isLast ? "Finish" : "Next"}
              <ChevronRight className="ml-1 h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
