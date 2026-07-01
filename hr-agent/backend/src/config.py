"""Central settings. All env-driven, loaded once via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_base_url: str = "http://localhost:8000"
    # Public URL of the Next.js frontend (used to build candidate-facing
    # links sent over email). When backend is exposed via one ngrok tunnel
    # and frontend via another, set this to the frontend tunnel host so
    # links open the React form, not the JSON API.
    frontend_base_url: str = "http://localhost:3100"
    log_level: str = "INFO"
    sentry_dsn: str | None = None

    # Postgres
    database_url: str
    database_url_sync: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Temporal
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "hiring-agent"

    # LLM provider — routed via LiteLLM. Set LLM_MODEL_FAST/SMART with the
    # provider prefix (e.g. "openai/gpt-4o-mini", "groq/llama-3.3-70b-versatile",
    # "ollama/qwen2.5:7b", "anthropic/claude-haiku-4-5").
    llm_provider: Literal["openai", "ollama", "anthropic", "groq"] = "openai"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    llm_model_fast: str = "openai/gpt-4o-mini"
    llm_model_smart: str = "openai/gpt-4o"
    embedding_model: str = "text-embedding-3-large"

    # Optional: point LiteLLM at a local Ollama server for zero-cost LLM.
    # Set alongside LLM_MODEL_FAST=ollama/llama3.1:8b etc.
    ollama_base_url: str | None = None

    # LLM usage caps (prevent runaway spend / abuse)
    llm_daily_call_limit: int = 2000
    llm_daily_token_limit: int = 5_000_000
    llm_per_candidate_monthly_limit: int = 40
    llm_rate_limit_per_minute: int = 60
    llm_daily_budget_usd: float = 20.0

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # R2
    r2_endpoint_url: str | None = None
    # Public endpoint used when signing URLs the browser must hit. In dev this is
    # http://localhost:9000 (MinIO port-mapped to host) while r2_endpoint_url is
    # http://minio:9000 (reachable only from inside the compose network). In
    # prod with real R2/S3, leave unset and presigned URLs use r2_endpoint_url.
    r2_public_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    # LinkedIn (optional). Required only for publish_linkedin_post tool.
    linkedin_access_token: str | None = None
    linkedin_author_urn: str | None = None

    r2_bucket_resumes: str = "hiring-agent-resumes"
    r2_bucket_consent: str = "hiring-agent-consent"
    r2_region: str = "auto"

    # Outbound email provider selector.
    #   "auto"   -> Graph if creds present, else Resend, else SMTP.
    #   "graph"  -> Microsoft Graph /sendMail via existing graph_* OAuth app.
    #              Recommended for Microsoft 365 tenants.
    #   "resend" -> Resend HTTPS API (requires resend_api_key).
    #   "smtp"   -> Direct SMTP (Outlook smtp.office365.com / Gmail / MailHog).
    email_provider: Literal["auto", "graph", "resend", "smtp"] = "auto"

    # Resend (legacy HTTPS provider — kept for fallback / non-M365 envs)
    resend_api_key: str | None = None
    resend_from_email: str = "careers@grabon.in"
    resend_from_name: str = "GrabOn Careers"
    resend_reply_to: str = "careers@grabon.in"
    admin_notify_email: str = "careers@grabon.in"

    # Per-round sender aliases. Override per env to send tech panel emails from
    # technical@grabon.in, CEO emails from ceo@grabon.in, HR emails from
    # hr@grabon.in. Empty string falls back to resend_from_email (careers@).
    email_from_technical: str | None = None
    email_from_ceo: str | None = None
    email_from_hr: str | None = None

    # Voice agent scope. V1 uses voice ONLY for the initial screening
    # questions round. All other touchpoints (meeting confirmation, slot
    # negotiation, reminders) go through email. Flip these to true once
    # voice infra is ready and budget allows.
    enable_voice_meeting_confirmation: bool = False

    # SMTP fallback -- MailHog by default for local dev.
    # In prod use any SMTP provider (Brevo free tier: 300/day; AWS SES; Postfix).
    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False

    # Landing-page lead capture. When set, the public /leads endpoint emails
    # visitor enquiries here. Unset -> the endpoint returns not-configured and
    # the site hides the form gracefully.
    leads_email: str | None = None

    # WhatsApp (Meta Cloud)
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_webhook_verify_token: str | None = None
    whatsapp_app_secret: str | None = None

    # SMS
    msg91_auth_key: str | None = None
    msg91_sender_id: str = "GRABON"
    msg91_template_id_reminder: str | None = None

    # Google
    gmail_service_account_json: str | None = None
    gmail_watch_topic: str | None = None
    gmail_pubsub_subscription: str | None = None

    # IMAP polling (zero-cloud path to ingest from Gmail / Outlook / any IMAP mailbox).
    # Each mailbox is a separate JSON object so multiple can be polled.
    # Example MAIL_INBOXES='[{"label":"gmail-careers","host":"imap.gmail.com","port":993,"user":"careers@grabon.in","password":"app_pw","source":"gmail","folder":"INBOX"}]'
    mail_inboxes: str | None = None
    mail_poll_interval_seconds: int = 60
    mail_max_messages_per_poll: int = 25

    # Microsoft Graph (Outlook webhook / API fallback + Teams scheduling).
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: str | None = None
    graph_webhook_client_state: str | None = None
    graph_organiser_email: str | None = None  # mailbox the agent organises meetings under
    google_calendar_service_account_json: str | None = None
    google_calendar_impersonate_user: str | None = None

    # Google OAuth user-flow (personal Gmail / Workspace user calendar).
    # Used by services/gmeet_meeting.py to create Calendar events with
    # auto-generated Google Meet links.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_project_id: str | None = None
    google_oauth_token_path: str = "secrets/google_token.json"
    google_oauth_organiser_email: str | None = None  # Gmail that owns the calendar

    # Online meeting provider switch:
    #   "graph" -> Microsoft Teams via Microsoft Graph (uses graph_* creds)
    #   "gmeet" -> Google Meet via Calendar API (uses google_oauth_* creds)
    online_meeting_provider: Literal["graph", "gmeet"] = "graph"

    # Cal.com
    calcom_api_key: str | None = None
    calcom_base_url: str = "https://cal.com"

    # Microsoft Teams (replaces Slack). Generate each webhook via:
    #   Teams channel → ••• → Workflows → "Post to a channel when a webhook
    #   request is received" → copy URL.
    teams_webhook_hr: str | None = None
    teams_webhook_alerts: str | None = None

    # Security
    jwt_signing_secret: str = Field(default="change_me_long_random_string", min_length=16)
    jwt_issuer: str = "hiring-agent"
    screening_url_ttl_days: int = 14

    # Dashboard key map -- JSON string, parsed lazily by ``api.auth``.
    # Kept as raw string (not dict) so pydantic-settings does not try to
    # auto-parse and so the value survives docker-compose env_file quirks.
    dashboard_keys: str = "{}"

    # Compliance
    data_retention_days_default: int = 180
    data_retention_days_talent_pool: int = 540
    dpo_contact_email: str = "dpo@grabon.in"

    # ------------------------------------------------------------------
    # Agentic V2: Voice screening (ElevenLabs Conversational AI)
    # ------------------------------------------------------------------
    # ElevenLabs hosts the agent end-to-end (STT + LLM + TTS) and bridges
    # via Twilio for telephony. We keep ONE persistent agent and override
    # the system prompt + first message per call. No self-hosted SIP
    # orchestrator, no Pipecat sidecar.
    voice_provider: Literal["elevenlabs"] = "elevenlabs"
    elevenlabs_api_key: str | None = None
    elevenlabs_agent_id: str | None = None
    elevenlabs_phone_number_id: str | None = None
    elevenlabs_webhook_secret: str | None = None
    twilio_auth_token: str | None = None

    # Voice agent behavior
    voice_agent_company_name: str = "GrabOn"
    voice_agent_max_call_seconds: int = 1200
    voice_agent_max_questions: int = 5
    voice_agent_pass_threshold: int = 65
    voice_agent_max_callback_attempts: int = 3
    # Shared evaluation routing (services/evaluation.route_score). A stage's
    # 0-100 score maps to: pass if >= threshold; reject only if clearly below
    # (< threshold - reject_band); the band in between parks for HR review.
    # Asymmetric on purpose -- the risk is losing good candidates, so borderline
    # goes to a human rather than an auto-reject. Global defaults; tune here.
    eval_pass_threshold: int = 70
    eval_reject_band: int = 15
    # No-answer retry policy: agent re-dials this many times with exponential
    # backoff before parking for HR review.
    voice_agent_max_noanswer_attempts: int = 3
    voice_agent_noanswer_backoff_seconds: int = 1800  # base; doubles each retry

    # Quiet hours -- never dial outside this window in the candidate's tz.
    # Set to 0-24 to disable (dev/testing). Production: 11-20 IST.
    voice_call_window_start_hour: int = 0
    voice_call_window_end_hour: int = 24
    voice_call_window_tz: str = "Asia/Kolkata"
    voice_call_skip_weekends: bool = False

    # ------------------------------------------------------------------
    # Agentic V2: Assessment round
    # ------------------------------------------------------------------
    # PI Cognitive is sent as a static link pasted onto each role at
    # creation time -- there is no per-call PI API integration. The
    # ``enable_assessment_round`` flag still gates the dispatch activity.

    # ------------------------------------------------------------------
    # Agentic V2: Meeting bot
    #
    # Read.ai is the production provider. Read.ai joins meetings via its
    # native calendar OAuth integration (Microsoft 365 / Google Calendar)
    # — there is NO bot-dispatch HTTP endpoint. The dispatch activity
    # therefore creates the meeting_session row and registers it for
    # webhook/poll matching; the actual join happens because the meeting
    # sits on the Read.ai-connected organiser calendar.
    #
    # Read.ai delivers completed reports via:
    #   1. Webhook (preferred) -> /webhooks/meeting/readai
    #   2. Polling REST API     -> GET {base}/meetings (fallback)
    #
    # Recall.ai legacy fields are kept for back-compat but unused when
    # meeting_bot_provider == "readai".
    # ------------------------------------------------------------------
    meeting_bot_provider: Literal["readai", "recall", "graph"] = "readai"

    # Read.ai
    read_ai_api_key: str | None = None
    read_ai_base_url: str = "https://api.read.ai/v1"
    read_ai_webhook_secret: str | None = None  # HMAC signing key from Read.ai webhook page
    # Mailbox whose calendar Read.ai is connected to. Used to scope poll
    # queries and to match incoming webhook reports to a meeting_session.
    read_ai_organiser_email: str | None = None
    # How long after scheduled_at to start polling (gives meeting time to
    # actually finish + Read.ai time to generate the report).
    read_ai_poll_delay_seconds: int = 4200  # 70 min default
    read_ai_poll_max_attempts: int = 12
    read_ai_poll_interval_seconds: int = 300

    # Legacy Recall.ai (kept for back-compat / optional fallback)
    recall_ai_api_key: str | None = None
    recall_ai_base_url: str = "https://us-east-1.recall.ai/api/v1"
    recall_ai_webhook_secret: str | None = None
    recall_bot_display_name: str = "GrabOn AI Notetaker"
    recall_bot_join_lead_seconds: int = 120

    # MS Graph transcript fallback (uses graph_* creds above)
    graph_transcripts_enabled: bool = False

    # Gemini audio evaluation
    gemini_api_key: str | None = None  # GEMINI_API_KEY — enables post-call audio scoring via Gemini 2.0 Flash

    # Submission enrichment (GitHub + Loom analysis for assignments)
    github_token: str | None = None
    loom_api_key: str | None = None

    # Feature flags
    auto_approve_green_tier: bool = False
    enable_whatsapp: bool = True
    enable_sms_reminders: bool = True
    enable_voice_screening: bool = False
    enable_voice_calls: bool = False
    inbound_call_mode: Literal["live_agent", "sms_callback"] = "sms_callback"
    voice_campaign_max_concurrent: int = 10
    voice_campaign_dispatch_rate_per_minute: int = 5
    enable_assessment_round: bool = False
    enable_meeting_analysis: bool = False
    enable_ceo_dashboard: bool = False
    pi_tests_enabled: bool = False  # When True, assignment emails include PI cognitive + personality test links if set on the role

    # Evidence & provenance layer (Phase 1). When enabled, pipeline activities
    # write EvidenceRecord + DecisionRecord rows alongside audit_log entries.
    enable_evidence_collection: bool = False

    # Cross-stage fact verification (Phase 2). When enabled, new evidence is
    # compared against prior records for the same fact_key. Contradictions
    # create PipelineAlert rows and Teams notifications.
    enable_fact_verification: bool = False

    # Supervisor engine (Phase 3). Event-driven reasoning loop that handles
    # arbitrary pipeline scenarios. Shadow mode writes proposals only;
    # execute mode acts through the tool registry.
    enable_supervisor: bool = False
    supervisor_mode: Literal["shadow", "execute"] = "shadow"
    supervisor_max_actions_per_event: int = 5

    # Phase 5: LLM-based classifiers (voicemail, candidate intent).
    # When off, falls back to regex/heuristic detection.
    enable_llm_classifiers: bool = False

    # Phase 6: Confidence-driven gate removal. When enabled, non-finals
    # human gates can be auto-advanced if pipeline confidence >= threshold
    # AND the stage's autonomy level is full_auto.
    enable_confidence_gates: bool = False

    # Arq worker (replaces FastAPI BackgroundTasks for durable jobs).
    # Falls back to BackgroundTasks when ARQ_ENABLED is false so the API
    # keeps working even if the worker container is down.
    arq_enabled: bool = False
    arq_queue_name: str = "hiring-agent"
    arq_max_jobs: int = 10
    arq_callback_poll_interval_seconds: int = 60

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if self.jwt_signing_secret == "change_me_long_random_string":
                raise ValueError("JWT_SIGNING_SECRET must be changed from default in production")
            if len(self.jwt_signing_secret) < 32:
                raise ValueError("JWT_SIGNING_SECRET must be >= 32 characters in production")
        return self


def _build_settings() -> Settings:
    """Build a Settings object from env defaults only. The runtime overlay
    (DB-backed admin edits) is applied by ``services.config_store.get_settings``.

    Direct callers of this function bypass the overlay — useful for the
    bootstrap path (Alembic env, the overlay loader itself) where the DB is
    not yet reachable.
    """
    return Settings()  # type: ignore[call-arg]


def get_settings() -> Settings:
    """Return current effective settings (env defaults overlaid by DB).

    Delegates to ``services.config_store`` when available. Falls back to env
    defaults during very early init / migrations.
    """
    try:
        from src.services.config_store import get_settings as _overlay_get  # local import to avoid cycle
        return _overlay_get()
    except Exception:  # noqa: BLE001
        return _build_settings()
