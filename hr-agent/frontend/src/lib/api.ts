"use client";

import { getDashboardKey, clearDashboardKey } from "./auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = getDashboardKey();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (key) headers.set("X-Dashboard-Key", key);

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (res.status === 401) {
    clearDashboardKey();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError("Unauthorized", 401, null);
  }

  if (res.status === 429) {
    throw new ApiError(
      "Too many requests — please wait a moment and try again.",
      429,
      null,
    );
  }

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(
      (detail as any)?.detail ?? `Request failed: ${res.status}`,
      res.status,
      detail,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// SWR-compatible fetcher -- uses the same auth + error handling.
// Silently returns undefined for 5xx so refreshInterval polling doesn't spam console.
export const swrFetcher = async <T>(path: string): Promise<T> => {
  try {
    return await api.get<T>(path);
  } catch (err) {
    if (err instanceof ApiError && err.status >= 500) {
      return undefined as T;
    }
    throw err;
  }
};

export async function verifyKey(key: string): Promise<{ role: string } | null> {
  try {
    const res = await fetch(`${BASE}/dashboard/settings/whoami`, {
      headers: { "X-Dashboard-Key": key },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as { role: string };
  } catch {
    return null;
  }
}
