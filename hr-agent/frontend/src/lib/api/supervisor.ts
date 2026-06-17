"use client";

import { api } from "../api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SupervisorAction {
  id: string;
  event_id: string;
  application_id: string | null;
  action_type: string;
  reasoning: string | null;
  confidence: number | null;
  mode: string;
  executed: boolean;
  execution_result: Record<string, unknown> | null;
  approved_by: string | null;
  rejected_by: string | null;
  created_at: string;
}

export interface ActionListResponse {
  actions: SupervisorAction[];
  total: number;
}

export interface SupervisorEvent {
  id: string;
  event_type: string;
  application_id: string | null;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface EventListResponse {
  events: SupervisorEvent[];
  total: number;
}

export interface AccuracyReport {
  total_proposals: number;
  approved: number;
  rejected: number;
  pending: number;
  executed: number;
  approval_rate: number | null;
}

// ---------------------------------------------------------------------------
// Evidence & Confidence
// ---------------------------------------------------------------------------

export interface EvidenceItem {
  id: string;
  fact_key: string;
  fact_value: unknown;
  source_stage: string | null;
  source_type: string | null;
  extraction_method: string | null;
  confidence: number | null;
  evidence_text: string | null;
  created_at: string;
}

export interface DecisionItem {
  id: string;
  decision_type: string;
  outcome: string;
  outcome_value: Record<string, unknown>;
  evidence_ids: string[];
  policy_rule_ids: string[];
  created_at: string;
}

export interface ContradictionItem {
  id: string;
  fact_key: string | null;
  old_value: unknown;
  new_value: unknown;
  old_source_stage: string | null;
  new_source_stage: string | null;
  created_at: string;
}

export interface EvidenceChainResponse {
  application_id: string;
  evidence: EvidenceItem[];
  decisions: DecisionItem[];
  contradictions: ContradictionItem[];
}

export interface StageConfidence {
  stage: string;
  evidence_count: number;
  avg_confidence: number;
  contradiction_count: number;
  consistency_ratio: number;
}

export interface PipelineConfidenceResponse {
  application_id: string;
  overall: number;
  recommendation: string;
  supervisor_accuracy: number | null;
  per_stage: StageConfidence[];
  details: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const supervisorApi = {
  actions(params: {
    status?: string;
    application_id?: string;
    limit?: number;
    offset?: number;
  } = {}) {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.application_id) qs.set("application_id", params.application_id);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return api.get<ActionListResponse>(`/supervisor/actions${q ? `?${q}` : ""}`);
  },

  approve(actionId: string, note?: string) {
    return api.post<{ status: string; executed: boolean; execution_result: unknown }>(
      `/supervisor/actions/${actionId}/approve`,
      { note: note ?? null },
    );
  },

  reject(actionId: string, note?: string) {
    return api.post<{ status: string }>(
      `/supervisor/actions/${actionId}/reject`,
      { note: note ?? null },
    );
  },

  events(params: { status?: string; limit?: number } = {}) {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return api.get<EventListResponse>(`/supervisor/events${q ? `?${q}` : ""}`);
  },

  accuracyReport() {
    return api.get<AccuracyReport>("/supervisor/accuracy-report");
  },
};

export interface ExperimentSummary {
  name: string;
  status: string;
  description: string;
  variant_count: number;
  sample_rate: number;
  target_stage: string | null;
}

export interface EngagementData {
  application_id: string;
  overall: number;
  response_speed: number;
  completion_rate: number;
  scheduling_flexibility: number;
  signals: string[];
  risk: string;
}

export interface PipelineHealthAlert {
  id: string;
  alert_type: string;
  stage: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export const evidenceApi = {
  chain(applicationId: string) {
    return api.get<EvidenceChainResponse>(
      `/dashboard/v1/applications/${applicationId}/evidence-chain`,
    );
  },

  confidence(applicationId: string) {
    return api.get<PipelineConfidenceResponse>(
      `/dashboard/v1/applications/${applicationId}/confidence`,
    );
  },

  engagement(applicationId: string) {
    return api.get<EngagementData>(
      `/dashboard/v1/applications/${applicationId}/engagement`,
    );
  },

  interviewIntelligence(applicationId: string) {
    return api.get<{
      application_id: string;
      interviews_with_feedback: number;
      total_discrepancies: number;
      discrepancies: Array<{
        type: string;
        description: string;
        evidence_id: string | null;
        round: string | null;
      }>;
    }>(`/dashboard/v1/applications/${applicationId}/interview-intelligence`);
  },
};

export const experimentApi = {
  list() {
    return api.get<ExperimentSummary[]>("/supervisor/experiments");
  },

  results(name: string) {
    return api.get<Record<string, unknown>>(`/supervisor/experiments/${name}/results`);
  },

  create(data: {
    name: string;
    description: string;
    variants: Array<{ name: string; weight: number; config: Record<string, unknown> }>;
    target_stage?: string;
    sample_rate?: number;
  }) {
    return api.post<{ ok: boolean; experiment: string; id: string }>(
      "/supervisor/experiments",
      data,
    );
  },

  conclude(name: string, notes?: string) {
    return api.post<{ ok: boolean }>(`/supervisor/experiments/${name}/conclude`, {
      notes: notes ?? "",
    });
  },
};
