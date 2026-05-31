// Typed API client. Server-side route handlers forward to the backend
// with the API key from `GRABON_API_KEY` env. Browser code should hit our
// own /api/* proxy so the key never lands in the bundle.

import type { AgentTrace, Approval, Brand, Dossier, LeadEvent, Notification, Person, PipelineStages, Signal } from "./types";

function getBase(): string {
  // Server-side: prefer BACKEND_URL (Docker internal network e.g. http://api:8000)
  if (typeof window === "undefined" && process.env.BACKEND_URL) {
    return process.env.BACKEND_URL;
  }
  return process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
}

function key(): string {
  // Server-only — set in route handlers / server components.
  const k = process.env.GRABON_API_KEY;
  if (!k) throw new Error("GRABON_API_KEY missing");
  return k;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-API-Key": key(),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${getBase()}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  listBrands: (opts: { status?: string; tier?: string; q?: string; has_domain?: boolean; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.status) p.set("status", opts.status);
    if (opts.tier) p.set("tier", opts.tier);
    if (opts.q) p.set("q", opts.q);
    if (opts.has_domain) p.set("has_domain", "true");
    if (opts.limit) p.set("limit", String(opts.limit));
    if (opts.offset) p.set("offset", String(opts.offset));
    return call<{ items: Brand[] }>(`/brands?${p}`);
  },
  getBrand: (id: number) => call<{ brand: Brand; latest_dossier: Dossier | null }>(`/brands/${id}`),
  listDossierVersions: (brandId: number) =>
    call<{ items: Pick<Dossier, "id" | "version" | "generated_at" | "cost_cents">[] }>(
      `/dossiers/by-brand/${brandId}`
    ),
  getDossier: (id: number) => call<Dossier>(`/dossiers/${id}`),
  recentSignals: (opts: { type?: string; source?: string; limit?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.type) p.set("type", opts.type);
    if (opts.source) p.set("source", opts.source);
    p.set("limit", String(opts.limit ?? 100));
    return call<{ items: Signal[] }>(`/signals?${p}`);
  },
  approvalsQueue: () => call<{ items: Approval[] }>(`/approvals?status=pending`),
  decideApproval: (id: number, body: { status: string; reviewer: string; notes?: string }) =>
    call<{ id: number; status: string }>(`/approvals/${id}/decide`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  triggerDossier: (body: { brand_id: number; reason?: string }) =>
    call<{ workflow_id: string; run_id: string }>(`/workflows/dossier`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  triggerDiscovery: (body: { collector: string; params: Record<string, unknown>; fan_out?: boolean }) =>
    call<{ workflow_id: string; run_id: string }>(`/workflows/discovery`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listTraces: (opts: { limit?: number; brand_id?: number; agent?: string } = {}) => {
    const p = new URLSearchParams();
    p.set("limit", String(opts.limit ?? 30));
    if (opts.brand_id) p.set("brand_id", String(opts.brand_id));
    if (opts.agent) p.set("agent", opts.agent);
    return call<{ items: (AgentTrace & { brand_name?: string })[] }>(`/traces?${p}`);
  },

  // Contacts
  listContacts: (opts: { brand_id?: number; limit?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.brand_id) p.set("brand_id", String(opts.brand_id));
    p.set("limit", String(opts.limit ?? 50));
    return call<{ items: Person[] }>(`/contacts?${p}`);
  },
  createContact: (body: { brand_id: number; name: string; title?: string; email?: string; linkedin_url?: string }) =>
    call<Person>(`/contacts`, { method: "POST", body: JSON.stringify(body) }),

  // Notifications
  listNotifications: (opts: { unread_only?: boolean; limit?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.unread_only) p.set("unread_only", "true");
    p.set("limit", String(opts.limit ?? 50));
    return call<{ items: Notification[]; unread_count: number }>(`/notification-center?${p}`);
  },
  markNotificationRead: (id: number) =>
    call<{ id: number }>(`/notification-center/read/${id}`, { method: "POST" }),
  markAllNotificationsRead: () =>
    call<{ marked_read: number }>(`/notification-center/read-all`, { method: "POST" }),

  // Lifecycle
  transitionStatus: (brandId: number, body: { to_status: string; note?: string; actor?: string; force?: boolean }) =>
    call<{ brand_id: number; from: string; to: string }>(`/lifecycle/${brandId}/transition`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getTimeline: (brandId: number) =>
    call<{ items: LeadEvent[] }>(`/lifecycle/${brandId}/timeline`),
  getPipelineStages: () =>
    call<PipelineStages>(`/lifecycle/pipeline-stages`),

  // Bulk operations
  bulkUpdateStatus: (body: { brand_ids: number[]; status: string }) =>
    call<{ updated: number; brand_ids: number[] }>(`/brands/bulk/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  bulkEnrich: (body: { brand_ids: number[] }) =>
    call<{ results: { brand_id: number; status: string; workflow_id?: string }[] }>(`/brands/bulk/enrich`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Export
  exportCsv: (opts: { tier?: string; status?: string; score_min?: number; score_max?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.tier) p.set("tier", opts.tier);
    if (opts.status) p.set("status", opts.status);
    if (opts.score_min != null) p.set("score_min", String(opts.score_min));
    if (opts.score_max != null) p.set("score_max", String(opts.score_max));
    return `${getBase()}/brands/export/csv?${p}`;
  },

  // Similar brands
  findSimilar: (brandId: number) =>
    call<{ items: Brand[] }>(`/similar/brand/${brandId}`),
};
