# Data Architecture

## 1. Storage Strategy

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Primary DB | PostgreSQL 16 + pgvector | All durable state (29 tables) |
| Cache/Queue | Redis 7 | Ephemeral state, rate limits, pub/sub, job queue |
| Object Storage | Cloudflare R2 (S3-compatible) | Resume PDFs, voice recordings, meeting transcripts |
| Email | Microsoft Graph / Resend / SMTP | Outbound email with fallback chain |

**Key principle:** Postgres is the source of truth. Redis holds ephemeral data that can be lost on restart. R2 stores binary blobs referenced by key in Postgres.

---

## 2. Database Schema (29 Tables)

### 2.1 Core Entities

#### `candidates`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | `gen_random_uuid()` |
| email | VARCHAR(255) UNIQUE | Lowercase unique index |
| phone | VARCHAR(20) INDEX | |
| name | VARCHAR(255) | |
| linkedin_url | VARCHAR(500) INDEX | |
| status | VARCHAR(50) INDEX | Default `intake` |
| source_channel | VARCHAR(50) | `form`, `email`, `referral`, `linkedin`, `naukri` |
| timezone | VARCHAR(64) | |
| consent_captured_at | TIMESTAMPTZ | |
| consent_text_shown | TEXT | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | Auto-update |

#### `roles`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| title | VARCHAR(255) | |
| jd_text | TEXT | |
| screening_questions | JSONB | `GeneratedQuestion[]` |
| scoring_rubric | JSONB | |
| cut_line | INTEGER | Default 60 |
| interviewer_panel | JSONB | |
| status | VARCHAR(20) INDEX | `open`, `paused`, `filled`, `cancelled` |
| ctc_min_lpa / ctc_max_lpa | FLOAT | |
| max_notice_days | INTEGER | |
| location | VARCHAR(255) | |
| remote_policy | VARCHAR(20) | `onsite`, `hybrid`, `remote` |
| assignment_brief / _instructions / _deadline_days / _problem_doc_key / _problem_filename | Various | Take-home assignment config |
| pi_cognitive_link | TEXT | PI cognitive assessment link |
| pi_personality_link | TEXT | PI personality assessment link |
| screening_modality | VARCHAR(16) INDEX | Always `voice` |
| pipeline_template | JSONB | Ordered stage list |

#### `applications`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | **Central entity** — everything FKs to this |
| candidate_id | UUID FK → candidates.id CASCADE | INDEX |
| role_id | UUID FK → roles.id SET NULL | INDEX |
| status | VARCHAR(50) INDEX | `active`, `needs_hr_review`, `rejected`, etc. |
| fit_score | INTEGER | |
| fit_tier | VARCHAR(10) | `green`, `amber`, `red` |
| screening_score | INTEGER | |
| screening_questions | JSONB | `GeneratedScreeningSet` |
| screening_evaluation | JSONB | `ScreeningEvaluation` |
| assignment_submission | JSONB | `AssignmentSubmission` |
| admin_review | JSONB | |
| stage_results | JSONB | Per-stage outcome overlay from generic stage runner: `{"<stage_key>": {"processing_status", "verdict", "result_ref", "updated_at"}}` |
| journey_report | TEXT | |
| current_stage | VARCHAR(50) INDEX | `PipelineStage` enum value |

### 2.2 Candidate Data

#### `candidate_profiles`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| candidate_id | UUID FK → candidates.id CASCADE | INDEX |
| raw_resume_r2_key | VARCHAR(500) | Resume PDF in R2 |
| parsed_data | JSONB | Structured resume output |
| extraction_confidence | JSONB | Per-field confidence |
| embedding | VECTOR(256) | pgvector for semantic search |
| created_at | TIMESTAMPTZ | |

IVFFLAT index on `embedding` with `vector_cosine_ops` (lists=100). Migration `0039_embedding_dim_256` dropped and rebuilt the index to match the new 256-dimension vectors.

#### `evidence_records`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| candidate_id | UUID | INDEX |
| fact_key | VARCHAR(128) INDEX | e.g. `years_experience`, `current_ctc` |
| fact_value | JSONB | The extracted value |
| source_stage | VARCHAR(64) | Where this was extracted |
| source_type | VARCHAR(32) | `screening`, `voice_call`, `meeting`, `assignment` |
| extraction_method | VARCHAR(32) | `llm_extract`, `structured` |
| evidence_text | TEXT | Supporting quote |
| source_ref | VARCHAR(500) | |
| confidence | FLOAT | 0-1 |
| superseded_by_id | UUID self-ref | Chain of updates |
| created_at | TIMESTAMPTZ | |

#### `decision_records`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| candidate_id | UUID | |
| decision_type | VARCHAR(64) INDEX | e.g. `screening_verdict`, `meeting_outcome` |
| outcome | VARCHAR(64) | `clear_pass`, `needs_hr_review`, `clear_reject` |
| outcome_value | JSONB | Score or structured result |
| evidence_ids | JSONB | Links to evidence_records |
| policy_rule_ids | JSONB | Links to policy_rules |
| created_at | TIMESTAMPTZ | |

### 2.3 Screening & Assessment

#### `screening_responses`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| responses | JSONB | `ScreeningAnswer[]` |
| knock_out_triggered | BOOLEAN | |
| knock_out_reason | VARCHAR(255) | |
| composite_score | INTEGER | |
| submitted_at | TIMESTAMPTZ | |

