"use client";

import { useState, useCallback } from "react";
import useSWR from "swr";
import { Search, MapPin, Briefcase, Users, Sparkles } from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  talentSearchApi,
  type TalentMatch,
  type TalentSearchResponse,
} from "@/lib/api/talent-search";
import type { Role } from "@/lib/types";

function similarityVariant(s: number): "success" | "warning" | "muted" {
  if (s >= 0.8) return "success";
  if (s >= 0.6) return "warning";
  return "muted";
}

function pct(s: number) {
  return `${Math.round(s * 100)}%`;
}

const ALL_ROLES = "__all__";

export default function TalentSearchPage() {
  const [query, setQuery] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [results, setResults] = useState<TalentSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: roles } = useSWR<Role[]>("/dashboard/roles", swrFetcher, {
    refreshInterval: 60_000,
  });

  const search = useCallback(async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setSelectedRole("");
    try {
      const res = await talentSearchApi.query(query.trim());
      setResults(res);
    } catch (e: any) {
      setError(e.message ?? "Search failed");
    } finally {
      setLoading(false);
    }
  }, [query]);

  const matchToRole = useCallback(
    async (roleId: string) => {
      if (!roleId) return;
      setSelectedRole(roleId);
      setLoading(true);
      setError(null);
      setQuery("");
      try {
        const res = await talentSearchApi.matchRole(roleId);
        setResults(res);
      } catch (e: any) {
        setError(e.message ?? "Match failed");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return (
    <>
      <Topbar title="Talent search" subtitle="semantic search across your talent pool" />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24">
        <div className="relative mb-6 overflow-hidden rounded-xl bg-violet-700 p-6 text-white shadow-card">
          <div
            className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-violet-300/30 blur-3xl"
            aria-hidden
          />
          <div className="absolute inset-0 grain-dark" aria-hidden />
          <div className="relative flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white/15 text-white shadow-pop">
              <Users className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-xl font-bold leading-tight text-white">
                Talent pool search
              </h2>
              <p className="mt-1 text-sm text-white/85 leading-relaxed">
                Search candidates by skill, experience, or background. Match your talent pool
                against open roles to surface the best fits.
              </p>
            </div>
          </div>
        </div>

        <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex flex-1 gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && search()}
                placeholder="Search by skills, experience, background..."
                className="h-10 w-full rounded-lg border border-border bg-card pl-10 pr-4 text-sm outline-none ring-ring focus:ring-2"
              />
            </div>
            <Button onClick={search} disabled={loading || !query.trim()}>
              <Search className="mr-1.5 h-4 w-4" />
              Search
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap">
              Match to role
            </span>
            <Select
              value={selectedRole || ALL_ROLES}
              onValueChange={(nv) => matchToRole(nv === ALL_ROLES ? "" : nv)}
            >
              <SelectTrigger className="h-10 min-w-[200px] rounded-lg border border-border bg-card px-3 text-sm outline-none ring-ring focus:ring-2">
                <SelectValue placeholder="Select a role..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_ROLES}>Select a role...</SelectItem>
                {roles
                  ?.filter((r) => r.status === "open")
                  .map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.title}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {error && (
          <Card className="mb-4 border-destructive/30 bg-destructive/5">
            <CardContent className="p-4 text-sm text-destructive">
              {error}
            </CardContent>
          </Card>
        )}

        {loading && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-48 rounded-xl skeleton" />
            ))}
          </div>
        )}

        {!loading && results && results.matches.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
              <Sparkles className="h-8 w-8 text-muted-foreground" />
              <p className="font-semibold">No matches found</p>
              <p className="max-w-sm text-sm text-muted-foreground">
                Try broadening your search terms or selecting a different role.
              </p>
            </CardContent>
          </Card>
        )}

        {!loading && results && results.matches.length > 0 && (
          <>
            <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
              {results.total} result{results.total !== 1 ? "s" : ""}
            </p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 stagger">
              {results.matches.map((m) => (
                <TalentCard key={m.candidate_id} match={m} />
              ))}
            </div>
          </>
        )}

        {!loading && !results && !error && (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
              <Search className="h-8 w-8 text-muted-foreground" />
              <p className="font-semibold">Search your talent pool</p>
              <p className="max-w-sm text-sm text-muted-foreground">
                Enter a query above to find candidates by skill, role, or experience,
                or match against an open role.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}

function TalentCard({ match: m }: { match: TalentMatch }) {
  return (
    <Card className="h-full transition hover:border-primary/40 hover:shadow-pop">
      <CardContent className="flex h-full flex-col gap-3 p-5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-base font-bold tracking-tight">
              {m.candidate_name ?? "Unnamed"}
            </p>
            {(m.current_title || m.current_company) && (
              <p className="flex items-center gap-1 truncate text-xs text-muted-foreground">
                <Briefcase className="h-3 w-3 shrink-0" />
                {[m.current_title, m.current_company].filter(Boolean).join(" at ")}
              </p>
            )}
            {m.candidate_email && (
              <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                {m.candidate_email}
              </p>
            )}
          </div>
          <Badge variant={similarityVariant(m.similarity)} className="shrink-0 font-mono tabular-nums">
            {pct(m.similarity)}
          </Badge>
        </div>

        {m.top_skills.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {m.top_skills.slice(0, 5).map((skill) => (
              <span
                key={skill}
                className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
              >
                {skill}
              </span>
            ))}
            {m.top_skills.length > 5 && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                +{m.top_skills.length - 5}
              </span>
            )}
          </div>
        )}

        <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
            {m.location && (
              <span className="flex items-center gap-1">
                <MapPin className="h-3 w-3" />
                {m.location}
              </span>
            )}
            {m.experience_years != null && (
              <span>{m.experience_years}y exp</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {m.last_role_applied && (
              <span className="font-mono text-[10px] text-muted-foreground truncate max-w-[120px]">
                {m.last_role_applied}
              </span>
            )}
            {m.last_application_status && (
              <span className={cn(
                "rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em]",
                m.last_application_status === "hired"
                  ? "bg-success/10 text-success"
                  : m.last_application_status === "rejected"
                    ? "bg-destructive/10 text-destructive"
                    : "bg-muted text-muted-foreground",
              )}>
                {m.last_application_status}
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
