"use client";

/**
 * Typed clients for the agentic-V2 backend resources.
 *
 * Mirrors:
 *   POST /agentic/voice-screen/dispatch
 *   POST /agentic/assessment/dispatch
 *   POST /agentic/meeting/dispatch
 *   GET  /dashboard/ceo/applications
 *   GET  /dashboard/ceo/applications/{id}
 *   POST /dashboard/ceo/applications/{id}/regenerate-brief
 *
 * All calls flow through the shared `api` wrapper so X-Dashboard-Key auth
 * + 401 redirect behaviour stay centralised.
 */

import { api } from "../api";

// ---------------------------------------------------------------------------
// Voice screen
// ---------------------------------------------------------------------------

export interface DispatchVoiceScreenBody {
  application_id: string;
  scheduled_at?: string | null;
}

export type CallKind =
  | "screening"
  | "confirmation"
  | "meeting_schedule"
  | "status_update"
  | "joining_details"
  | "general_query";

export const CALL_KIND_LABELS: Record<CallKind, string> = {
  screening: "Screening",
  confirmation: "Confirmation",
  meeting_schedule: "Meeting schedule",
  status_update: "Status update",
  joining_details: "Joining details",
  general_query: "General query",
};

export interface VoiceCallListItem {
  voice_call_id: string;
  application_id: string;
  candidate_name: string | null;
  role_title: string | null;
  candidate_phone: string | null;
  status: string;
  call_kind: CallKind;
  overall_score: number | null;
  verdict: string | null;
  duration_sec: number | null;
  scheduled_at: string | null;
  callback_at: string | null;
  callback_reason: string | null;
  candidate_response: string | null;
  next_action: string | null;
  error: string | null;
  attempt_no: number;
  recording_url: string | null;
  transcript_url: string | null;
  created_at: string;
  ended_at: string | null;
}

export const voiceCalls = {
  dispatch(body: DispatchVoiceScreenBody) {
    return api.post<{ ok: boolean; application_id: string }>(
      "/agentic/voice-screen/dispatch",
      body,
    );
  },
  dispatchCall(body: { application_id: string; call_kind: CallKind }) {
    return api.post<{ ok: boolean; application_id: string }>(
      "/agentic/voice-call/dispatch",
      body,
    );
  },
  list(params: { status?: string; call_kind?: string; limit?: number; offset?: number } = {}) {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.call_kind) qs.set("call_kind", params.call_kind);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return api.get<VoiceCallListItem[]>(`/agentic/voice-calls${q ? `?${q}` : ""}`);
  },
  transcript(voiceCallId: string) {
    return api.get<{ transcript_text: string | null; answers: Array<{ question_id?: string; question: string; answer_transcript: string; duration_sec?: number | null }> }>(
      `/agentic/voice-calls/${voiceCallId}/transcript`,
    );
  },
  recordingUrl(voiceCallId: string) {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
    return `${base}/agentic/voice-calls/${voiceCallId}/recording`;
  },
};

// ---------------------------------------------------------------------------
// Voice campaigns
// ---------------------------------------------------------------------------

export interface CampaignResponse {
  id: string;
  status: string;
  call_kind: string;
  name: string;
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
}

export interface CampaignDetailResponse extends CampaignResponse {
  progress: Record<string, number>;
  max_concurrent: number;
}

export interface CreateCampaignBody {
  call_kind: CallKind;
  application_ids: string[];
  name: string;
  role_id?: string;
  max_concurrent?: number;
  dispatch_rate_per_minute?: number;
}

export const campaigns = {
  create(body: CreateCampaignBody) {
    return api.post<CampaignResponse>("/agentic/voice-campaigns", body);
  },
  start(campaignId: string) {
    return api.post<CampaignResponse>(`/agentic/voice-campaigns/${campaignId}/start`);
  },
  get(campaignId: string) {
    return api.get<CampaignDetailResponse>(`/agentic/voice-campaigns/${campaignId}`);
  },
  pause(campaignId: string) {
    return api.post<CampaignResponse>(`/agentic/voice-campaigns/${campaignId}/pause`);
  },
  cancel(campaignId: string) {
    return api.post<CampaignResponse>(`/agentic/voice-campaigns/${campaignId}/cancel`);
  },
};

// ---------------------------------------------------------------------------
// Assessment
// ---------------------------------------------------------------------------

export type AssessmentKind = "behavioral" | "cognitive";

export interface DispatchAssessmentBody {
  application_id: string;
  kinds?: AssessmentKind[];
}