#### `assignments`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | UNIQUE |
| brief_md | TEXT | |
| problems | JSONB | |
| submission_format | JSONB | |
| evaluation_rubric | JSONB | |
| submission_url | VARCHAR(1000) | |
| submission_text | TEXT | |
| submission_r2_keys | JSONB | File artifacts |
| evaluation | JSONB | |
| score | INTEGER | |
| deadline_at | TIMESTAMPTZ | |
| submitted_at | TIMESTAMPTZ | |

#### `assessment_results`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| provider | VARCHAR(32) | e.g. `hackerrank`, `codesignal` |
| assessment_kind | VARCHAR(32) | |
| external_assessment_id | VARCHAR(255) INDEX | |
| invite_url | VARCHAR(1000) | |
| raw_result | JSONB | |
| normalized | JSONB | |
| normalized_score | NUMERIC(6,2) | |
| percentile | NUMERIC(5,2) | |
| fit_band | VARCHAR(8) | |
| status | VARCHAR(32) INDEX | `invited`, `started`, `completed` |

### 2.4 Voice Calls

#### `voice_calls`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| campaign_id | UUID FK → voice_campaigns.id SET NULL | |
| provider | VARCHAR(32) | `elevenlabs` |
| provider_call_id | VARCHAR(255) INDEX | |
| candidate_phone | VARCHAR(32) | |
| status | VARCHAR(32) INDEX | `pending`, `dialing`, `in_progress`, `completed`, `failed`, `no_answer`, `callback_requested`, `declined`, `voicemail` |
| call_kind | VARCHAR(20) | `screening`, `confirmation`, `meeting_schedule`, etc. |
| questions | JSONB | `VoiceQuestion[]` |
| answers | JSONB | `VoiceAnswer[]` |
| evaluation | JSONB | `VoiceCallScore` |
| overall_score | INTEGER | |
| verdict | VARCHAR(32) | `clear_pass`, `needs_hr_review`, `clear_reject` |
| recording_r2_key | VARCHAR(500) | Audio file in R2 |
| transcript_r2_key | VARCHAR(500) | Transcript in R2 |
| emotion_features | JSONB | `EmotionFeatures` — paralinguistic analysis |
| callback_at | TIMESTAMPTZ INDEX | |
| attempt_no | INTEGER | Default 1 |
| duration_sec | FLOAT | |

Partial unique index: `(application_id)` WHERE status IN `pending,dialing,in_progress` — prevents duplicate in-flight calls per candidate.

#### `voice_campaigns`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| role_id | UUID FK → roles.id SET NULL | |
| call_kind | VARCHAR(20) | |
| name | VARCHAR(255) | |
| status | VARCHAR(20) INDEX | `draft`, `running`, `completed` |
| total_calls / completed_calls / failed_calls | INTEGER | |
| max_concurrent / dispatch_rate_per_minute | INTEGER | Rate limiting |
| target_application_ids | JSONB | |
| context_template | JSONB | |

### 2.5 Meetings (Interviews)

#### `meeting_sessions`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| interview_id | UUID FK → interviews.id SET NULL | |
| round | VARCHAR(16) | `technical`, `ceo`, `hr` |
| teams_join_url | VARCHAR(1000) | |
| bot_provider | VARCHAR(32) | `recall`, `readai` |
| bot_id | VARCHAR(255) INDEX | |
| bot_status | VARCHAR(32) INDEX | `pending`, `scheduled`, `in_call`, `completed`, `failed` |
| scheduled_at | TIMESTAMPTZ INDEX | |
| started_at / ended_at | TIMESTAMPTZ | |
| duration_sec | FLOAT | |
| recording_r2_key | VARCHAR(500) | |
| transcript_r2_key | VARCHAR(500) | |
| participants | JSONB | |
| candidate_emotion_timeline | JSONB | `EmotionTimelineEntry[]` |
| technical_score / communication_score / confidence_score / overall_score | INTEGER | |
| report | JSONB | `MeetingAnalysis` |
| llm_report | TEXT | Free-text summary |
| verdict | VARCHAR(32) | `clear_pass`, `needs_hr_review`, `clear_reject` |
| negotiation_state | JSONB | |

**Notable:** No `UNIQUE(application_id, round)` constraint — duplicates possible (NB-4).

#### `interviews`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID FK → applications.id CASCADE | INDEX |
| scheduled_at | TIMESTAMPTZ INDEX | |
| calendar_event_id | VARCHAR(255) | |
| meeting_link | VARCHAR(500) | |
| status | VARCHAR(20) | `proposed`, `confirmed`, `rescheduled`, `completed`, `no_show`, `cancelled` |
| transcript_r2_key | VARCHAR(500) | |
| report | JSONB | |
| feedback | JSONB | |

### 2.6 Panel & Config

#### `panel_members`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| name | VARCHAR(255) | |
| email | VARCHAR(255) UNIQUE | INDEX |
| role_type | VARCHAR(20) INDEX | e.g. `interviewer`, `hiring_manager` |
| job_title | VARCHAR(255) | |
| timezone | VARCHAR(64) | Default `Asia/Kolkata` |
| calendar_provider | VARCHAR(20) | `microsoft`, `google` |
| calendar_id | VARCHAR(255) | |
| expertise_tags | JSONB | |
| department | VARCHAR(100) | |
| max_interviews_per_week | INTEGER | Default 10 |
| is_active | BOOLEAN INDEX | |

#### `config_settings`
Key-value store with JSONB values. PK is `key` VARCHAR(128). Has `is_secret` flag, `version` counter, audit trail in `config_audit` table.

#### `policy_rules`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| key | VARCHAR(128) INDEX | |
| role_id | UUID FK → roles.id CASCADE (nullable — null = global) | |
| value | JSONB | The rule value |
| value_type | VARCHAR(16) | |
| is_active | BOOLEAN | |
| version | INTEGER | |

