"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2, ShieldCheck, Sparkles, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Logo } from "@/components/brand/logo";
import { setDashboardKey } from "@/lib/auth";
import { verifyKey } from "@/lib/api";

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
      setErr("That key isn't valid. Ask an admin for a fresh one, or check your .env for DASHBOARD_KEYS.");
      return;
    }
    setDashboardKey(key.trim());
    router.replace("/dashboard");
  }

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      {/* Left: brand-green hero. Single brand colour, no gradient drift. */}
      <div className="relative hidden overflow-hidden bg-brand-green text-white lg:block">
        {/* Layered ambient shapes -- subtle, brand-tinted */}
        <div
          className="absolute -left-32 -top-32 h-[420px] w-[420px] rounded-full bg-brand-green-light/40 blur-3xl"
          aria-hidden
        />
        <div
          className="absolute -bottom-40 right-[-10%] h-[520px] w-[520px] rounded-full bg-brand-blue-deep/30 blur-3xl"
          aria-hidden
        />
        <div className="absolute inset-0 grain-dark" aria-hidden />

        <div className="relative z-10 flex h-full flex-col justify-between p-12">
          <Logo variant="green" kind="wordmark" width={150} height={42} priority />

          <div className="space-y-6 max-w-lg">
            <div className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] backdrop-blur">
              <Sparkles className="h-3 w-3" />
              Agentic Hiring · V2
            </div>
            <h1 className="text-4xl font-extrabold tracking-tight leading-[1.05]">
              Every applicant. Every signal.<br />
              <span className="text-brand-green-light">One workspace.</span>
            </h1>
            <p className="text-white/85 text-base leading-relaxed">
              The agent screens resumes, calls candidates, runs assessments, and
              joins technical interviews -- so HR can spend time deciding, not
              chasing.
            </p>

            <ul className="grid grid-cols-1 gap-2.5 text-[13px] text-white/85 sm:grid-cols-2">
              <li className="flex items-center gap-2">
                <Zap className="h-3.5 w-3.5 text-brand-green-light" />
                Outbound AI phone screens
              </li>
              <li className="flex items-center gap-2">
                <Zap className="h-3.5 w-3.5 text-brand-green-light" />
                PI behavioral + cognitive
              </li>
              <li className="flex items-center gap-2">
                <Zap className="h-3.5 w-3.5 text-brand-green-light" />
                Teams meeting analysis
              </li>
              <li className="flex items-center gap-2">
                <ShieldCheck className="h-3.5 w-3.5 text-brand-green-light" />
                DPDP-compliant by default
              </li>
            </ul>
          </div>

          <p className="font-mono text-[11px] text-white/55">
            Inspirelabs Solutions Pvt. Ltd. · Hyderabad · dpo@grabon.in
          </p>
        </div>
      </div>

      {/* Right: sign-in card */}
      <div className="flex items-center justify-center bg-background p-6">
        <Card className="w-full max-w-md border-0 shadow-none lg:border lg:shadow-card">
          <CardHeader className="space-y-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <KeyRound className="h-5 w-5" />
            </div>
            <CardTitle className="text-2xl font-bold tracking-tight">Sign in</CardTitle>
            <CardDescription>
              Paste the dashboard key your admin issued. Stored locally on this device only.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-1.5">
                <Label htmlFor="key">Dashboard key</Label>
                <Input
                  id="key"
                  type="password"
                  placeholder="admin_… / rec_… / view_…"
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
              <p className="text-xs text-muted-foreground">
                Looking for your key? Check the <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">DASHBOARD_KEYS</code> entry in your backend <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">.env</code>.
              </p>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
