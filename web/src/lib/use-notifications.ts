"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Notification = {
  id: number;
  type: string;
  title: string;
  message: string;
  brand_id: number | null;
  brand_name?: string | null;
  entity_type?: string | null;
  entity_id?: number | null;
  read: boolean;
  created_at: string;
};

type NotificationListResponse = {
  items: Notification[];
  unread_count: number;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

async function fetchNotifications(): Promise<NotificationListResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/notification-center`, {
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as NotificationListResponse;
  } catch {
    return null;
  }
}

async function postApi(path: string, body: Record<string, unknown> = {}) {
  try {
    await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {}
}

export function useNotifications() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    const data = await fetchNotifications();
    if (data && mountedRef.current) {
      setNotifications(data.items);
      setUnreadCount(data.unread_count);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    load();
    const interval = setInterval(load, 30_000);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [load]);

  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource(`/api/stream/notifications`);
      const refetch = () => load();
      es.addEventListener("approval", refetch);
      es.addEventListener("hot_lead", refetch);
      es.addEventListener("discovery", refetch);
      es.addEventListener("reply", refetch);
      es.onerror = () => es?.close();
    } catch {}
    return () => es?.close();
  }, [load]);

  const markRead = useCallback(async (id: number) => {
    await postApi(`/notification-center/read/${id}`, {});
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
    setUnreadCount((prev) => Math.max(0, prev - 1));
  }, []);

  const markAllRead = useCallback(async () => {
    await postApi("/notification-center/read-all", {});
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnreadCount(0);
  }, []);

  return { notifications, unreadCount, loading, reload: load, markRead, markAllRead };
}

export type { Notification };
