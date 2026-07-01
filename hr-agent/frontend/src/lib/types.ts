// Mirrors the backend Pydantic models (src/api/dashboard.py + src/api/roles.py).

export type FitTier = "green" | "amber" | "red"; // amber kept for backward compat with existing data

/** Dynamic, role-specific criterion from evaluation_spec (new shape). */
export interface CriterionScore {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  rationale: string;
  evidence: string[];
  data_status: "verified" | "pending_verification";
}

/** Fixed fallback dimension (legacy roles without evaluation_spec). */
export interface DimensionScore {
  score: number | null;
  rationale: string;
  evidence: string[];
  data_status: "verified" | "pending_verification";
}

/** Fit assessment payload persisted on the application. */
export interface FitAssessment {
  overall_score: number | null;
  deterministic_tier: string | null;
  llm_tier?: string | null;
  tier_agreement?: boolean | null;
  summary: string | null;
  scoring_pass?: string | null;
  green_flags: string[];
  red_flags: string[];
  knock_outs?: string[];
  pending_verification: string[];
  weights_used?: Record<string, number>;
  /** Dynamic, role-specific criteria — the only scored structure going forward. Always non-empty for new scores. */
  criteria_scores: CriterionScore[];
  /** Legacy fixed fallback — only present on rows scored before the dynamic-spec rewrite. */
  dimensions?: {
    skills_match?: DimensionScore;
    experience_level?: DimensionScore;
  };
}

export type MailSource = "gmail" | "outlook" | "imap_gmail" | "imap_outlook" | "generic" | string;

export interface MailInboxItem {
  application_id: string;
  candidate_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  role_title: string | null;
  status: string;
  mail_source: MailSource | null;
  subject: string | null;
  received_at: string | null;
  created_at: string;
}

export interface ShortlistItem {
  application_id: string;
  candidate_id: string;
  candidate_name: string | null;
  role_title: string | null;
  fit_score: number | null;
  fit_tier: FitTier | null;
  screening_score: number | null;
  status: string;
  created_at: string;
}

export interface SchedulingItem {
  interview_id: string;
  application_id: string;
  candidate_name: string | null;
  role_title: string | null;
  scheduled_at: string | null;
  meeting_link: string | null;
  status: string;
}

export interface AuditItem {
  id: number;
  candidate_id: string | null;
  application_id: string | null;
  action: string;
  actor: string;
  details: Record<string, unknown> | null;
  model_version: string | null;
  prompt_version: string | null;
  langfuse_trace_id: string | null;
  created_at: string;
}

export interface ScreeningAnswerItem {
  question_id: string;
  question_text: string | null;
  question_type: string | null;
  answer: unknown;
  score: number | null;
  max_score: number | null;
  rationale: string | null;
}

export interface ScreeningResponseView {
  application_id: string;
  role_title: string | null;
  submitted_at: string | null;
  composite_score: number | null;
  knock_out_triggered: boolean;
  knock_out_reason: string | null;
  items: ScreeningAnswerItem[];
}

export interface FitFactorItem {
  factor: string;
  score: number | null;
  weight: number | null;
  rationale: string | null;
  evidence?: string[];
}

export interface FitReportExtras {
  overall_score: number | null;
  summary: string | null;
  red_flags: string[];
  green_flags: string[];
  deterministic_tier: string | null;
  llm_tier: string | null;
  tier_agreement: boolean | null;
}

export interface CandidateView {
  candidate_id: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  status: string;
  source_channel: string | null;
  applications: ShortlistItem[];
  latest_profile: Record<string, unknown> | null;
  recent_audit: AuditItem[];
  screening_responses?: ScreeningResponseView[];
  fit_factors?: FitFactorItem[];
  fit_report?: FitReportExtras | null;
  resume_download_url?: string | null;
  resume_filename?: string | null;
}

export interface FunnelCounts {
  since_days: number;
  role_id: string | null;
  counts: Record<string, number>;
}

export interface OverrideMetrics {
  overrides: number;
  decisions: number;
  rate: number;
  since_days: number;
}

export interface ChannelStat {
  ok: number;
  fail: number;
  total: number;
  success_rate: number;
}

