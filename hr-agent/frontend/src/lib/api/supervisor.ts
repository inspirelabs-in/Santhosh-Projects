"use client";

import { api } from "../api";

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

export interface EngagementData {
  application_id: string;
  overall: number;
  response_speed: number;
  completion_rate: number;
  scheduling_flexibility: number;
  signals: string[];
  risk: string;
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
