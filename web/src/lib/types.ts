export type Brand = {
  id: number;
  name: string;
  domain: string | null;
  status: string;
  created_at: string;
  tier?: string | null;
  score?: number | null;
  score_headline?: string | null;
  diagnosis?: string | null;
  gap_summary?: string | null;
};

export type Tier = "hot" | "warm" | "watchlist" | "park";

export type DossierCompany = {
  hq?: string | null;
  geos?: string[];
  domain?: string | null;
  brand_name?: string | null;
  legal_name?: string | null;
  founded_year?: number | null;
  revenue_band?: string | null;
  employees_est?: number | null;
  funding_stage?: string | null;
  public_listed?: boolean | null;
  revenue_estimate_inr?: number | null;
};

export type DossierPositioning = {
  audience?: string | null;
  category?: string | null;
  price_band?: string | null;
  USP_summary?: string | null;
  sub_category?: string | null;
  audience_segments?: string[];
};

export type FundingRound = {
  round?: string | null;
  amount?: string | null;
  date?: string | null;
  investors?: string[];
};

export type DigitalFootprint = {
  seo_signal?: string | null;
  paid_signal?: string | null;
  email_signal?: string | null;
  social_signal?: string | null;
  web_perf_signal?: string | null;
  content_maturity?: string | null;
  programmatic_signal?: string | null;
  martech_stack?: string[];
  channels_active?: string[];
};

export type DossierResearch = {
  company?: DossierCompany;
  positioning?: DossierPositioning;
  funding_history?: FundingRound[];
  digital_footprint?: DigitalFootprint;
};

export type Competitor = {
  name?: string;
  domain?: string;
  strengths?: string;
  positioning?: string;
  threat_level?: string;
};

export type DossierCompetitor = {
  competitors?: Competitor[];
  gap_map?: Record<string, string>;
};

export type ServiceRecommendation = {
  service?: string;
  rationale?: string;
  confidence?: string | number;
  estimated_impact?: string;
};

export type DossierOpportunity = {
  diagnosis?: string;
  urgency_factors?: string[];
  top_3_weaknesses?: string[];
  services_recommended?: ServiceRecommendation[];
  estimated_deal_size_inr?: number | string;
};

export type ScoreBreakdown = {
  score?: number;
  evidence?: string;
};

export type ServiceGap = {
  score?: number;
  evidence?: string;
  priority?: string;
};

export type DossierScore = {
  tier?: string;
  total?: number;
  breakdown?: Record<string, ScoreBreakdown>;
  service_gaps?: Record<string, ServiceGap>;
  top_services?: string[];
  why?: string[];
  red_flags?: string[];
  confidence?: number;
  confidence_explanation?: string;
  estimated_deal_value_inr?: number;
  predicted_conversion_probability?: number;
};

export type DossierOutreach = {
  subjects?: string[];
  bodies?: string[];
  linkedin_inmail?: string;
  voicemail_script?: string;
  skipped?: boolean;
};

export type DossierData = {
  reason?: string;
  research?: DossierResearch;
  competitor?: DossierCompetitor;
  opportunity?: DossierOpportunity;
  score?: DossierScore;
  outreach?: DossierOutreach;
  tool_data_keys?: string[];
  signal_evidence?: SignalEvidence;
};

export type Dossier = {
  id: number;
  brand_id: number;
  version: number;
  cost_cents: number;
  generated_at: string;
  data: DossierData;
};

export type AgentTrace = {
  id: string;
  workflow_id: string;
  brand_id: number | null;
  agent: string;
  total_cost_cents: number;
  duration_ms: number | null;
  status: string;
  created_at: string;
};

export type CollectorFinding = {
  type: string;
  value_num: number | null;
  value_text: string | null;
  payload?: Record<string, unknown>;
  observed_at: string | null;
};

export type SignalEvidence = Record<string, CollectorFinding[]>;

export type Signal = {
  id: number;
  brand_id: number | null;
  brand_hint: string | null;
  type: string;
  value_num: number | null;
  value_text: string | null;
  payload?: Record<string, unknown>;
  source: string;
  observed_at: string;
  ingested_at: string;
};

export type Approval = {
  id: number;
  entity_type: string;
  entity_id: number;
  brand_id: number | null;
  status: string;
  reviewer: string | null;
  notes: string | null;
  created_at: string;
  decided_at: string | null;
};

export type Person = {
  id: number;
  brand_id: number;
  name: string;
  title: string | null;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  source: string;
  confidence: number;
  verified: boolean;
  created_at: string;
  updated_at: string;
  brand_name?: string;
  brand_domain?: string;
};

export type Notification = {
  id: number;
  type: string;
  title: string;
  message: string;
  brand_id: number | null;
  entity_type: string | null;
  entity_id: number | null;
  read: boolean;
  created_at: string;
  brand_name?: string;
};

export type LeadEvent = {
  id: number;
  brand_id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  note: string | null;
  actor: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type PipelineStage = {
  count: number;
  total_value: number;
};

export type PipelineStages = {
  stages: Record<string, PipelineStage>;
  valid_transitions: Record<string, string[]>;
};