UNIQUE `(key, role_id)` — per-role or global default.

### 2.7 Recruiter Chat System

#### `recruiter_conversations`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| actor_hash | VARCHAR(64) INDEX | Hashed recruiter identity |
| actor_role | VARCHAR(16) | `recruiter` |
| title | VARCHAR(255) | |
| state | JSONB | Agent conversation state |
| archived | BOOLEAN | |
| created_at / updated_at | TIMESTAMPTZ | |

#### `recruiter_messages`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| conversation_id | UUID FK → recruiter_conversations.id CASCADE | INDEX |
| sequence | INTEGER | Per-conversation ordering |
| role | VARCHAR(16) | `user`, `assistant`, `tool`, `system` |
| content | TEXT | |
| tool_name | VARCHAR(64) | |
| tool_calls | JSONB | |
| tool_result | JSONB | |
| attachments | JSONB | NudgeCard, ConfirmCard data |
| model | VARCHAR(64) | LLM model used |
| input_tokens / output_tokens | INTEGER | |
| latency_ms | INTEGER | |
| tombstoned | BOOLEAN | Soft-delete for edit-and-resend |

UNIQUE `(conversation_id, sequence)`.

#### `recruiter_memory`
Key-value memory per recruiter: UNIQUE `(actor_hash, scope, key)`. Value is JSONB.

### 2.8 Audit & Monitoring

#### `audit_log`
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | Append-only (trigger prevents UPDATE/DELETE) |
| candidate_id | UUID INDEX | |
| application_id | UUID INDEX | |
| action | VARCHAR(100) INDEX | |
| actor | VARCHAR(255) | `agent`, `system`, or recruiter email |
| details | JSONB | |
| model_version / prompt_version | VARCHAR | |
| langfuse_trace_id | VARCHAR(100) | |
| created_at | TIMESTAMPTZ INDEX | |

#### `pipeline_alerts`
Stuck application alerts: `application_id`, `alert_type`, `stage`, `hours_stuck`, `resolved_at`. Created by the cron stall detector.

#### `supervisor_events` / `supervisor_actions` / `supervisor_experiments`
The Supervisor engine — an event-driven governance layer that intercepts pipeline decisions, optionally routes them through experiments, and logs all actions with approval/rejection tracking.

#### `webhook_events`
Deduplicated webhook deliveries: UNIQUE `(source, external_id)`. Covers ElevenLabs, Recall.ai, etc.

#### `email_sends`
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| application_id | UUID INDEX | |
| idempotency_key | VARCHAR(255) UNIQUE | |
| to_email | VARCHAR(320) INDEX | |
| template | VARCHAR(64) | |
| provider | VARCHAR(16) | `graph`, `resend`, `smtp` |
| status | VARCHAR(20) | |

#### `processed_messages` / `consent_artifacts`
- `processed_messages`: Dedup incoming emails (PK = `message_id` VARCHAR)
- `consent_artifacts`: GDPR consent records per candidate

---

## 3. Pipeline State Machine

**Enum:** `PipelineStage` (30 values in `src/models/v1.py:22`)

```
applied → screening_sent → screening_submitted → screening_evaluated
                                                      ↓
                                            needs_hr_review ←──── reassess
                                                  ↓
                                          assignment_sent → assignment_submitted → report_ready
                                                  ↓                                    ↓
                                          voice_screen_scheduled → ... → voice_screen_evaluated
                                                                              ↓
                                                                      assessment_invited → ... → assessment_evaluated
                                                                                                      ↓
                                                                                              technical_meeting_scheduled
                                                                                                      ↓
                                                                                              technical_meeting_completed
                                                                                                      ↓
                                                                                              technical_evaluated
                                                                                                      ↓
                                                                                              technical_pending_approval
                                                                                                      ↓
                                                                                              ceo_meeting_scheduled
                                                                                                      ↓
                                                                                              ceo_meeting_completed
                                                                                                      ↓
                                                                                              ceo_pending_approval
                                                                                                      ↓
                                                                                              hr_meeting_scheduled
                                                                                                      ↓
                                                                                              hr_meeting_completed
                                                                                                      ↓
                                                                                              hr_evaluated → hired
                                                                                              ↓
                                                                                              rejected
```

Every stage can transition to `needs_hr_review` (parking lot) or `rejected`. `ALLOWED_TRANSITIONS` dict at `v1.py:60` enforces valid moves.

---

## 4. Pydantic Schemas (Wire Format)

### 4.1 Screening Pipeline

| Schema | File | Stored In | Fields |
|--------|------|-----------|--------|
| `GeneratedQuestion` | v1.py:224 | `roles.screening_questions` | id, question, type, expected_signal, required |
| `GeneratedScreeningSet` | v1.py:232 | `roles.screening_questions` | questions[], generated_at, prompt_version |
| `ScreeningAnswer` | v1.py:240 | `screening_responses.responses` | question_id, question, answer |
| `ScreeningSubmission` | v1.py:246 | `screening_responses.responses` | answers[], submitted_at |
| `PerQuestionScore` | v1.py:253 | `screening_evaluation.per_question` | question_id, score, relevance, notes |
| `LogisticsCheck` | v1.py:260 | `screening_evaluation.logistics_check` | ctc_in_range, notice_acceptable, location_workable, rationale |
| `LogisticsValues` | v1.py:267 | `screening_evaluation.logistics_values` | current_ctc, expected_ctc, notice_period, current_location, relocate |
| `ScreeningEvaluation` | v1.py:277 | `applications.screening_evaluation` | overall_score, per_question[], logistics, red_flags, strengths, verdict |
| `AssignmentFileArtifact` | v1.py:292 | `assignment_submission.files[]` | r2_key, filename, content_type, size_bytes, extracted_text_preview |
| `AssignmentSubmission` | v1.py:300 | `applications.assignment_submission` | files[], links[], notes, project_choice, deployed_url, parse_result |
| `AssignmentParseResult` | v1.py:325 | `assignment_submission.parse_result` | completeness, quality_signals, highlights, concerns, evidence_quotes, summary |
| `JourneyReport` | v1.py:337 | `applications.journey_report` | markdown, generated_at |

