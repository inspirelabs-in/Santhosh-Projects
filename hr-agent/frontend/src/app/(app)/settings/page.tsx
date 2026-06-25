"use client";

import Link from "next/link";
import useSWR from "swr";
import {
  Check,
  X,
  AlertTriangle,
  ChevronRight,
  ExternalLink,
  Mail,
  Video,
  Database,
  Phone,
  ShieldCheck,
  Building2,
  ArrowUpRight,
} from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Org, UiSettings } from "@/lib/types";
import { configApi, type ConfigSchema } from "@/lib/configClient";

export default function SettingsPage() {
  const { data: s } = useSWR<UiSettings>("/dashboard/settings", swrFetcher, {
    refreshInterval: 15000,
  });

  return (
    <>
      <Topbar title="Settings" subtitle="configuration · live health" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl px-8 py-6 pb-24">
          {s?.pipeline_readiness && (
            <PipelineReadiness readiness={s.pipeline_readiness} channels={s.channel_configured} />
          )}

          <OrgSection />

          <ConfigGroupNav />

          <section className="mt-12">
            <SectionHeading label="channels" title="Outbound + storage" />
            <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 stagger">
              {s &&
                Object.entries(s.channel_configured).map(([name, ok]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded-lg border border-border bg-card px-4 py-3"
                  >
                    <span className="font-mono text-sm lowercase">{name}</span>
                    {ok ? (
                      <span className="inline-flex items-center gap-1 font-mono text-[11px] text-success">
                        <Check className="h-3 w-3" /> configured
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
                        <X className="h-3 w-3" /> not set
                      </span>
                    )}
                  </div>
                ))}
            </div>
          </section>

          <section className="mt-12">
            <SectionHeading label="compliance" title="DPDP Act" />
            <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3 stagger">
              <MetricCard
                label="default retention"
                value={s ? `${s.data_retention_days_default}d` : "—"}
                sub="candidates & applications"
              />
              <MetricCard
                label="talent pool retention"
                value={s ? `${s.data_retention_days_talent_pool}d` : "—"}
                sub="opted-in cold pool"
              />
              <MetricCard
                label="DPO contact"
                value={s?.dpo_contact_email ?? "—"}
                mono
              />
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

function SectionHeading({ label, title }: { label: string; title: string }) {
  return (
    <div>
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
          {label}
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
      <h2 className="mt-2 font-display text-2xl">{title}</h2>
    </div>
  );
}

function MetricCard({
  label,
  value,
  sub,
  mono,
}: {
  label: string;
  value: string;
  sub?: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "mt-3 break-all text-xl leading-tight",
          mono ? "font-mono" : "font-display",
        )}
      >
        {value}
      </div>
      {sub && (
        <div className="mt-1 font-mono text-[11px] text-muted-foreground">{sub}</div>
      )}
    </div>
  );
}


function OrgSection() {
  const { data: org } = useSWR<Org>("/dashboard/settings/org", swrFetcher);

  return (
    <section className="mt-8">
      <SectionHeading label="org" title="Organization" />
      <Link
        href="/settings/org"
        className="group mt-5 flex items-center justify-between rounded-lg border border-border bg-card p-5 transition hover:border-primary/40 hover:bg-accent/20"
      >
        <div className="flex items-center gap-3">
          <Building2 className="h-5 w-5 text-muted-foreground" />
          <div>
            <p className="font-display text-base">{org?.name ?? "Loading..."}</p>
            {org && <p className="text-xs text-muted-foreground">Manage hiring persona, mission, values & more</p>}
          </div>
        </div>
        <ArrowUpRight className="h-4 w-4 text-muted-foreground transition group-hover:text-foreground" />
      </Link>
    </section>
  );
}

function ConfigGroupNav() {
  const { data: schema } = useSWR<ConfigSchema>("config:schema", () => configApi.schema());
  if (!schema) return null;
  return (
    <section className="mt-8">
      <SectionHeading label="edit" title="Configuration sections" />
      <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 stagger">
        {schema.groups.map((g) => (
          <Link
            key={g.name}
            href={`/settings/${encodeURIComponent(g.name)}`}
            className="group flex items-center justify-between rounded-lg border border-border bg-card px-5 py-4 transition-colors hover:border-foreground/30 hover:bg-card/80"
          >
            <div>
              <div className="font-display text-base">{g.name}</div>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                {g.fields.length} setting{g.fields.length === 1 ? "" : "s"}
              </div>
            </div>
            <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
          </Link>
        ))}
        <Link
          href="/settings/prompts"
          className="group flex items-center justify-between rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 transition-colors hover:border-foreground/30 hover:bg-card/80"
        >
          <div>
            <div className="font-display text-base">Prompts</div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              edit LLM prompts · Langfuse sync
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>
        <Link
          href="/settings/audit"
          className="group flex items-center justify-between rounded-lg border border-dashed border-border bg-card/40 px-5 py-4 transition-colors hover:border-foreground/30 hover:bg-card/80"
        >
          <div>
            <div className="font-display text-base">Audit log</div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              every change · who · when · diff
            </div>
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>
    </section>
  );
}

