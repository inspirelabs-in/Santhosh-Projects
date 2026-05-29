"use client";

import Link from "next/link";
import useSWR from "swr";
import { Crown, ArrowRight, Sparkles } from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent } from "@/components/ui/card";
import { StatusTag } from "@/components/status-tag";
import { ceoDashboard, type CEOListItem } from "@/lib/api/agentic";
import { fmtRelative } from "@/lib/utils";

export default function CEOListPage() {
  const { data, error, isLoading } = useSWR<CEOListItem[]>(
    "/dashboard/ceo/applications",
    () => ceoDashboard.list(),
    { refreshInterval: 30_000 },
  );

  return (
    <>
      <Topbar title="CEO journey" subtitle="finalists awaiting your decision" />
      <div className="flex-1 overflow-auto px-8 py-6">
        {/* Hero */}
        <div className="relative mb-6 overflow-hidden rounded-xl bg-brand-blue-deep p-6 text-white shadow-card">
          <div
            className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-brand-green/20 blur-3xl"
            aria-hidden
          />
          <div className="absolute inset-0 grain-dark" aria-hidden />
          <div className="relative flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-green text-white shadow-pop">
              <Crown className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-xl font-bold leading-tight text-white">Finalists in your queue</h2>
              <p className="mt-1 text-sm text-white/85 leading-relaxed">
                Each card aggregates resume score, phone screen, assessments,
                and the technical interview into one read-once brief.
              </p>
            </div>
          </div>
        </div>

        {error ? (
          <Card className="border-destructive/30 bg-destructive/5">
            <CardContent className="p-4 text-sm text-destructive">
              Failed to load CEO queue: {error.message}
            </CardContent>
          </Card>
        ) : null}

        {isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-36 rounded-xl skeleton" />
            ))}
          </div>
        ) : null}

        {data && data.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
              <Sparkles className="h-8 w-8 text-muted-foreground" />
              <p className="font-semibold">No finalists yet</p>
              <p className="max-w-sm text-sm text-muted-foreground">
                Candidates appear here after they clear the technical round. The
                agent will prepare a brief automatically.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {data && data.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 stagger">
            {data.map((row) => (
              <Link key={row.application_id} href={`/ceo/${row.application_id}`}>
                <Card className="group h-full transition hover:border-primary/40 hover:shadow-pop">
                  <CardContent className="flex h-full flex-col gap-3 p-5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-base font-bold tracking-tight">
                          {row.candidate_name ?? "Unnamed"}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {row.role_title ?? "—"}
                        </p>
                      </div>
                      {row.fit_score != null ? (
                        <span className="rounded-md bg-primary/10 px-2 py-1 font-mono text-[11px] font-semibold tabular-nums text-primary">
                          {row.fit_score}
                        </span>
                      ) : null}
                    </div>

                    <div className="flex items-center gap-2">
                      <StatusTag stage={row.current_stage} />
                      {row.has_brief ? (
                        <span className="rounded-full bg-success/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-success">
                          brief ready
                        </span>
                      ) : (
                        <span className="rounded-full bg-warning/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-foreground">
                          brief pending
                        </span>
                      )}
                    </div>

                    <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
                      <span>updated {fmtRelative(row.updated_at)}</span>
                      <ArrowRight className="h-4 w-4 transition group-hover:translate-x-0.5 group-hover:text-primary" />
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}