### 4.2 Voice Screening

| Schema | File | Fields |
|--------|------|--------|
| `VoiceQuestion` | v1.py:351 | id, question, type (8 kinds), expected_signal, follow_up_hint |
| `VoiceAnswer` | v1.py:361 | question_id, question, answer_transcript, answer_audio_r2_key, duration_sec |
| `EmotionFeatures` | v1.py:369 | avg_pitch_hz, speaking_rate_wpm, arousal_score, valence_score, dominant_emotion, etc. |
| `ExtractedCandidateFacts` | v1.py:382 | 15 optional fields — facts extracted from transcript (CTC, location, experience, skills) |
| `VoiceCallScore` | v1.py:407 | overall_score, per_question[], red_flags, strengths, verdict, extracted_facts |
| `CallKind` | v1.py:421 | StrEnum: screening, confirmation, meeting_schedule, status_update, joining_details, general_query |
| `VoiceCallStatus` | v1.py:430 | StrEnum: pending, dialing, in_progress, completed, failed, no_answer, callback_requested, declined, voicemail |

### 4.3 Meeting Analysis

| Schema | File | Fields |
|--------|------|--------|
| `MeetingRound` | v1.py:442 | StrEnum: technical, ceo, hr |
| `EmotionTimelineEntry` | v1.py:448 | t_start_sec, t_end_sec, speaker, emotion, confidence |
| `MeetingAnalysis` | v1.py:456 | scores, strengths, red_flags, highlights, candidate_emotion_timeline[], summary, verdict |
| `MeetingSession` | db/base (ORM) | See section 2.5 |

### 4.4 Recruiter Agent Tool Schemas

File: `recruiter_agent/schemas.py` — 22 tools defined as OpenAI-format function descriptors.

**Read tools:** list_candidates, get_candidate, list_roles, pipeline_metrics, stuck_applications, audit_tail, search_talent_pool, search_candidates, suggest_meeting_slots

**Write tools (require confirmation):** create_role, update_role, archive_role, set_role_assignment_brief, override_stage, send_custom_email, add_candidate_note, schedule_interview, propose_slots, schedule_meeting, reschedule_meeting, set_panel_member, add_panel_member, publish_linkedin_post, generate_assignment

**Chat management:** new_conversation, rename_conversation, archive_conversation

---

## 5. Redis Usage (14 Patterns)

### 5.1 Pub/Sub Channels

| Channel | Purpose | Producers | Consumers |
|---------|---------|-----------|-----------|
| `hiring-agent:application:{id}` | Per-application event bus | Activities, services | SSE endpoint, nudge worker |
| `recruiter-chat:{id}:nudge` | Proactive nudge to recruiter | Nudge worker | Recruiter SSE stream |
| `config:invalidate` | Config cache invalidation | Admin config API | Config store listener |
| `config:restart-workers` | Worker restart signal | Admin config API | Arq workers |

**Events published:** `stage_changed`, `voice_call_completed`, `assessment_completed`, `meeting_analyzed`, `chat_stage_change`, `chat_message`, `assignment_submitted`, `meeting_reschedule_requested`, `meeting_scheduling_needed`, `assignment_artifact_uploaded`

### 5.2 Key-Value with TTL

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `ratelimit:rpm:{unix_minute}` | Counter | 90s | LLM per-minute burst |
| `ratelimit:calls:{YYYYMMDD}` | Counter | 26h | LLM daily call count |
| `ratelimit:tokens:{YYYYMMDD}` | Counter | 26h | LLM daily token count |
| `ratelimit:usd_cents:{YYYYMMDD}` | Counter | 26h | LLM daily USD spend |
| `ratelimit:cand:{id}:{YYYYMM}` | Counter | 31d | Per-candidate monthly |
| `httprl:{bucket}:{ip}:{window}` | Counter | 2× window | HTTP rate limiter (fails closed) |
| `recruiter-chat:{id}:cancel` | Flag | 30s | Cancel running agent turn |
| `recruiter-confirm:{id}:{req}` | JSON | 10min | Pending tool confirmation |
| `schedule:slots:{app_id}` | JSON | 3d | Proposed interview slots |
| `circuit:{service}` | Sorted Set | ~15min | Failure tracking |
| `circuit:{service}:open` | Flag | ~15min | Circuit breaker state |

### 5.3 List (Queue)

| Key Pattern | Purpose |
|-------------|---------|
| `recruiter-chat:{id}:inbox` | In-memory message queue for SSE delivery (LTRIM caps at 50) |

### 5.4 Arq Job Queue

**Queue name:** `hiring-agent`

| Job Function | Description |
|-------------|-------------|
| `dispatch_voice_screening` | Initiate ElevenLabs voice call |
| `dispatch_voice_call` | General voice call dispatch |
| `evaluate_voice_call` | LLM evaluation of call transcript |
| `dispatch_assessment` | Send assessment invite |
| `dispatch_meeting_bot` | Join meeting bot (Recall.ai) |
| `analyze_meeting` | Analyze meeting transcript |
| `schedule_meeting` | Schedule interview (OLD path) |
| `schedule_meeting_reattempt` | Reschedule after candidate rejects |
| `generate_ceo_brief` | CEO round brief generation |
| `smart_schedule_meeting` | Calendar-based smart scheduling |
| `panel_availability_request` | Panel member availability check |

