"use client";

import { api } from "../api";

export type PanelRoleType = "technical" | "hr" | "ceo";
export type CalendarProvider = "microsoft" | "google" | "none";

export interface PanelMember {
  id: string;
  name: string;
  email: string;
  role_type: PanelRoleType;
  job_title: string | null;
  timezone: string;
  calendar_provider: CalendarProvider;
  calendar_id: string | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PanelMemberCreate {
  name: string;
  email: string;
  role_type: PanelRoleType;
  job_title?: string | null;
  timezone?: string;
  calendar_provider?: CalendarProvider;
  calendar_id?: string | null;
  notes?: string | null;
}

export type PanelMemberUpdate = Partial<PanelMemberCreate> & {
  is_active?: boolean;
};

export const panels = {
  list(params: { role_type?: PanelRoleType; include_inactive?: boolean } = {}) {
    const qs = new URLSearchParams();
    if (params.role_type) qs.set("role_type", params.role_type);
    if (params.include_inactive) qs.set("include_inactive", "true");
    const q = qs.toString();
    return api.get<PanelMember[]>(`/dashboard/panels${q ? `?${q}` : ""}`);
  },
  create(body: PanelMemberCreate) {
    return api.post<PanelMember>("/dashboard/panels", body);
  },
  update(id: string, body: PanelMemberUpdate) {
    return api.patch<PanelMember>(`/dashboard/panels/${id}`, body);
  },
  remove(id: string) {
    return api.del<void>(`/dashboard/panels/${id}`);
  },
};
