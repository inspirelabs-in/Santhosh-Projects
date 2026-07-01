# HR Agent — Configuration Audit

**Total keys defined across `config.py` + `config_schema.py`: 103**

This document captures the full audit of every env/config key — what's active, what's dead,
what's load-bearing, and what's safe to delete. Last updated: 2026-06-30.

---

## Summary

| Category | Count |
|---|---|
| Fully active — real runtime effect | ~65 |
| Dead — defined, never read in src/ | ~18 |
| Partial — gated/experimental, disabled by default | ~15 |

---

## Active Keys (real effect on pipeline)

### LLM

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `LLM_PROVIDER` | `llm_provider` | `llm/client.py`, `api/role_drafting.py` | Routes all LLM calls through LiteLLM (openai, anthropic, groq, ollama) |
| `LLM_MODEL_FAST` | `llm_model_fast` | `llm/client.py`, `activities/parse_resume.py` | Fast model for screening/parsing (default: gpt-4o-mini) |
| `LLM_MODEL_SMART` | `llm_model_smart` | `llm/client.py`, `supervisor/engine.py` | Smart model for evaluation and reports (default: gpt-4o) |
| `EMBEDDING_MODEL` | `embedding_model` | `activities/parse_resume.py`, `api/talent_search.py`, `recruiter_agent/tools.py` | OpenAI embeddings for resume semantic search |
| `LLM_DAILY_CALL_LIMIT` | `llm_daily_call_limit` | `services/rate_limit.py` | Hard cap on LLM API calls per day |
| `LLM_DAILY_TOKEN_LIMIT` | `llm_daily_token_limit` | `services/rate_limit.py` | Hard cap on tokens consumed daily |
| `LLM_PER_CANDIDATE_MONTHLY_LIMIT` | `llm_per_candidate_monthly_limit` | `services/rate_limit.py` | Per-candidate token budget |
| `LLM_RATE_LIMIT_PER_MINUTE` | `llm_rate_limit_per_minute` | `services/rate_limit.py` | Per-minute call rate limiter |
| `LLM_DAILY_BUDGET_USD` | `llm_daily_budget_usd` | `services/rate_limit.py` | Soft spend cap with warning/blocking |
| `GEMINI_API_KEY` | `gemini_api_key` | `activities/v1_evaluate_voice_call.py:225`, `services/gemini_audio_eval.py:52,76` | Gemini 2.0 Flash for post-call audio scoring |

### Voice (ElevenLabs)

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `ENABLE_VOICE_SCREENING` | `enable_voice_screening` | `activities/v1_voice_screening.py`, `api/meetings.py`, `workers/jobs.py`, `v1.py:287` | **Master toggle** — if False, voice calls never dispatch |
| `ELEVENLABS_API_KEY` | `elevenlabs_api_key` | `workers/jobs.py`, `api/webhooks_voice.py`, `services/voice_provider.py` | Authenticates ElevenLabs agent API calls |
| `ELEVENLABS_AGENT_ID` | `elevenlabs_agent_id` | `api/settings.py`, `services/voice_provider.py` | Identifies which ElevenLabs agent to use |
| `ELEVENLABS_PHONE_NUMBER_ID` | `elevenlabs_phone_number_id` | `services/voice_provider.py` | Twilio phone routing identifier |
| `ELEVENLABS_WEBHOOK_SECRET` | `elevenlabs_webhook_secret` | `api/webhooks_voice.py:112` | HMAC signing verification for voice webhooks |
| `VOICE_AGENT_COMPANY_NAME` | `voice_agent_company_name` | 30+ files across activities/, services/, api/ | Company name announced in voice calls and injected into emails |
| `VOICE_AGENT_MAX_CALL_SECONDS` | `voice_agent_max_call_seconds` | `activities/v1_voice_screening.py`, `workers/jobs.py` | Call duration hard timeout (default: 1200s) |
| `VOICE_AGENT_MAX_QUESTIONS` | `voice_agent_max_questions` | `activities/v1_dispatch_voice_call.py`, `v1_voice_screening.py` | Question count limiter |
| `VOICE_AGENT_MAX_CALLBACK_ATTEMPTS` | `voice_agent_max_callback_attempts` | `workers/jobs.py:242`, `activities/v1_dispatch_voice_call.py` | Retry budget for callback logic |
| `VOICE_AGENT_MAX_NOANSWER_ATTEMPTS` | `voice_agent_max_noanswer_attempts` | `api/webhooks_voice.py`, `services/auto_progress.py` | No-answer retry count before parking candidate |
| `VOICE_AGENT_NOANSWER_BACKOFF_SECONDS` | `voice_agent_noanswer_backoff_seconds` | `api/webhooks_voice.py:690` | Base backoff for exponential retry (default: 1800s) |
| `VOICE_CALL_WINDOW_START_HOUR` | `voice_call_window_start_hour` | `services/voice_window.py:57` | Quiet hours start (0–24) |
| `VOICE_CALL_WINDOW_END_HOUR` | `voice_call_window_end_hour` | `services/voice_window.py:58` | Quiet hours end (0–24) |
| `VOICE_CALL_WINDOW_TZ` | `voice_call_window_tz` | `services/voice_window.py:59`, `api/ceo_dashboard.py` | Timezone for call window (default: Asia/Kolkata) |
| `VOICE_CALL_SKIP_WEEKENDS` | `voice_call_skip_weekends` | `services/voice_window.py:61` | Skip dialing on weekends |
| `ENABLE_VOICE_MEETING_CONFIRMATION` | `enable_voice_meeting_confirmation` | `activities/v1_schedule_meeting.py:375` | Use voice for meeting confirmations |