**Cron jobs (7):** poll_due_callbacks, reconcile_stuck_voice_calls, pipeline_sla_monitor, prune_old_artifacts, assignment_deadline_reminders, reconcile_stuck_meetings, campaign_dispatch_tick

---

## 6. Data Flow Architecture

### 6.1 Application Intake → Hire
```
Candidate fills form → /api/apply
  → create candidate, application
  → enqueue parse_resume activity
  → auto_progress: fit_score → screening_sent
  → email candidate screening questions
  → candidate submits → screening_evaluated
  → auto_progress: needs_hr_review / assignment_sent / voice_screen_scheduled
  → ... pipeline state machine advances ...
  → hired / rejected
```

### 6.2 Real-time Updates (SSE)
```
Backend event occurs (e.g. stage_changed)
  → Redis pub/sub: hiring-agent:application:{id}
  → Frontend subscribes via /api/events?application_id={id}
  → SSE stream pushes to browser
  → nudge worker also subscribes to same channel
    → creates system message in recruiter_conversation
    → pushes to recruiter-chat:{id}:nudge channel
    → recruiter SSE picks it up → renders in Pulse sidebar
```

### 6.3 Recruiter Chat (Pulse)
```
Recruiter types → POST /v2/recruiter-chat/{id}/stream
  → Message appended to recruiter_messages
  → RPUSH to redis recruiter-chat:{id}:inbox
  → RecruiterAgent runner processes (LLM call + tool execution)
  → Tool results streamed back via SSE
  → Write tools require confirmation:
    → Runner stashes pending tool in recruiter-confirm:{id}:{req} (TTL 10min)
    → Confirmation card rendered in chat
    → Recruiter clicks confirm → POST /confirm → tool executed
```

### 6.4 Voice Call Flow
```
voice_screen_scheduled → dispatch_voice_screening (Arq job)
  → ElevenLabs ConvAI initiates outbound call
  → Callback webhook → status updates
  → Call completed → webhook_events dedup
  → enqueue evaluate_voice_call
  → LLM evaluates transcript → VoiceCallScore
  → auto_progress advances stage
  → Redis pub/sub event → UI updates via SSE
```

### 6.5 Meeting Flow
```
Recruiter schedules via Pulse (schedule_meeting tool)
  → chat_meeting.book_meeting()
    → Check for existing active session (idempotency)
    → Create MeetingSession row
    → Create Google Calendar event with Meet link
    → Email invites to candidate + panel
    → Enqueue dispatch_meeting_bot
  → Meeting conducted
  → Recall.ai callback → transcript_r2_key
  → Enqueue analyze_meeting
  → LLM analyzes → MeetingAnalysis stored in session.report
  → auto_progress advances stage
```

---

## 7. Frontend Data Flow

### 7.1 Page-to-Data Mapping

| Route | Component | Fetches | Backend Endpoint |
|-------|-----------|---------|-----------------|
| `/dashboard` | ChatPanel + PulseComposer | SSE stream | `POST /v2/recruiter-chat/{id}/stream` |
| `/candidates` | Candidate list + Kanban | SWR | `GET /candidates/search`, `GET /candidates/stages/` |
| `/candidates/[id]` | Monolithic profile (~60K chars) | SWR | `GET /dashboard/v1/candidates/{id}` — joins 6+ tables |
| `/candidates/compare` | Side-by-side compare | SWR | By candidate IDs |
| `/ceo` | CEO journey list | SWR | `ceoDashboard.list()` |
| `/ceo/[id]` | AgenticJourney | SWR + SSE | `ceoDashboard.detail()` (= same as hr) |
| `/hr` | HR journey list | SWR | `GET /dashboard/v1/applications/hr` |
| `/hr/[id]` | AgenticJourney | SWR + SSE | Same endpoint as `/ceo/[id]` |
| `/meetings` | Meeting list | SWR | `GET /dashboard/v1/meetings` |
| `/assessments` | Assessment list | SWR | `GET /dashboard/v1/assessments` |
| `/voice-screens` | Voice screen list | SWR | Voice call endpoint |
| `/analytics` | Charts (Recharts) | SWR | `GET /analytics/` |
| `/roles` | Role list | SWR | `GET /dashboard/v1/roles/` |
| `/meeting/reschedule/[token]` | Candidate reschedule page | Next.js Route Handler | `GET /api/meeting-reschedule/[token]` |

### 7.2 Data Fetching Pattern
- **Library:** SWR with `swrFetcher` (fetch + auth header)
- **Global config:** max 3 retries (skip 401/404), 2s dedup
- **Realtime:** SSE for per-application events, polling fallback (8s)
- **API client:** `lib/api.ts` base helper → typed wrappers in `lib/api/*.ts`

---

## 8. UX Issues & Blocker Analysis

### Issue 1: System Messages Lost on Page Reload
**File:** `useRecruiterChat.ts:101-125`

**Current behavior:** Nudge cards (reschedule requests, stage change alerts) arrive via SSE and render in real-time. On page reload, `hydrate()` drops `role: "system"` messages — they vanish forever. The DB still has them, but the UI never renders them after refresh.

**Suggested fix:** Add `"system"` case to `hydrate()` rendering system messages as bot-side messages with attachments.

### Issue 2: Assignment Submission Content Invisible
**File:** `candidates/[id]/page.tsx:537-549`

