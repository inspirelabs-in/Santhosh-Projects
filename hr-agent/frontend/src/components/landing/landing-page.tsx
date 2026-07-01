"use client";

import Link from "next/link";
import {
  ArrowRight,
  Bot,
  ClipboardCheck,
  FileSearch2,
  GitBranch,
  PhoneCall,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Video,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/brand/logo";
import { FunnelScreen } from "./funnel-screen";
import { LeadsForm } from "./leads-form";

/* -------------------------------------------------------------------------- */
/* Data                                                                       */
/* -------------------------------------------------------------------------- */

const CAPABILITIES = [
  {
    icon: SlidersHorizontal,
    title: "Built around your process",
    body: "Configure the pipeline per role. Choose the stages, the order, and the bar, with auto or manual control on each one.",
  },
  {
    icon: PhoneCall,
    title: "Outbound AI voice calls",
    body: "The agent calls candidates, runs a real conversation, and scores it against the role. No scheduling ping pong.",
  },
  {
    icon: FileSearch2,
    title: "Resume parse and fit scoring",
    body: "Every application is parsed, mapped to the JD, and tiered, so shortlisting starts from signal, not a keyword match.",
  },
  {
    icon: ClipboardCheck,
    title: "Take-home assignments",
    body: "Role-grounded assignments are generated, sent, and evaluated for the reasoning behind the work, not just the output.",
  },
  {
    icon: Video,
    title: "Interview analysis",
    body: "Teams and Meet interviews are transcribed and analysed, with structured evidence surfaced back to the panel.",
  },
  {
    icon: Bot,
    title: "Pulse recruiter agent",
    body: "Draft roles, generate assignments, move candidates, and send mail in plain language, with a human confirm on every action.",
  },
];

const PIPELINE = [
  { key: "intake", label: "Intake" },
  { key: "parse", label: "Parse" },
  { key: "fit", label: "Fit score" },
  { key: "voice", label: "Voice" },
  { key: "assignment", label: "Assignment" },
  { key: "interview", label: "Interview" },
  { key: "offer", label: "Offer" },
];

const STATS = [
  { value: "Custom", label: "pipeline shaped per role" },
  { value: "24/7", label: "agent triage on every inbox" },
  { value: "100%", label: "actions audited and reversible" },
  { value: "0", label: "silent auto-rejections" },
];

/* -------------------------------------------------------------------------- */
/* Decorative                                                                 */
/* -------------------------------------------------------------------------- */

function Blobs() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="animate-float absolute -left-40 -top-40 h-[520px] w-[520px] rounded-full bg-brand-green/20 blur-3xl" />
      <div className="animate-float-slow absolute -right-32 top-24 h-[460px] w-[460px] rounded-full bg-brand-green-light/25 blur-3xl" />
      <div className="animate-float absolute bottom-[-12rem] left-1/3 h-[420px] w-[420px] rounded-full bg-brand-blue/15 blur-3xl" />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export function LandingPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur">
        <nav className="container flex h-16 items-center justify-between">
          <Logo variant="light" kind="wordmark" width={140} height={38} priority />
          <div className="hidden items-center gap-8 text-sm font-semibold text-muted-foreground md:flex">
            <a href="#capabilities" className="transition-colors hover:text-foreground">Capabilities</a>
            <a href="#pipeline" className="transition-colors hover:text-foreground">Pipeline</a>
            <a href="#pulse" className="transition-colors hover:text-foreground">Pulse agent</a>
          </div>
          <div className="flex items-center gap-2">
            <Button asChild variant="ghost" size="sm" className="hidden font-semibold sm:inline-flex">
              <a href="#contact">Get in touch</a>
            </Button>
            <Button asChild size="sm" className="font-semibold">
              <Link href="/login">
                Sign in
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </nav>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="grain absolute inset-0" />
        <Blobs />
        <div className="container relative grid gap-14 py-20 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:py-28">
          <div className="stagger max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-brand-green/30 bg-brand-green/10 px-3.5 py-1.5 text-[11px] font-bold uppercase tracking-[0.16em] text-brand-green">
              <Sparkles className="h-3.5 w-3.5" />
              Agentic hiring workspace
            </div>
            <h1 className="mt-6 font-display text-5xl leading-[1.02] tracking-tight sm:text-6xl">
              Stop screening resumes.
              <br />
              <span className="text-brand-green">Start hiring signal.</span>
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
              GrabOn Hiring Pulse runs the whole funnel: parsing, calls, assignments,
              interviews and offers. It maps real proof of work to your team context,
              so your recruiters spend their time deciding, not chasing.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Button asChild size="lg" className="brand-glow h-12 px-7 text-base font-semibold">
                <a href="#contact">
                  Get in touch
                  <ArrowRight className="h-4 w-4" />
                </a>
              </Button>
              <Button asChild size="lg" variant="outline" className="h-12 px-7 text-base font-semibold">
                <a href="#pipeline">See how it works</a>
              </Button>
            </div>
            <p className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
              <ShieldCheck className="h-4 w-4 text-brand-green" />
              Every agent action is audited, reversible, and confirmed by a human.
            </p>
          </div>

          {/* Hero card: funnel screening + philosophy (white surface) */}
          <div className="rise-in relative">
            <div className="pulse-card rounded-3xl p-6">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-brand-green">
                  Core philosophy
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-green/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-brand-green">
                  <span className="pulse-online-dot h-1.5 w-1.5 rounded-full" />
                  Live
                </span>
              </div>
              <div className="mt-4">
                <FunnelScreen />
              </div>
              <h3 className="mt-4 text-lg font-bold tracking-tight">Relevancy wins over keyword matching</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                Every applicant is weighed on real relevancy and context, how they
                fit the role and your team, not just the words on a page.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Stats band */}
      <section className="border-y border-border/60 bg-card">
        <div className="container grid grid-cols-2 gap-8 py-10 lg:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.label} className="text-center lg:text-left">
              <div className="font-display text-3xl text-brand-green sm:text-4xl">{s.value}</div>
              <div className="mt-1 text-sm text-muted-foreground">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Capabilities */}
      <section id="capabilities" className="container py-24">
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-brand-green">
            One workspace
          </span>
          <h2 className="mt-3 font-display text-4xl tracking-tight sm:text-5xl">
            Every applicant. Every signal.
          </h2>
          <p className="mt-4 text-lg text-muted-foreground">
            The agent handles the busywork end to end, so hiring decisions stay human.
          </p>
        </div>

        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="card-lift group rounded-2xl border border-border/70 bg-card p-6 shadow-card"
            >
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-green/10 text-brand-green transition-colors group-hover:bg-brand-green group-hover:text-white">
                <Icon className="h-5 w-5" />
              </div>
              <h3 className="mt-4 text-lg font-bold tracking-tight">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pipeline band */}
      <section id="pipeline" className="relative overflow-hidden border-y border-border/60 bg-brand-blue-deep text-white">
        <div aria-hidden className="grain-dark absolute inset-0" />
        <div className="container relative py-24">
          <div className="mx-auto max-w-2xl text-center">
            <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-brand-green-light">
              How it works
            </span>
            <h2 className="mt-3 font-display text-4xl tracking-tight text-white sm:text-5xl">
              A pipeline you shape per role
            </h2>
            <p className="mt-4 text-lg text-white/70">
              Enable the stages a hire actually needs, in the order that fits. The
              engine fires auto stages, parks the manual ones for review, and never
              rejects on its own.
            </p>
          </div>

          <div className="relative mx-auto mt-16 max-w-5xl">
            {/* Connector line */}
            <div className="lp-line-grow absolute left-0 right-0 top-6 hidden h-px bg-gradient-to-r from-transparent via-brand-green-light/60 to-transparent md:block" />
            <ol className="grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-4 md:grid-cols-7">
              {PIPELINE.map((stage, i) => (
                <li key={stage.key} className="relative flex flex-col items-center text-center">
                  <div className="relative z-10 flex h-12 w-12 items-center justify-center rounded-full border border-white/15 bg-white/5 font-data text-sm font-semibold text-brand-green-light backdrop-blur">
                    {String(i + 1).padStart(2, "0")}
                  </div>
                  <span className="mt-3 text-sm font-semibold">{stage.label}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      {/* Pulse agent split */}
      <section id="pulse" className="container py-24">
        <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
          <div>
            <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-brand-green">
              Pulse recruiter agent
            </span>
            <h2 className="mt-3 font-display text-4xl tracking-tight sm:text-5xl">
              Run hiring in plain language
            </h2>
            <p className="mt-4 text-lg text-muted-foreground">
              Ask Pulse to draft a role, generate a take-home, move a candidate, or
              send an email. It proposes, you confirm. Nothing leaves the workspace
              without a recruiter&apos;s nod.
            </p>
            <ul className="mt-8 space-y-4">
              {[
                { icon: Bot, t: "Draft roles and assignments", d: "Grounded in your JD and company context." },
                { icon: GitBranch, t: "Move candidates safely", d: "Overrides and advances are audited and reversible." },
                { icon: ShieldCheck, t: "Human confirm on every action", d: "The agent never sends or rejects on its own." },
              ].map(({ icon: Icon, t, d }) => (
                <li key={t} className="flex items-start gap-4">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-green/10 text-brand-green">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-semibold">{t}</div>
                    <div className="text-sm text-muted-foreground">{d}</div>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {/* Mock chat */}
          <div className="rise-in pulse-card rounded-3xl p-6">
            <div className="flex items-center gap-3 border-b border-border/60 pb-4">
              <div className="pulse-glow-ring flex h-9 w-9 items-center justify-center rounded-full bg-brand-green text-white">
                <Sparkles className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-bold">Pulse</div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="pulse-online-dot h-1.5 w-1.5 rounded-full" />
                  Agent idle, waiting for applications
                </div>
              </div>
            </div>
            <div className="space-y-3 pt-4 text-sm">
              <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-brand-green px-4 py-2.5 text-white">
                Open a Junior Node.js Developer role with a take-home.
              </div>
              <div className="w-fit max-w-[90%] rounded-2xl rounded-bl-sm border border-border/70 bg-card px-4 py-2.5">
                Drafted a pipeline with a Golang systems assignment. Review and
                confirm to publish?
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <span className="rounded-full border border-border bg-muted px-3 py-1 text-xs font-medium">Edit stages</span>
                <span className="rounded-full bg-brand-green px-3 py-1 text-xs font-semibold text-white">Confirm and publish</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Contact / leads CTA */}
      <section id="contact" className="container pb-24">
        <div className="relative overflow-hidden rounded-3xl bg-brand-green px-8 py-14 text-center text-brand-blue-deep sm:px-16">
          <div aria-hidden className="grain absolute inset-0 opacity-40" />
          <div className="relative">
            <h2 className="mx-auto max-w-2xl font-display text-4xl leading-tight tracking-tight sm:text-5xl">
              Hire on proof, not keywords.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base text-brand-blue-deep/75">
              Tell us where to reach you and we will set up a workspace for your team.
            </p>
            <div className="mt-8">
              <LeadsForm />
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border/60">
        <div className="container flex flex-col items-center justify-between gap-4 py-8 text-sm text-muted-foreground sm:flex-row">
          <Logo variant="light" kind="wordmark" width={116} height={32} />
          <span>Agentic hiring workspace · GrabOn Talent Acquisition</span>
        </div>
      </footer>
    </div>
  );
}