### Email

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `EMAIL_PROVIDER` | `email_provider` | `channels/email.py:382–422` | Router: Graph → Resend → SMTP fallback chain |
| `RESEND_API_KEY` | `resend_api_key` | `channels/email.py` | Resend HTTPS API authentication |
| `RESEND_FROM_EMAIL` | `resend_from_email` | `channels/email.py`, `recruiter_agent/tools.py` | Default from address |
| `RESEND_FROM_NAME` | `resend_from_name` | `channels/email.py:336,341` | Display name for outbound emails |
| `RESEND_REPLY_TO` | `resend_reply_to` | `channels/email.py:380` | Reply-to header |
| `EMAIL_FROM_TECHNICAL` | `email_from_technical` | `channels/email.py:322` | Sender override for tech review emails |
| `EMAIL_FROM_CEO` | `email_from_ceo` | `channels/email.py:324` | Sender override for CEO handoff emails |
| `EMAIL_FROM_HR` | `email_from_hr` | `channels/email.py:326,327` | Sender override for HR and offer emails |
| `SMTP_HOST` | `smtp_host` | `channels/email.py:303` | SMTP fallback host |
| `SMTP_PORT` | `smtp_port` | `channels/email.py:304,307,308` | SMTP port (default: 1025 for MailHog in dev) |
| `SMTP_USER` | `smtp_user` | `channels/email.py:305` | SMTP username |
| `SMTP_PASSWORD` | `smtp_password` | `channels/email.py:306` | SMTP auth password |
| `SMTP_USE_TLS` | `smtp_use_tls` | `channels/email.py:307,308` | TLS/STARTTLS selection |
| `MAIL_INBOXES` | `mail_inboxes` | `services/imap_inbox.py:80` | JSON array of IMAP configs for inbound mail polling |
| `MAIL_POLL_INTERVAL_SECONDS` | `mail_poll_interval_seconds` | `services/mail_ingest.py:506` | Inbound email polling cadence |

### Microsoft Graph / Teams

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `GRAPH_TENANT_ID` | `graph_tenant_id` | `channels/email.py:120` | Microsoft Graph OAuth tenant |
| `GRAPH_CLIENT_ID` | `graph_client_id` | `channels/email.py:122` | Graph OAuth app client ID |
| `GRAPH_CLIENT_SECRET` | `graph_client_secret` | `channels/email.py:123` | Graph OAuth secret |
| `GRAPH_ORGANISER_EMAIL` | `graph_organiser_email` | `services/online_meeting.py:95`, `services/meeting_bot.py:308` | Calendar organiser mailbox |

