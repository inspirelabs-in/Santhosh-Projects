"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Bot, GitBranch, KeyRound, Loader2, PhoneCall, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Logo } from "@/components/brand/logo";
import { setDashboardKey } from "@/lib/auth";
import { verifyKey } from "@/lib/api";

const HIGHLIGHTS = [
  { icon: PhoneCall, label: "Outbound AI phone screens" },
  { icon: Bot, label: "Pulse recruiter agent" },
  { icon: GitBranch, label: "Assignments & interview analysis" },
  { icon: ShieldCheck, label: "Every action audited & reversible" },
];

export default function LoginPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const who = await verifyKey(key.trim());
    setBusy(false);
    if (!who) {
      setErr("That key isn't valid. Ask your admin for a fresh one.");
      return;
    }
    setDashboardKey(key.trim());
    router.replace("/dashboard");
  }

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[1.05fr_1fr]">
      {/* Left: brand-green hero — synced with the landing page. */}
      <div className="relative hidden overflow-hidden bg-brand-green text-brand-blue-deep lg:block">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="animate-float absolute -left-32 -top-32 h-[440px] w-[440px] rounded-full bg-brand-green-light/40 blur-3xl" />
          <div className="animate-float-slow absolute -bottom-40 right-[-10%] h-[520px] w-[520px] rounded-full bg-brand-blue-deep/25 blur-3xl" />
        </div>
        <div aria-hidden className="grain-dark absolute inset-0" />

        <div className="relative z-10 flex h-full flex-col justify-between p-12">
          <Logo variant="green" kind="wordmark" width={150} height={42} priority />

          <div className="max-w-lg space-y-6">
            <div className="inline-flex items-center gap-2 rounded-full bg-brand-blue-deep/10 px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em]">
              <Sparkles className="h-3 w-3" />
              Agentic hiring workspace
            </div>
            <h1 className="font-display text-4xl leading-[1.05] tracking-tight sm:text-5xl">
              Stop screening resumes.
              <br />
              <span className="text-brand-blue-deep/70">Start hiring signal.</span>
            </h1>
            <p className="text-base leading-relaxed text-brand-blue-deep/75">
              The agent screens resumes, calls candidates, runs assessments, and
              joins interviews, so your team spends time deciding, not chasing.
            </p>

            <ul className="grid grid-cols-1 gap-2.5 text-[13px] font-medium text-brand-blue-deep/85 sm:grid-cols-2">
              {HIGHLIGHTS.map(({ icon: Icon, label }) => (
                <li key={label} className="flex items-center gap-2">
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </li>
              ))}
            </ul>
          </div>

          <Link
            href="/"
            className="inline-flex w-fit items-center gap-1.5 text-[12px] font-semibold text-brand-blue-deep/60 transition-colors hover:text-brand-blue-deep"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to home
          </Link>
        </div>
      </div>

      {/* Right: sign-in card */}
      <div className="relative flex items-center justify-center bg-background p-6">
        <div aria-hidden className="grain absolute inset-0" />
        {/* Mobile-only back link */}
        <Link
          href="/"
          className="absolute left-5 top-5 inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground lg:hidden"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Home
        </Link>

        <Card className="relative w-full max-w-md border-0 shadow-none lg:border lg:shadow-card">
          <CardHeader className="space-y-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-green/10 text-brand-green">
              <KeyRound className="h-5 w-5" />
            </div>
            <CardTitle className="text-2xl font-bold tracking-tight">Sign in</CardTitle>
            <CardDescription>
              Enter your dashboard key to open the workspace. It stays on this device.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-1.5">
                <Label htmlFor="key">Dashboard key</Label>
                <Input
                  id="key"
                  type="password"
                  autoComplete="current-password"
                  placeholder="Paste your key"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  autoFocus
                />
              </div>
              {err ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                  {err}
                </div>
              ) : null}
              <Button className="w-full font-semibold" type="submit" disabled={!key || busy}>
                {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                Continue
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