**Current behavior:** Assignment file entries show only filename + size. The `extracted_text_preview` field exists in the model (first ~2k chars) but is never surfaced in the UI. Recruiters must download every file to evaluate.

**Suggested fix:** Render `extracted_text_preview` with syntax highlighting for code files; add collapsible "Show file content" toggle.

### Issue 3: Candidate Detail Page is a Monolith
**File:** `candidates/[id]/page.tsx` (~60K chars)

**Current behavior:** Single endpoint returns ALL data (profile, screening, voice, meetings, assignment, audit, evidence, decisions). All sections load upfront even if never scrolled to. No lazy loading. Payload grows with every new data source.

**Suggested fix:** Split backend into sub-resources (`/candidates/{id}/voice`, `/candidates/{id}/meetings`); add `?include=` query param; lazy-load sections on frontend.

### Issue 4: `/ceo/[id]` and `/hr/[id]` Share Same Backend
**File:** `ceo_dashboard.py`, `hr_dashboard.py`

Both pages use `AgenticJourney` component calling the same `ceoDashboard.detail()` endpoint. Only the list page differs.

### Issue 5: No Meeting Scheduling Notifications
When a candidate reschedules via `/meeting/reschedule/[token]`, the system publishes a `meeting_reschedule_requested` event and creates a nudge card — but the card disappears on reload (Issue 1). The recruiter gets no persistent notification.

### Issue 6: No Unique Constraint on `(application_id, round)` in `meeting_sessions`
Partial index exists for `application_id + round` but no UNIQUE constraint. Concurrent requests can create duplicate sessions.

---

## 9. Event Catalog

Events published via `publish_event()` → Redis channel `hiring-agent:application:{id}`:

| Event | Source | Consumer |
|-------|--------|----------|
| `stage_changed` | auto_progress, set_stage | Frontend SSE, nudge worker |
| `voice_call_completed` | voice webhook | Nudge worker |
| `voice_call_evaluated` | evaluate_voice_call | Frontend SSE |
| `assessment_completed` | assessment webhook | Nudge worker |
| `meeting_analyzed` | analyze_meeting | Frontend SSE |
| `meeting_scheduled` | book_meeting | Frontend SSE |
| `meeting_reschedule_requested` | meeting_reschedule API | Nudge worker |
| `meeting_scheduling_needed` | auto_progress | Nudge worker |
| `assignment_submitted` | apply API | Frontend SSE, nudge worker |
| `assignment_artifact_uploaded` | artifact upload | Frontend SSE |
| `chat_stage_change` | Pulse agent | Nudge worker |
| `chat_message` | Pulse agent | Nudge worker |

---

## 10. Key Architectural Notes

1. **No foreign key on `candidate_id` in `evidence_records`** — uses application_id FK but candidate_id is denormalized (no FK constraint)
2. **audit_log is append-only** — trigger prevents UPDATE and DELETE. Uses BIGSERIAL (not UUID) PK for sequential ordering
3. **Voice calls have application-level unique partial index** — prevents duplicate in-flight calls. Meetings lack this protection (NB-4)
4. **Recruiter messages use sequence numbers** — `(conversation_id, sequence)` unique pair enables ordered message history with edit/resend via tombstoning
5. **R2 is the binary store** — recordings, transcripts, resumes stored in Cloudflare R2; DB holds only the key
6. **Config store has a two-layer cache** — in-process (30s TTL) + Redis pub/sub invalidation
7. **Rate limiter fails open for LLM calls** — if Redis is down, LLM calls proceed. HTTP rate limiter fails closed (rejects)

---

## 11. Data Architecture Flaws & Improvements

### 11.1 JSONB Overuse

| Table | JSONB Column | Size Risk | Why It's Bad |
|-------|-------------|-----------|-------------|
| `applications` | `screening_evaluation` | ~3KB | Read every time candidate page loads, but ~80% of requests only need the verdict + score |
| `applications` | `assignment_submission` | ~50KB-2MB | LLM parse_result with evidence_quotes, highlights — shipped to frontend on every page load |
| `applications` | `screening_questions` | ~5KB | Never changes after generation but fetched alongside everything else |
| `voice_calls` | `evaluation` | ~5KB | Stored per-call, but only latest evaluation matters for pipeline decisions |
| `voice_calls` | `answers` | ~20KB | Full transcript embedded in JSONB column — no incremental streaming |
| `meeting_sessions` | `report` | ~10KB+ | Full meeting analysis with LLM report, emotion timeline, scores — all in one JSONB cell |

