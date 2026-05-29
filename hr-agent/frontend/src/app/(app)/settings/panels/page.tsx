"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Plus,
  Pencil,
  Trash2,
  Check,
  X,
  Loader2,
  Users,
  Crown,
  Wrench,
  HeartHandshake,
  Search,
  Mail,
  Globe2,
  Calendar,
  RotateCcw,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  panels,
  type PanelMember,
  type PanelMemberCreate,
  type PanelRoleType,
  type CalendarProvider,
} from "@/lib/api/panels";

interface RoleTypeMeta {
  value: PanelRoleType;
  label: string;
  description: string;
  icon: typeof Users;
  accent: string;
  badge: string;
}

const ROLE_TYPES: RoleTypeMeta[] = [
  {
    value: "technical",
    label: "Technical",
    description: "Engineers who run the technical interview round",
    icon: Wrench,
    accent: "from-blue-500/10 to-blue-500/0 border-blue-500/30",
    badge: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  },
  {
    value: "ceo",
    label: "CEO",
    description: "Founders / CEO who run the final fit interview",
    icon: Crown,
    accent: "from-amber-500/10 to-amber-500/0 border-amber-500/30",
    badge: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  },
  {
    value: "hr",
    label: "HR",
    description: "HR partners who run the offer + closing round",
    icon: HeartHandshake,
    accent: "from-emerald-500/10 to-emerald-500/0 border-emerald-500/30",
    badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
];

const COMMON_TIMEZONES = [
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Dubai",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "UTC",
];

type TabKey = "all" | PanelRoleType;

export default function PanelsPage() {
  const { data, error, isLoading, mutate } = useSWR<PanelMember[]>(
    "/dashboard/panels?include_inactive=true",
    () => panels.list({ include_inactive: true }),
    { refreshInterval: 30_000 },
  );

  const [editing, setEditing] = useState<PanelMember | null>(null);
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState<TabKey>("all");
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(true);

  const stats = useMemo(() => {
    const rows = data ?? [];
    const active = rows.filter((r) => r.is_active);
    const byType: Record<PanelRoleType, number> = {
      technical: 0,
      hr: 0,
      ceo: 0,
    };
    for (const r of active) {
      if (r.role_type in byType) byType[r.role_type as PanelRoleType]++;
    }
    return {
      total: rows.length,
      active: active.length,
      inactive: rows.length - active.length,
      byType,
    };
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data ?? []).filter((m) => {
      if (tab !== "all" && m.role_type !== tab) return false;
      if (!showInactive && !m.is_active) return false;
      if (q) {
        const hay = `${m.name} ${m.email} ${m.job_title ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [data, tab, search, showInactive]);

  const grouped = useMemo(() => {
    const g: Record<PanelRoleType, PanelMember[]> = {
      technical: [],
      hr: [],
      ceo: [],
    };
    for (const m of filtered) {
      if (m.role_type in g) g[m.role_type as PanelRoleType].push(m);
    }
    return g;
  }, [filtered]);

  return (
    <>
      <Topbar
        title="Panel members"
        subtitle="interview directory · workspace"
      />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6 space-y-6">
          <PageHeader
            title={
              <span className="font-display text-[40px] font-normal">
                Interview panels
              </span>
            }
            description={
              <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
                add once · referenced by roles · used by the agent for
                tech / CEO / HR rounds
              </span>
            }
          />

          <KpiStrip stats={stats} />

          <div className="flex flex-wrap items-center gap-2">
            <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
              <TabsList>
                <TabsTrigger value="all">All ({stats.active})</TabsTrigger>
                {ROLE_TYPES.map((rt) => (
                  <TabsTrigger key={rt.value} value={rt.value}>
                    {rt.label} ({stats.byType[rt.value]})
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>

            <div className="relative ml-auto w-72">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, email, title"
                className="pl-8"
              />
            </div>

            <Button
              size="sm"
              variant={showInactive ? "default" : "outline"}
              onClick={() => setShowInactive((v) => !v)}
              title="Toggle inactive members"
            >
              {showInactive ? "Hiding none" : "Show all"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => mutate()}>
              <RotateCcw className="mr-1 h-4 w-4" /> Refresh
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus className="mr-1 h-4 w-4" /> Add member
            </Button>
          </div>

          {error ? (
            <Card className="border-destructive/30 bg-destructive/5">
              <CardContent className="p-3 text-sm text-destructive">
                {error.message}
              </CardContent>
            </Card>
          ) : null}

          {isLoading ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-24 rounded-lg skeleton" />
              ))}
            </div>
          ) : null}

          {!isLoading && filtered.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
                <Users className="h-8 w-8 text-primary" />
                <p className="text-base font-bold">No panel members</p>
                <p className="max-w-md text-sm text-muted-foreground">
                  {(data ?? []).length === 0
                    ? "Click Add member — these power the agent's tech / CEO / HR routing."
                    : "Try clearing the search or switching tabs."}
                </p>
                {(data ?? []).length === 0 ? (
                  <Button size="sm" onClick={() => setAdding(true)}>
                    <Plus className="mr-1 h-4 w-4" /> Add first member
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {!isLoading && filtered.length > 0 ? (
            <div className="space-y-6">
              {ROLE_TYPES.filter(
                (rt) => tab === "all" || tab === rt.value,
              ).map((rt) => {
                const items = grouped[rt.value] ?? [];
                if (items.length === 0 && tab === "all") return null;
                const Icon = rt.icon;
                return (
                  <section key={rt.value}>
                    <div
                      className={`mb-3 flex items-center gap-3 rounded-lg border bg-gradient-to-r p-3 ${rt.accent}`}
                    >
                      <Icon className="h-4 w-4" />
                      <div className="flex-1">
                        <h3 className="text-sm font-bold">{rt.label}</h3>
                        <p className="text-[11px] text-muted-foreground">
                          {rt.description}
                        </p>
                      </div>
                      <span
                        className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] ${rt.badge}`}
                      >
                        {items.length}
                      </span>
                    </div>
                    {items.length === 0 ? (
                      <Card>
                        <CardContent className="px-3 py-4 text-xs text-muted-foreground">
                          No matches in this tab.
                        </CardContent>
                      </Card>
                    ) : (
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                        {items.map((m) => (
                          <MemberCard
                            key={m.id}
                            m={m}
                            meta={rt}
                            onEdit={() => setEditing(m)}
                            onChanged={() => mutate()}
                          />
                        ))}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      {(adding || editing) && (
        <MemberDialog
          existing={editing}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSaved={() => {
            setAdding(false);
            setEditing(null);
            mutate();
          }}
        />
      )}
    </>
  );
}

function KpiStrip({
  stats,
}: {
  stats: {
    total: number;
    active: number;
    inactive: number;
    byType: Record<PanelRoleType, number>;
  };
}) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      <Kpi label="Total" value={stats.total} />
      <Kpi label="Active" value={stats.active} accent="ok" />
      <Kpi label="Inactive" value={stats.inactive} muted />
      <Kpi label="Tech" value={stats.byType.technical} />
      <Kpi label="CEO · HR" value={stats.byType.ceo + stats.byType.hr} />
    </div>
  );
}

function Kpi({
  label,
  value,
  accent,
  muted,
}: {
  label: string;
  value: number | string;
  accent?: "ok";
  muted?: boolean;
}) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </p>
        <p
          className={`mt-1 text-2xl font-bold tabular-nums ${
            accent === "ok"
              ? "text-success"
              : muted
                ? "text-muted-foreground"
                : ""
          }`}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function MemberCard({
  m,
  meta,
  onEdit,
  onChanged,
}: {
  m: PanelMember;
  meta: RoleTypeMeta;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function toggleActive() {
    setBusy(true);
    try {
      await panels.update(m.id, { is_active: !m.is_active });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (
      !window.confirm(
        `Deactivate ${m.name}? They'll stop appearing in role pickers.`,
      )
    )
      return;
    setBusy(true);
    try {
      await panels.remove(m.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const initials = m.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");

  return (
    <Card
      className={`group transition hover:shadow-md hover:ring-1 hover:ring-border ${
        m.is_active ? "" : "opacity-60"
      }`}
    >
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full font-mono text-xs font-bold ${meta.badge}`}
          >
            {initials || "?"}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate font-semibold leading-tight">
                  {m.name}
                  {!m.is_active ? (
                    <span className="ml-2 rounded-full bg-muted px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.15em] text-muted-foreground">
                      inactive
                    </span>
                  ) : null}
                </p>
                {m.job_title ? (
                  <p className="truncate text-xs text-muted-foreground">
                    {m.job_title}
                  </p>
                ) : null}
              </div>
              <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                <button
                  onClick={onEdit}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  title="Edit"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={toggleActive}
                  disabled={busy}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  title={m.is_active ? "Deactivate" : "Reactivate"}
                >
                  {m.is_active ? (
                    <X className="h-3.5 w-3.5" />
                  ) : (
                    <Check className="h-3.5 w-3.5" />
                  )}
                </button>
                <button
                  onClick={remove}
                  disabled={busy}
                  className="rounded-md p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            <div className="mt-2 space-y-1 text-xs">
              <a
                href={`mailto:${m.email}`}
                className="flex items-center gap-1.5 truncate text-muted-foreground hover:text-foreground hover:underline"
              >
                <Mail className="h-3 w-3 shrink-0" />
                <span className="truncate">{m.email}</span>
              </a>
              <p className="flex items-center gap-1.5 truncate text-muted-foreground">
                <Globe2 className="h-3 w-3 shrink-0" />
                {m.timezone}
              </p>
              <p className="flex items-center gap-1.5 truncate text-muted-foreground">
                <Calendar className="h-3 w-3 shrink-0" />
                {m.calendar_provider}
                {m.calendar_id ? (
                  <span className="truncate">· {m.calendar_id}</span>
                ) : null}
              </p>
            </div>

            {m.notes ? (
              <p className="mt-2 line-clamp-2 rounded-md bg-muted/40 px-2 py-1 text-[11px] italic text-muted-foreground">
                {m.notes}
              </p>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function MemberDialog({
  existing,
  onClose,
  onSaved,
}: {
  existing: PanelMember | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<PanelMemberCreate>({
    name: existing?.name ?? "",
    email: existing?.email ?? "",
    role_type: (existing?.role_type as PanelRoleType) ?? "technical",
    job_title: existing?.job_title ?? "",
    timezone: existing?.timezone ?? "Asia/Kolkata",
    calendar_provider:
      (existing?.calendar_provider as CalendarProvider) ?? "microsoft",
    calendar_id: existing?.calendar_id ?? "",
    notes: existing?.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const payload: PanelMemberCreate = {
        ...form,
        job_title: form.job_title || null,
        calendar_id: form.calendar_id || null,
        notes: form.notes || null,
      };
      if (existing) {
        await panels.update(existing.id, payload);
      } else {
        await panels.create(payload);
      }
      onSaved();
    } catch (e: any) {
      setError(e?.detail?.detail ?? e?.message ?? "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider">
            {existing ? "Edit panel member" : "Add panel member"}
          </h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Full name">
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                placeholder="Jane Doe"
              />
            </Field>
            <Field label="Job title">
              <input
                value={form.job_title ?? ""}
                onChange={(e) =>
                  setForm({ ...form, job_title: e.target.value })
                }
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                placeholder="Senior Backend Engineer"
              />
            </Field>
          </div>

          <Field label="Email">
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              placeholder="jane@grabon.in"
            />
          </Field>

          <Field label="Panel type">
            <div className="grid grid-cols-3 gap-2">
              {ROLE_TYPES.map((rt) => {
                const Icon = rt.icon;
                const active = form.role_type === rt.value;
                return (
                  <button
                    key={rt.value}
                    type="button"
                    onClick={() => setForm({ ...form, role_type: rt.value })}
                    className={`flex flex-col items-start gap-1 rounded-md border px-3 py-2 text-left text-xs transition ${
                      active
                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                        : "border-border hover:bg-muted/40"
                    }`}
                  >
                    <span className="flex items-center gap-1.5 font-semibold">
                      <Icon className="h-3.5 w-3.5" />
                      {rt.label}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      {rt.description}
                    </span>
                  </button>
                );
              })}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Timezone">
              <select
                value={form.timezone}
                onChange={(e) =>
                  setForm({ ...form, timezone: e.target.value })
                }
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              >
                {COMMON_TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Calendar provider">
              <select
                value={form.calendar_provider}
                onChange={(e) =>
                  setForm({
                    ...form,
                    calendar_provider: e.target.value as CalendarProvider,
                  })
                }
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              >
                <option value="microsoft">Microsoft 365</option>
                <option value="google">Google</option>
                <option value="none">None / manual</option>
              </select>
            </Field>
          </div>
          <Field label="Calendar id (optional)">
            <input
              value={form.calendar_id ?? ""}
              onChange={(e) =>
                setForm({ ...form, calendar_id: e.target.value })
              }
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              placeholder="leave blank to default to email"
            />
          </Field>
          <Field label="Notes (optional)">
            <textarea
              value={form.notes ?? ""}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              rows={2}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              placeholder="Any context for HR..."
            />
          </Field>

          {error ? <p className="text-xs text-destructive">{error}</p> : null}

          <div className="flex justify-end gap-2 pt-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={save}
              disabled={busy || !form.name || !form.email}
            >
              {busy ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              {existing ? "Save changes" : "Add member"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}
