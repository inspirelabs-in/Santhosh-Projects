"use client";

import { api } from "./api";

export type FieldType =
  | "string"
  | "secret"
  | "int"
  | "float"
  | "bool"
  | "select"
  | "json"
  | "url"
  | "email"
  | "textarea";

export interface ConfigFieldDef {
  key: string;
  settings_attr: string;
  group: string;
  subgroup: string;
  label: string;
  help: string;
  type: FieldType;
  default: unknown;
  options: string[] | null;
  min: number | null;
  max: number | null;
  is_secret: boolean;
  hot_reload: boolean;
  test_integration: string | null;
  advanced: boolean;
}

export interface ConfigSchema {
  groups: { name: string; fields: ConfigFieldDef[] }[];
}

export interface GroupValues {
  group: string;
  fields: { key: string; value: unknown; configured: boolean }[];
}

export interface AllValues {
  [group: string]: {
    [key: string]: { value: unknown; configured: boolean; is_secret: boolean };
  };
}

export interface ProbeResult {
  ok: boolean;
  message: string;
  detail: Record<string, unknown>;
}

export interface AuditRow {
  id: number;
  key: string;
  old_value: unknown;
  new_value: unknown;
  actor: string;
  actor_role: string;
  action: string;
  ip: string | null;
  created_at: string | null;
}

export interface SetupStatus {
  completed: boolean;
  missing_groups: string[];
  has_llm_key: boolean;
  has_email: boolean;
}

export const configApi = {
  schema: () => api.get<ConfigSchema>("/admin/config/schema"),
  values: () => api.get<AllValues>("/admin/config/values"),
  group: (group: string) =>
    api.get<GroupValues>(`/admin/config/${encodeURIComponent(group)}`),
  patch: (updates: Record<string, unknown>) =>
    api.patch<{ updated: Record<string, unknown> }>("/admin/config", { updates }),
  test: (integration: string, candidate: Record<string, unknown>) =>
    api.post<ProbeResult>(`/admin/config/test/${integration}`, { config: candidate }),
  audit: (key?: string, limit = 100) =>
    api.get<{ rows: AuditRow[] }>(
      `/admin/config/audit/log?limit=${limit}${key ? `&key=${encodeURIComponent(key)}` : ""}`,
    ),
  resetField: (key: string) =>
    api.del<{ deleted: boolean; key: string }>(`/admin/config/${encodeURIComponent(key)}`),
  restartWorkers: () => api.post<{ ok: boolean }>("/admin/config/restart-workers"),
  setupStatus: () => api.get<SetupStatus>("/admin/config/setup-status"),
};