### Meetings & Bots

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `ENABLE_MEETING_ANALYSIS` | `enable_meeting_analysis` | `activities/v1_dispatch_meeting_bot.py:47`, `v1_schedule_meeting.py:114` | **Master toggle** for meeting bot dispatch |
| `MEETING_BOT_PROVIDER` | `meeting_bot_provider` | `services/meeting_bot.py:303`, `activities/v1_dispatch_meeting_bot.py:87` | Select readai / recall / graph |
| `ONLINE_MEETING_PROVIDER` | `online_meeting_provider` | `services/online_meeting.py:25,40,66,94` | Switch between Graph (Teams) and Google Meet |
| `READ_AI_API_KEY` | `read_ai_api_key` | `services/meeting_bot.py:306`, `api/webhooks_meeting.py:411` | Read.ai API auth (primary meeting bot) |
| `READ_AI_BASE_URL` | `read_ai_base_url` | `services/meeting_bot.py:307` | Read.ai API endpoint |
| `READ_AI_WEBHOOK_SECRET` | `read_ai_webhook_secret` | `api/webhooks_meeting.py:311` | HMAC signing key for Read.ai webhooks |
| `READ_AI_ORGANISER_EMAIL` | `read_ai_organiser_email` | `services/meeting_bot.py:308` | Calendar organiser connected to Read.ai |
| `RECALL_AI_API_KEY` | `recall_ai_api_key` | `services/meeting_bot.py:312,315` | Recall.ai API (legacy fallback only) |
| `RECALL_AI_BASE_URL` | `recall_ai_base_url` | `services/meeting_bot.py:315` | Recall.ai endpoint |
| `RECALL_BOT_DISPLAY_NAME` | `recall_bot_display_name` | `activities/v1_dispatch_meeting_bot.py:92`, `v1_schedule_meeting.py:456` | Bot display name in Recall |

### Google (Meet / Calendar)

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | `google_oauth_client_id` | `services/gmeet_meeting.py:50` | Google Meet OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `google_oauth_client_secret` | `services/gmeet_meeting.py:50` | Google Meet OAuth secret |
| `GOOGLE_OAUTH_ORGANISER_EMAIL` | `google_oauth_organiser_email` | `services/online_meeting.py:95` | Gmail account used for calendar |
| `GOOGLE_OAUTH_TOKEN_PATH` | `google_oauth_token_path` | `services/gmeet_meeting.py:40` | Path to cached OAuth token file |

### WhatsApp / SMS

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `ENABLE_WHATSAPP` | `enable_whatsapp` | `channels/whatsapp.py:56` | **Toggle** — guards all WhatsApp channel init |
| `WHATSAPP_ACCESS_TOKEN` | `whatsapp_access_token` | `channels/whatsapp.py:60,103` | Meta Cloud API auth |
| `WHATSAPP_PHONE_NUMBER_ID` | `whatsapp_phone_number_id` | `channels/whatsapp.py:101` | Phone routing ID |
| `WHATSAPP_APP_SECRET` | `whatsapp_app_secret` | `api/webhooks_whatsapp.py:37` | HMAC verification for webhooks |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | `whatsapp_webhook_verify_token` | `api/webhooks_whatsapp.py:55` | Challenge-response verification token |
| `ENABLE_SMS_REMINDERS` | `enable_sms_reminders` | `channels/sms.py:39` | **Toggle** — guards SMS channel |
| `MSG91_AUTH_KEY` | `msg91_auth_key` | `channels/sms.py:41,63` | MSG91 API authentication |
| `MSG91_SENDER_ID` | `msg91_sender_id` | `channels/sms.py:55` | Sender ID (GRABON) |
| `MSG91_TEMPLATE_ID_REMINDER` | `msg91_template_id_reminder` | `channels/sms.py:49` | SMS template for reminders |

### Pipeline / Org