**The problem:** JSONB offers schema flexibility but becomes a dumping ground. The candidate detail endpoint reads 6+ JSONB columns spanning 200KB+ total. No indexing inside JSONB (can't query `screening_evaluation->>verdict` efficiently at scale). Every frontend load ships this entire payload even if only the verdict is needed.

**Fix:**
- Normalize high-query-frequency fields (verdict, score, stage) to dedicated columns with indexes
- Store large LLM artifacts (`parse_result`, `evidence_quotes`, `emotion_timeline`) in R2 with a key reference in DB
- Add generated columns for commonly filtered JSONB paths (Postgres 16 supports this natively)
- Separate `voice_calls.answers` into a `voice_call_answers` child table with one row per question (enables querying individual answers without loading all)

### 11.2 No Read Models / Materialized Views

**Current state:** Every page renders from the same normalized tables. The candidate detail page (`GET /dashboard/v1/candidates/{id}`) joins 6+ tables + 200 audit rows + JSONB extraction on every request. Scores, evaluations, and reports are recomputed from raw JSONB every time.

**The problem:** Normalized schemas are optimized for writes. Read-heavy pages (candidate profile, meetings list, pipeline overview) pay the join+parse penalty on every request. No caching layer between Postgres and the API.

**Fix:**
- Build read-model tables: `candidate_summary`, `application_summary` that denormalize scores, latest stage, latest voice verdict, latest meeting verdict into flat columns updated by DB triggers or application hooks
- Use PostgreSQL materialized views for dashboards (pipeline stage counts, stuck candidates, hiring funnels) — refresh via cron or on-stage-change event
- Add Redis caching for high-frequency reads: candidate list, stage counts, role list with `SETEX` + pub/sub invalidation

### 11.3 R2 as a Binary Black Hole

**Current state:** 8 columns across 5 tables store R2 keys (`recording_r2_key`, `transcript_r2_key`, `resume_r2_key`, `submission_r2_keys`, etc.). The actual objects (audio recordings, meeting transcripts, resume PDFs, assignment files) live in Cloudflare R2. The DB has zero metadata about these objects — no size, no content type, no checksum, no lifecycle.

**The problem:**
- Pruning old artifacts (cron `prune_old_artifacts`) requires listing R2 objects and cross-referencing DB keys — expensive and race-prone
- No referential integrity between DB rows and R2 objects — an orphaned key in the DB doesn't mean the object exists in R2, and vice versa
- No content-type validation at write time — a `.exe` uploaded as `resume.pdf` key passes all checks

**Fix:**
- Create a `storage_artifacts` table: `id, application_id, r2_key, bucket, filename, content_type, size_bytes, sha256, artifact_type (recording|transcript|resume|assignment_file), created_at, expires_at`
- All current `*_r2_key` columns become FK references to this table
- Enables lifecycle management, dedup, content validation, and easy pruning
- Expiring presigned URLs should be generated on read, not stored

### 11.4 No Event Sourcing — Current State Is All That Exists

**Current state:** Every table stores the **current** state only. `applications.current_stage` is overwritten on every transition. `voice_calls.status` is overwritten. `meeting_sessions.bot_status` is overwritten. The only historical log is `audit_log` — an append-only JSONB blob with no structured columns for the old/new values of specific fields.

**The problem:**
- To answer "what was the stage before needs_hr_review?" you parse audit_log.action + audit_log.details JSONB — brittle and slow
- Concurrent updates to the same entity (e.g., two webhook callbacks for the same voice call) can race — last writer wins, no conflict detection
- No compensation/rollback: if a stage transition fails halfway, the audit log says it happened but the application state is inconsistent
- Debugging state machine bugs requires manually reconstructing the timeline from audit log entries and hoping they're complete

**Fix:**
- Use an event-log pattern for stage transitions: `application_events` table with `id, application_id, event_type, sequence, old_value, new_value, created_at, actor`. `current_stage` becomes a materialized column derived from the latest event
- Use PostgreSQL `SERIALIZABLE` isolation for critical transitions (stage moves, voice call status) to prevent races
- Add `previous_stage` column to `applications` as immediate opt-in without full event sourcing

### 11.5 Missing Entity Relationships

| Missing Relationship | Evidence | Impact |
|---------------------|----------|--------|
| `applications` ↔ `meeting_sessions` | No direct FK from applications to meeting_sessions (only via interview_id — optional) | Finding all meetings for an application requires a join through interviews or filtering by application_id on meeting_sessions directly |
| `voice_calls` ↔ `meeting_sessions` | No link when a voice call is the screening before a meeting | Can't trace "this technical meeting came after this voice screen" without walking the application's audit log |
| `roles` ↔ `panel_members` | `role.interviewer_panel` is JSONB (email list) | Can't query "which roles is John assigned to?" without parsing JSONB in all roles |
| `candidates` ↔ `recruiter_conversations` | No link except recruiter knowing the candidate's name | No "show me all chat history for this candidate" feature |
| `assignments` ↔ `applications.evaluation` | Assignment evaluation lives in `assignments.evaluation` JSONB but `applications.assignment_submission.parse_result` duplicates it | Two sources of truth for the same evaluation |

**Fix:** Add FKs and join tables where the data model implies relationships. Normalize `role.interviewer_panel` into `role_panel_members` junction table. Link `recruiter_conversations` to `application_id` when the conversation is about a specific candidate.

### 11.6 Pipeline Stage as a String — Not a State Machine in the DB

**Current state:** `PipelineStage` is a Python `StrEnum` with 30 values. `ALLOWED_TRANSITIONS` is a Python dict. The DB column `applications.current_stage` is just `VARCHAR(50)` — no CHECK constraint, no FK to a stages table, no transition validation at the DB level.

**The problem:** Any code path can set `current_stage = "invalid_stage"` and the DB will accept it. The state machine is enforced only in Python, which means:
- A buggy migration, a direct SQL query, or a new code path can produce invalid states
- Two concurrent API calls can race and leave the application in an inconsistent state (e.g., `rejected` → `hired` because the Python check happened before the other call's write)
- No way to query "which stages can follow this one?" without loading Python code

**Fix:**
- Create a `pipeline_stages` lookup table with `stage_key, stage_order, allowed_next_stages[]` (JSONB array of stage_keys)
- Add a `FOREIGN KEY` from `applications.current_stage` to `pipeline_stages.stage_key`
- Add a CHECK constraint or trigger that validates transitions: `NEW.current_stage IN (SELECT unnest(allowed_next_stages) FROM pipeline_stages WHERE stage_key = OLD.current_stage)`
- This makes the state machine explicit, queryable, and race-condition resistant

### 11.7 No Data Tiering / Archival

**Current state:** All 29 tables live in the same PostgreSQL instance. `audit_log`, `recruiter_messages`, `voice_call_answers` all grow unboundedly. The `prune_old_artifacts` cron deletes R2 objects but DB rows are never archived.

**The problem:** `audit_log` entries are frequently queried as part of the candidate detail endpoint (`ORDER BY created_at DESC LIMIT 200`). As more applications flow through, this query gets slower. No partition strategy for time-series tables. No archival of completed applications.

**Fix:**
- Partition `audit_log` by month on `created_at` — old partitions can be `DETACH`ed to a slower tablespace or deleted
- Add `archived_at` to `applications` — applications that reached a terminal stage (hired/rejected) and are >90 days old get archived (summary row + data moved to cold storage)
- Archive old `voice_calls` with `status = completed` and age > 180 days — keep only the evaluation (small) and delete the full transcript and answers

### 11.8 Schema Drift Between ORM and DB

**Current state:** SQLAlchemy ORM models in `db/base.py` define the schema. Alembic migrations apply changes. Pydantic models in `models/v1.py` define the wire format. These three layers can (and do) drift apart.

**The problem:** When a new column is added to the ORM model but the migration is not created, the app still works (SQLAlchemy ignores unknown columns in SELECT, raises on INSERT for NOT NULL). When the Pydantic model adds a field that the ORM model doesn't have, the API returns 500. There's no automated check that the three layers are in sync.

**Fix:**
- Add a CI test that introspects the DB schema, the ORM model, and the Pydantic model for each entity and reports mismatches
- Use a code-generation step: Pydantic models generated from ORM models (or vice versa) to guarantee one source of truth
- Add integration tests that hydrate a full entity from the DB and serialize it through the Pydantic model without error

### 11.9 Redis as Ephemeral Queue for Persistent Data

**Current state:** `recruiter-chat:{id}:inbox` is a Redis list used as a message queue for SSE delivery. When a recruiter sends a message, the backend writes it to Postgres AND pushes it to Redis. The SSE stream drains from Redis (BLPOP). On page reload, messages are loaded from Postgres via `hydrate()`.

**The problem:** This dual-write pattern means:
- If the Redis write succeeds but the Postgres write fails, the message appears in the chat but is lost on reload (phantom message)
- If the Postgres write succeeds but the Redis write fails, the message is persisted but never delivered via SSE (silent loss — recruiter must reload)
- No transaction across Redis and Postgres — they can't be in a distributed transaction
- The LTRIM cap of 50 means a fast-streaming conversation can drop messages from Redis even though they're in Postgres

**Fix:** Remove Redis from the write path entirely. Write only to Postgres. Use Postgres `LISTEN/NOTIFY` or a lightweight polling mechanism (every 500ms, SELECT messages WHERE sequence > last_seen) for SSE delivery. This eliminates the dual-write problem and simplifies the architecture. Or, keep Redis as a read-only cache: Write to Postgres → PUBLISH event ID → SSE reads from Postgres by ID. Never write to Redis from the API.

### 11.10 No Data Validation at the DB Level

**Current state:** String columns like `status`, `round`, `call_kind`, `role_type` are `VARCHAR` with no CHECK constraints. Enum validation happens only in Python code. If a buggy code path writes `"technial"` instead of `"technical"`, the DB accepts it.

**The problem:** Silent data corruption. A typo in a tool implementation, a migration that sets the wrong default, or a direct SQL query can produce invalid values. These are invisible until a downstream query filters by the correct value and misses rows.

**Fix:** Add CHECK constraints for all enum-like columns:
```sql
ALTER TABLE meeting_sessions ADD CONSTRAINT chk_meeting_sessions_round
  CHECK (round IN ('technical', 'ceo', 'hr'));
ALTER TABLE voice_calls ADD CONSTRAINT chk_voice_calls_status
  CHECK (status IN ('pending','dialing','in_progress','completed','failed','no_answer','callback_requested','declined','voicemail'));
ALTER TABLE applications ADD CONSTRAINT chk_applications_stage
  CHECK (current_stage IN ('applied','screening_sent',...,'hired'));
```
Use PostgreSQL domains for reusable enums across tables.

### 11.11 Summary: Priority-Ordered Improvements

| # | Improvement | Effort | Impact | Dependencies |
|---|------------|--------|--------|-------------|
| 1 | Add DB-level CHECK constraints for enum columns | 1 day | Prevents silent data corruption across all code paths | None |
| 2 | Add FK from `evidence_records.candidate_id` to candidates | 0.5 day | Fixes referential integrity hole | None |
| 3 | Add partial UNIQUE index on `meeting_sessions(application_id, round)` | 0.5 day | Prevents duplicate meeting sessions (NB-4) | None |
| 4 | Normalize `role.interviewer_panel` into junction table | 1 day | Enables querying panel assignments | Role- panel migration |
| 5 | Add read-model tables for candidate summary | 2 days | 10x faster candidate detail page | Trigger/event setup |
| 6 | Replace Redis dual-write with Postgres-based SSE delivery | 2 days | Eliminates phantom messages and silent drops | SSE refactor |
| 7 | Create `storage_artifacts` table for R2 objects | 2 days | Enables lifecycle management, dedup, integrity | Migration + R2 sync |
| 8 | Add event-log table for stage transitions | 3 days | Enables rollback, race-proof transitions, queryable history | State machine change |
| 9 | Partition `audit_log` by month | 1 day | Prevents unbounded query slowdown | Postgres 16 native partitioning |
| 10 | Create `pipeline_stages` lookup table with DB-level transition validation | 2 days | Race-proof state machine, queryable transitions | Migration + trigger |
