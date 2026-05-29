"use client";

import { useState } from "react";
import useSWR from "swr";
import { Briefcase, MapPin, Search } from "lucide-react";
import { Logo } from "@/components/brand/logo";

interface Role {
  id: string;
  title: string;
  department?: string;
  location?: string;
  description?: string;
  status: string;
}

async function publicFetcher(url: string) {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const res = await fetch(`${base}${url}`);
  if (!res.ok) throw new Error(`fetch ${url}: ${res.status}`);
  return res.json();
}

export default function CareersPage() {
  const { data: roles } = useSWR<Role[]>("/careers/roles", publicFetcher);
  const [q, setQ] = useState("");

  const filtered = (roles ?? []).filter(
    (r) =>
      r.status === "open" &&
      (r.title.toLowerCase().includes(q.toLowerCase()) ||
        (r.department ?? "").toLowerCase().includes(q.toLowerCase())),
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Hero */}
      <header className="relative overflow-hidden border-b border-border bg-gradient-to-br from-primary/5 via-background to-secondary/5">
        <div className="mx-auto max-w-4xl px-6 py-20 text-center">
          <Logo width={48} kind="icon" className="mx-auto mb-6" />
          <h1 className="text-4xl font-extrabold tracking-tight md:text-5xl">
            Join <span className="text-primary">GrabOn</span>
          </h1>
          <p className="mx-auto mt-4 max-w-lg text-lg text-muted-foreground">
            Help millions save more. Build technology that matters.
          </p>
          <div className="mx-auto mt-8 max-w-md relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search roles..."
              className="w-full rounded-lg border border-border bg-card py-3 pl-10 pr-4 text-sm shadow-card outline-none focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
            />
          </div>
        </div>
      </header>

      {/* Listings */}
      <main className="mx-auto max-w-4xl px-6 py-12">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          {filtered.length} open position{filtered.length !== 1 ? "s" : ""}
        </h2>
        <div className="mt-4 space-y-3">
          {filtered.map((role) => (
            <a
              key={role.id}
              href={`/careers/${role.id}`}
              className="card-lift block rounded-xl border border-border bg-card p-5 shadow-card transition"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-bold tracking-tight">{role.title}</h3>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    {role.department && (
                      <span className="flex items-center gap-1">
                        <Briefcase className="h-3 w-3" /> {role.department}
                      </span>
                    )}
                    {role.location && (
                      <span className="flex items-center gap-1">
                        <MapPin className="h-3 w-3" /> {role.location}
                      </span>
                    )}
                  </div>
                </div>
                <span className="shrink-0 rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                  Apply
                </span>
              </div>
              {role.description && (
                <p className="mt-3 line-clamp-2 text-sm text-muted-foreground">
                  {role.description}
                </p>
              )}
            </a>
          ))}
          {filtered.length === 0 && (
            <p className="py-12 text-center text-sm text-muted-foreground">
              {q ? "No roles match your search." : "No open positions right now. Check back soon!"}
            </p>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-8 text-center text-xs text-muted-foreground">
        <Logo width={24} kind="icon" className="mx-auto mb-2" />
        &copy; {new Date().getFullYear()} GrabOn. All rights reserved.
      </footer>
    </div>
  );
}