| Key | Settings Attr | Where Used | What It Does |
|---|---|---|---|
| `ENABLE_ASSESSMENT_ROUND` | `enable_assessment_round` | `services/auto_progress.py:431` | Gates one auto-progress branch — NOT a full PI flag |
| `AUTO_APPROVE_GREEN_TIER` | `auto_approve_green_tier` | `api/settings.py` | Auto-pass top-tier candidates without HR review |
| `SCREENING_URL_TTL_DAYS` | `screening_url_ttl_days` | `services/screening_url.py:55,119` | Expiry for screening invite links (default: 14 days) |
| `DATA_RETENTION_DAYS_DEFAULT` | `data_retention_days_default` | `workers/jobs.py:749` | Age threshold for candidate/app purge (default: 180 days) |
| `DPO_CONTACT_EMAIL` | `dpo_contact_email` | `api/settings.py` | Data Privacy Officer email (shown in GDPR flows) |
| `GITHUB_TOKEN` | `github_token` | `services/submission_enrichment.py:29` | GitHub token for assignment submission analysis |
| `LOOM_API_KEY` | `loom_api_key` | `services/submission_enrichment.py:163` | Loom API for video submission analysis |

---

## Dead Keys — Safe to Remove

These are defined in `config.py` and/or `config_schema.py` but are **never read anywhere in `src/`**.
Removing them has zero runtime effect. Remove the field from `config.py`, the `ConfigField` from
`config_schema.py`, and any UI toggle that references them.

| Key | Settings Attr | Why Dead |
|---|---|---|
| `OPENAI_API_KEY` | `openai_api_key` | LiteLLM reads this directly from env — the Settings field is never accessed |
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | Same — LiteLLM handles auth internally |
| `GROQ_API_KEY` | `groq_api_key` | Same |
| `OLLAMA_BASE_URL` | `ollama_base_url` | Only appears in `config.py:55`, never read elsewhere |
| `LANGFUSE_PUBLIC_KEY` | `langfuse_public_key` | LiteLLM integration reads from env directly |
| `LANGFUSE_SECRET_KEY` | `langfuse_secret_key` | Same |
| `LANGFUSE_HOST` | `langfuse_host` | Same |
| `CALCOM_API_KEY` | `calcom_api_key` | Cal.com integration was never built |
| `CALCOM_BASE_URL` | `calcom_base_url` | Same |
| `GMAIL_SERVICE_ACCOUNT_JSON` | `gmail_service_account_json` | Legacy Gmail Pub/Sub, replaced by IMAP polling |
| `GMAIL_WATCH_TOPIC` | `gmail_watch_topic` | Same |
| `GMAIL_PUBSUB_SUBSCRIPTION` | `gmail_pubsub_subscription` | Same |
| `LINKEDIN_ACCESS_TOKEN` | `linkedin_access_token` | Agent tool exists but never reads this Settings field |
| `LINKEDIN_AUTHOR_URN` | `linkedin_author_urn` | Same |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | `whatsapp_business_account_id` | WhatsApp channel works without this — never read |
| `ADMIN_NOTIFY_EMAIL` | `admin_notify_email` | In schema, never read in src/ |
| `VOICE_PROVIDER` | `voice_provider` | Hardcoded to `"elevenlabs"` in config.py — this field is a no-op |
| `TWILIO_AUTH_TOKEN` | `twilio_auth_token` | Defined, never read |
| `ENABLE_CEO_DASHBOARD` | `enable_ceo_dashboard` | In config.py, zero grep hits in src/ |

> When removing these: also check `config_schema.py` for the matching `ConfigField` and any
> settings UI component that renders it. The field in `config.py` alone is harmless but adds noise.

---

## Partial / Gated Keys — Experimental, Disabled by Default

These exist, are checked in code, but guard experimental phases that are not part of the main
V2 pipeline. All default to `False`. Safe to remove if you also delete the gated code blocks —
otherwise leave them to avoid `AttributeError`.