export interface AssessmentListItem {
  assessment_id: string;
  application_id: string;
  candidate_name: string | null;
  role_title: string | null;
  provider: string;
  kind: string | null;
  status: string;
  fit_band: "green" | "amber" | "red" | null;
  normalized_score: number | null;
  percentile: number | null;
  invite_sent_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export const assessments = {
  dispatch(body: DispatchAssessmentBody) {
    return api.post<{ ok: boolean; application_id: string }>(
      "/agentic/assessment/dispatch",
      body,
    );
  },
  list(
    params: {
      status?: string;
      provider?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.provider) qs.set("provider", params.provider);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return api.get<AssessmentListItem[]>(`/agentic/assessments${q ? `?${q}` : ""}`);
  },
};

// ---------------------------------------------------------------------------
// Meeting bot
// ---------------------------------------------------------------------------

export type MeetingRound = "technical" | "ceo";

export interface DispatchMeetingBody {
  application_id: string;
  round: MeetingRound;
  teams_join_url: string;
  scheduled_at: string; // ISO datetime
  interview_id?: string | null;
}

export interface MeetingListItem {
  meeting_session_id: string;
  application_id: string;
  candidate_name: string | null;
  role_title: string | null;
  round: MeetingRound;
  bot_status: string;
  overall_score: number | null;
  verdict: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  duration_sec: number | null;
  transcript_url: string | null;
  created_at: string;
}

export interface AISchedulingBody {
  application_id: string;
  round: "technical" | "ceo" | "hr";
}

export const meetings = {
  dispatch(body: DispatchMeetingBody) {
    return api.post<{ ok: boolean; meeting_session_id: string }>(
      "/agentic/meeting/dispatch",
      body,
    );
  },
  aiSchedule(body: AISchedulingBody) {
    return api.post<{ ok: boolean; application_id: string; round: string }>(
      "/agentic/meeting/ai-schedule",
      body,
    );
  },
  list(
    params: {
      round?: MeetingRound;
      bot_status?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) {
    const qs = new URLSearchParams();
    if (params.round) qs.set("round", params.round);
    if (params.bot_status) qs.set("bot_status", params.bot_status);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return api.get<MeetingListItem[]>(`/agentic/meetings${q ? `?${q}` : ""}`);
  },
  transcript(meetingSessionId: string) {
    return api.get<MeetingTranscript>(
      `/agentic/meetings/${meetingSessionId}/transcript`,
    );
  },
};

export interface TranscriptBlock {
  speaker?: string | { name?: string } | null;
  text?: string | null;
  words?: string | null;
  transcript?: string | null;
  t_start?: number | null;
  start_time?: number | null;
}

export interface MeetingTranscript {
  meeting_session_id: string;
  round: string;
  verdict: string | null;
  scores: {
    technical: number | null;
    communication: number | null;
    confidence: number | null;
    overall: number | null;
  };
  llm_report: string | null;
  transcript: TranscriptBlock[];
}

// ---------------------------------------------------------------------------
// CEO dashboard
// ---------------------------------------------------------------------------

export interface CEOListItem {
  application_id: string;
  candidate_id: string;
  candidate_name: string | null;
  role_title: string | null;
  current_stage: string;
  fit_score: number | null;
  has_brief: boolean;
  updated_at: string;
}

export interface VoiceSummary {
  voice_call_id: string;
  status: string;
  overall_score: number | null;
  verdict: string | null;
  duration_sec: number | null;
  recording_url: string | null;
  transcript_url: string | null;
  transcript_text: string | null;
  answers: Array<Record<string, unknown>> | null;
  scheduled_at: string | null;
  callback_at: string | null;
  callback_reason: string | null;
  attempt_no: number;
  error: string | null;
  candidate_response: string | null;
  next_action: string | null;
}

export interface AssessmentSummary {
  assessment_id: string;
  provider: string;
  kind: string | null;
  status: string;
  fit_band: "green" | "amber" | "red" | null;
  normalized_score: number | null;
  percentile: number | null;
}

export interface MeetingSummary {
  meeting_session_id: string;
  round: MeetingRound;
  bot_status: string;
  overall_score: number | null;
  technical_score: number | null;
  communication_score: number | null;
  confidence_score: number | null;
  verdict: string | null;
  transcript_url: string | null;
  recording_url: string | null;
  summary: string | null;
  emotion_timeline: Array<{
    t_start_sec: number;
    t_end_sec: number;
    speaker: string;
    emotion: string;
    confidence: number;
  }> | null;
}

export interface CEODetail {
  application_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  role_title: string | null;
  current_stage: string;
  fit_score: number | null;
  fit_tier: string | null;
  screening_evaluation: Record<string, unknown> | null;
  voice_calls: VoiceSummary[];
  assessments: AssessmentSummary[];
  meetings: MeetingSummary[];
  journey_report_markdown: string | null;
}

// ---------------------------------------------------------------------------
// Conversational role drafting + LinkedIn post
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface RoleDraftEnvelope {
  message: string;
  draft: Record<string, any>;
  missing: string[];
  quick_replies?: string[];
  ready_to_save: boolean;
}

export interface RoleDefaults {
  common_remote_policy: string | null;
  common_locations: string[];
  common_ctc_min: number | null;
  common_ctc_max: number | null;
}

export interface LinkedInPost {
  post_text: string;
  hashtags: string[];
  headline: string;
}

export const roleChat = {
  defaults() {
    return api.get<RoleDefaults>("/agentic/roles/defaults");
  },
  send(body: {
    user_message: string;
    history: ChatMessage[];
    draft: Record<string, any>;
    memory_override?: Record<string, any> | null;
  }) {
    return api.post<RoleDraftEnvelope>("/agentic/roles/chat", body);
  },
  rewriteSection(body: {
    section: string;
    current_body: string;
    role_context?: Record<string, any>;
    user_directive?: string | null;
  }) {
    return api.post<{ body: string }>("/agentic/roles/section-rewrite", body);
  },
  linkedinPost(body: {
    title: string;
    jd_text: string;
    location?: string | null;
    remote_policy?: string | null;
    ctc_min_lpa?: number | null;
    ctc_max_lpa?: number | null;
    apply_url?: string | null;
  }) {
    return api.post<LinkedInPost>("/agentic/roles/linkedin-post", body);
  },
};

export const ceoDashboard = {
  list() {
    return api.get<CEOListItem[]>("/dashboard/ceo/applications");
  },
  detail(applicationId: string) {
    return api.get<CEODetail>(`/dashboard/ceo/applications/${applicationId}`);
  },
  regenerateBrief(applicationId: string) {
    return api.post<{ ok: boolean }>(
      `/dashboard/ceo/applications/${applicationId}/regenerate-brief`,
    );
  },
};

export const hrDashboard = {
  list() {
    return api.get<CEOListItem[]>("/dashboard/hr/applications");
  },
  detail(applicationId: string) {
    return api.get<CEODetail>(`/dashboard/hr/applications/${applicationId}`);
  },
};
