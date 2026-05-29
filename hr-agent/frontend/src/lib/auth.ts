"use client";

const KEY = "hiring_agent_dashboard_key";

export function getDashboardKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function setDashboardKey(value: string): void {
  window.localStorage.setItem(KEY, value);
}

export function clearDashboardKey(): void {
  window.localStorage.removeItem(KEY);
}