| Key | Settings Attr | Code Path | Notes |
|---|---|---|---|
| `ENABLE_EVIDENCE_COLLECTION` | `enable_evidence_collection` | `activities/` (Phase 1) | Experimental evidence gathering — not in V2 pipeline |
| `ENABLE_FACT_VERIFICATION` | `enable_fact_verification` | `db/repositories/evidence.py:120` | Experimental Phase 2 — one file |
| `ENABLE_SUPERVISOR` | `enable_supervisor` | `supervisor/engine.py:581` | Code comments indicate this is retired |
| `SUPERVISOR_MODE` | `supervisor_mode` | `supervisor/engine.py` | Only active if `enable_supervisor=True` |
| `SUPERVISOR_MAX_ACTIONS_PER_EVENT` | `supervisor_max_actions_per_event` | `supervisor/engine.py` | Same |
| `ENABLE_LLM_CLASSIFIERS` | `enable_llm_classifiers` | `classifiers/` modules | Phase 5 experimental |
| `ENABLE_CONFIDENCE_GATES` | `enable_confidence_gates` | `services/confidence_gate.py:49` | Phase 6 experimental |
| `ARQ_ENABLED` | `arq_enabled` | `workers/main.py`, `services/queue.py` | Background queue — active if you use Arq |
| `ARQ_QUEUE_NAME` | `arq_queue_name` | Workers | Only if `ARQ_ENABLED=True` |
| `ARQ_MAX_JOBS` | `arq_max_jobs` | Workers | Same |
| `ARQ_CALLBACK_POLL_INTERVAL_SECONDS` | `arq_callback_poll_interval_seconds` | Workers | Same |
| `INBOUND_CALL_MODE` | `inbound_call_mode` | `api/webhooks_inbound_call.py:205` | Only `live_agent` branch is gated |
| `VOICE_CAMPAIGN_MAX_CONCURRENT` | `voice_campaign_max_concurrent` | `api/v1_campaigns.py:88` | Behind voice campaigns feature gate |
| `VOICE_CAMPAIGN_DISPATCH_RATE_PER_MINUTE` | `voice_campaign_dispatch_rate_per_minute` | `api/v1_campaigns.py:89` | Same |

---

## Known Schema Mismatches (Bugs)

Two keys have conflicting defaults between `config_schema.py` (what the settings UI displays)
and `config.py` (what actually runs on a fresh deployment). Whoever sets these via the UI gets
one value; a fresh deployment without touching settings gets another.

| Key | config.py default | config_schema.py default | Effect |
|---|---|---|---|
| `VOICE_AGENT_MAX_QUESTIONS` | `5` | `3` | UI shows 3; unset deployments use 5 |
| `VOICE_CALL_WINDOW_START_HOUR` | `0` | `11` | UI shows 11 IST; unset deployments have no window |
| `VOICE_CALL_WINDOW_END_HOUR` | `24` | `20` | UI shows 20 IST; unset deployments have no window |

Fix: align `config.py` defaults to match schema or vice versa. Schema is the user-facing contract — prefer aligning config.py to it.

---

## Notes on `enable_assessment_round`

This key is labeled "Enable assessment round (PI Cognitive)" in the schema but is **not a full
PI test flag**. It gates exactly one branch in `services/auto_progress.py:431`. It does not
control assignment email content, role configuration, or PI link injection.

**Do not reuse this for the PI test links feature.** Add a separate `pi_tests_enabled` flag.

---

## Keys Defined in config.py But Not Verified in src/

These show up in `config.py` but no direct grep hit was found in `src/`. They may be read
indirectly (via env var, passed to external libs, or consumed at startup):

- `GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON`, `GOOGLE_CALENDAR_IMPERSONATE_USER`
- `APP_BASE_URL`, `FRONTEND_BASE_URL` (likely consumed by template rendering or candidate links)
- `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_RESUMES`, `R2_BUCKET_CONSENT`, `R2_REGION`
- `DATA_RETENTION_DAYS_TALENT_POOL`
- `GRAPH_TRANSCRIPTS_ENABLED`
- `EMOTION_MODEL_PROVIDER`, `EMOTION_MODEL_ENDPOINT`, `EMOTION_API_KEY`
- `READ_AI_POLL_DELAY_SECONDS`, `READ_AI_POLL_MAX_ATTEMPTS`, `READ_AI_POLL_INTERVAL_SECONDS`
- `MAIL_MAX_MESSAGES_PER_POLL`
- `TEAMS_WEBHOOK_HR`, `TEAMS_WEBHOOK_ALERTS`

Do not blindly delete these — grep the specific attr name before removing.