// Pipeline-readiness banner: surfaces what's blocking go-live so HR sees
// missing prod credentials at a glance instead of digging through env files.
function PipelineReadiness({
  readiness,
  channels,
}: {
  readiness: NonNullable<UiSettings["pipeline_readiness"]>;
  channels: Record<string, boolean>;
}) {
  type Item = {
    icon: React.ReactNode;
    label: string;
    ready: boolean;
    blocker: string;
    fix: string;
    helpHref?: string;
  };
  const items: Item[] = [
    {
      icon: <Mail className="h-4 w-4" />,
      label: "Outbound email (production)",
      ready: readiness.ready_for_prod_email,
      blocker:
        readiness.email_provider === "auto" || readiness.email_provider === "smtp"
          ? "Currently using SMTP/MailHog (dev only). No real email leaving."
          : "Microsoft Graph credentials missing.",
      fix: "Settings → Microsoft Graph: paste TENANT_ID / CLIENT_ID / CLIENT_SECRET (Azure AD app with Mail.Send permission + admin consent), then set EMAIL_PROVIDER=graph.",
      helpHref: "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    },
    {
      icon: <Video className="h-4 w-4" />,
      label: "Meeting capture (Read.ai)",
      ready: readiness.ready_for_meeting_capture,
      blocker: "Read.ai webhook signing key not set. Bot may join meetings but reports never reach the agent.",
      fix: "Read.ai dashboard → Webhooks → reveal signing key → paste into READ_AI_WEBHOOK_SECRET in Settings → Meeting bot.",
      helpHref: "https://app.read.ai/analytics/integrations/webhooks",
    },
    {
      icon: <Phone className="h-4 w-4" />,
      label: "AI voice screening",
      ready: !!channels.elevenlabs_voice,
      blocker: "ElevenLabs not configured. Voice screening round will be skipped.",
      fix: "elevenlabs.io → Conversational AI → create agent + connect Twilio → paste API key, agent ID, phone number ID, webhook secret in Settings → Voice screen.",
      helpHref: "https://elevenlabs.io/app/conversational-ai",
    },
    {
      icon: <Database className="h-4 w-4" />,
      label: "Calendar scheduling (Teams)",
      ready: readiness.ready_for_calendar_scheduling,
      blocker: "Microsoft Graph not configured. AI auto-schedule disabled.",
      fix: "Same Azure AD app as outbound email, plus OnlineMeetings.ReadWrite.All + Calendars.Read.All permissions.",
    },
  ];
  const blocked = items.filter((i) => !i.ready);
  const allReady = blocked.length === 0;
  return (
    <section className="mt-8">
      <SectionHeading label="readiness" title="Production checklist" />
      <div className="mt-5 rounded-lg border border-border bg-card overflow-hidden">
        <div
          className={cn(
            "flex items-center justify-between border-b border-border px-5 py-3",
            allReady ? "bg-success/10 text-success" : "bg-warning/10 text-warning",
          )}
        >
          <div className="flex items-center gap-2">
            {allReady ? (
              <ShieldCheck className="h-4 w-4" />
            ) : (
              <AlertTriangle className="h-4 w-4" />
            )}
            <span className="font-mono text-sm">
              {allReady
                ? "All production integrations configured. Pipeline ready."
                : `${blocked.length} of ${items.length} integrations need attention before go-live.`}
            </span>
          </div>
          <span className="font-mono text-[11px] uppercase tracking-[0.15em]">
            email: {readiness.email_provider} · meetings: {readiness.meeting_bot_provider}
          </span>
        </div>
        <ul className="divide-y divide-border">
          {items.map((it) => (
            <li key={it.label} className="px-5 py-4">
              <div className="flex items-start gap-3">
                <div
                  className={cn(
                    "mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full",
                    it.ready
                      ? "bg-success/15 text-success"
                      : "bg-warning/15 text-warning",
                  )}
                >
                  {it.ready ? <Check className="h-4 w-4" /> : it.icon}
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-display text-base">{it.label}</span>
                    <span
                      className={cn(
                        "font-mono text-[10px] uppercase tracking-[0.15em]",
                        it.ready ? "text-success" : "text-warning",
                      )}
                    >
                      {it.ready ? "ready" : "action needed"}
                    </span>
                  </div>
                  {!it.ready && (
                    <>
                      <p className="mt-1 text-sm text-muted-foreground">{it.blocker}</p>
                      <p className="mt-2 text-sm">
                        <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
                          fix:
                        </span>{" "}
                        {it.fix}
                      </p>
                      {it.helpHref && (
                        <a
                          href={it.helpHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1 inline-flex items-center gap-1 font-mono text-[11px] text-primary hover:underline"
                        >
                          open provider <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