export interface ChannelStats {
  email: ChannelStat;
  whatsapp: ChannelStat;
  sms: ChannelStat;
}

export interface PipelineSnapshot {
  total_candidates: number;
  candidates_by_status: Record<string, number>;
  applications_by_tier: Record<string, number>;
}

export interface Role {
  id: string;
  title: string;
  jd_text: string;
  screening_questions: ScreeningQuestion[];
  scoring_rubric: Record<string, unknown>;
  cut_line: number;
  interviewer_panel: InterviewerPanelMember[];
  status: "open" | "paused" | "filled" | "cancelled";
  ctc_min_lpa: number | null;
  ctc_max_lpa: number | null;
  max_notice_days: number | null;
  location: string | null;
  remote_policy: "onsite" | "hybrid" | "remote" | null;
  assignment_brief: string | null;
  assignment_instructions: string | null;
  assignment_deadline_days: number;
  assignment_problem_doc_filename?: string | null;
  has_problem_doc?: boolean;
  pi_cognitive_link?: string | null;
  pi_personality_link?: string | null;
  pipeline_template: string[] | null;
  /** Real pipeline source of truth (role_pipeline_stages), ordered by position. */
  pipeline?: RolePipelineStageEntry[];
  screening_modality: string;
  evaluation_spec?: Record<string, unknown> | null;
  company_context?: Record<string, unknown> | null;
  created_at: string;
}

export interface RolePipelineStageEntry {
  stage_key: string;
  stage_type: string;
  label: string;
  position: number;
  mode: string;
  is_enabled: boolean;
}

export interface ScreeningQuestion {
  id: string;
  question: string;
  type: "ctc_check" | "notice_period_check" | "must_have_skill" | "scored" | "open_text" | "location_check";
  weight: number;
  knock_out_value?: string | null;
  rubric_description?: string | null;
  max_score: number;
  options?: string[] | null;
  required?: boolean;
}

export interface InterviewerPanelMember {
  user_id: string;
  calendar_email: string;
  role_in_panel: "hiring_manager" | "peer" | "cross_functional";
}

export interface UiSettings {
  app_env: string;
  app_base_url: string;
  llm_provider: "openai" | "anthropic";
  llm_model_fast: string;
  llm_model_smart: string;
  screening_url_ttl_days: number;
  data_retention_days_default: number;
  data_retention_days_talent_pool: number;
  dpo_contact_email: string;
  auto_approve_green_tier: boolean;
  pi_tests_enabled: boolean;
  enable_whatsapp: boolean;
  enable_sms_reminders: boolean;
  channel_configured: Record<string, boolean>;
  pipeline_readiness?: {
    email_provider: string;
    meeting_bot_provider: string;
    ready_for_prod_email: boolean;
    ready_for_meeting_capture: boolean;
    ready_for_calendar_scheduling: boolean;
  };
  llm_limits: {
    daily_call_limit: number;
    daily_token_limit: number;
    per_candidate_monthly_limit: number;
    rate_limit_per_minute: number;
    daily_budget_usd: number;
  };
  llm_usage_today: {
    calls_last_minute: number;
    calls_today: number;
    tokens_today: number;
    usd_spent_today: number;
  };
}

export type OverrideDecision = "approve" | "reject" | "force_shortlist" | "change_role" | "withdraw";

export interface OverridePayload {
  decision: OverrideDecision;
  reason: string;
  hr_email: string;
  new_role_id?: string | null;
  new_fit_tier?: FitTier | null;
}

export interface Org {
  id: string;
  name: string;
  slug: string;
  hiring_persona: Record<string, unknown>;
  settings: Record<string, unknown>;
}

export interface StageViewEntry {
  stage_key: string;
  stage_type: string;
  label: string;
  position: number;
  mode: string;
  is_enabled: boolean;
  is_current: boolean;
  processing_status: string;
  verdict: string;
  result_ref: unknown;
}

export interface CandidateListItem {
  application_id: string;
  candidate_id: string;
  name: string | null;
  email: string | null;
  role_title: string | null;
  current_stage: string;
  current_stage_key: string;
  screening_score: number | null;
  fit_score: number | null;
  fit_tier: string | null;
  created_at: string;
  updated_at: string;
}
