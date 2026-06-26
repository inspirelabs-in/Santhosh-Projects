"""OpenAI-format tool schemas exposed to the recruiter agent."""

from __future__ import annotations

from src.recruiter_agent.prompts import QUICK_REPLIES_TOOL as _QR

RECRUITER_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_candidates",
            "description": "List recent candidate applications. Filter by stage, role, recency. Use when the user asks to see candidates, applicants, the pipeline, or recent applications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": "Pipeline stage to filter by (e.g. applied, screening_sent, screening_evaluated, assignment_sent, assignment_submitted, hired, rejected, needs_hr_review).",
                    },
                    "role_id": {"type": "string", "description": "Role UUID to filter by."},
                    "days_since_applied": {
                        "type": "integer",
                        "description": "Only show candidates who applied within the last N days.",
                    },
                    "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 100},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_candidate",
            "description": "Full candidate detail: stage, screening summary, assignment, links. Use when the user asks for a specific candidate's status / details / journey.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string", "description": "Application UUID."}
                },
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_roles",
            "description": "List open roles + applicant counts. Use when the user asks about roles / job openings / postings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "open | closed | draft"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_role",
            "description": "Read a role by id — returns title, status, pipeline stages, assignment brief + doc state, deadline, evaluation spec. Use when the user wants to see or edit a specific role's assignment or evaluation configuration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string", "description": "UUID of the role"},
                },
                "required": ["role_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline_metrics",
            "description": "Counts of applications per stage + last-7-day inflow. Use for dashboard-style overviews ('what does the pipeline look like?', 'how many candidates are stuck?').",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stuck_applications",
            "description": "Applications that haven't moved in N hours. Use for 'who's stuck?', 'what needs attention?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "default": 48, "minimum": 1},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_tail",
            "description": "Recent audit-log entries. Use for 'what has the agent done?', 'show recent activity', 'why did X happen?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string", "description": "Filter to one application."},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_talent_pool",
            "description": "Semantic search across the talent pool (vector similarity). Use when the recruiter asks 'find candidates who know X', 'who in our pool matches this role', 'similar candidates to...'. Much richer than keyword search_candidates — uses embeddings to find skill/experience matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query (skills, role description, experience)."},
                    "role_id": {"type": "string", "description": "Optional role UUID — matches candidates against the role's JD instead of free-text query."},
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        },
    },
    # ---------------- Phase 1 write tools ----------------
    {
        "type": "function",
        "function": {
            "name": "search_candidates",
            "description": "Free-text search over candidate name / email / role title. Use when the user names someone or partially identifies them.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 25}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_role",
            "description": "Create a new open role. Use when the user asks to add a role, post a job, onboard a position. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "jd_text": {"type": "string", "description": "Job description, plain text or markdown."},
                    "ctc_min_lpa": {"type": "number"},
                    "ctc_max_lpa": {"type": "number"},
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string", "enum": ["onsite", "hybrid", "remote"]},
                    "max_notice_days": {"type": "integer"},
                    "screening_modality": {"type": "string", "default": "voice"},
                    "evaluation_spec": {"type": "object", "description": "Evaluation dimensions and knockouts for fit scoring. Include dimension keys, weights, rubrics."},
                    "company_context": {"type": "object", "description": "Role-specific grounding context — what matters here, hiring bar."},
                    "pipeline_template": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered pipeline step IDs. Must end with 'offer'. Use preset names or custom list.",
                    },
                },
                "required": ["title", "jd_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_role",
            "description": "Patch an existing role. Use for edits to JD, CTC, location, etc. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "title": {"type": "string"},
                    "jd_text": {"type": "string"},
                    "ctc_min_lpa": {"type": "number"},
                    "ctc_max_lpa": {"type": "number"},
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string", "enum": ["onsite", "hybrid", "remote"]},
                    "max_notice_days": {"type": "integer"},
                    "screening_modality": {"type": "string"},
                    "status": {"type": "string", "enum": ["open", "closed", "draft"]},
                },
                "required": ["role_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_role",
            "description": "Close a role (status -> closed). Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"role_id": {"type": "string"}},
                "required": ["role_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_role_assignment_brief",
            "description": "Set or replace the take-home assignment brief for a role. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "assignment_brief": {"type": "string"},
                    "assignment_instructions": {"type": "string"},
                    "assignment_deadline_days": {"type": "integer"},
                },
                "required": ["role_id", "assignment_brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "override_stage",
            "description": "HR override: force a candidate's pipeline stage (rejected, hired, advance, parking). Requires confirmation. Bypasses normal transition rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "to_stage": {"type": "string", "description": "PipelineStage enum value."},
                    "reason": {"type": "string"},
                },
                "required": ["application_id", "to_stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_custom_email",
            "description": "Send a one-off email to a candidate (re-engage, ask for clarification, etc.). Uses configured email channel. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "body_markdown": {"type": "string"},
                },
                "required": ["application_id", "subject", "body_markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_candidate_note",
            "description": "Append a recruiter note to an application's audit log. Use for private commentary HR wants to keep.",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}, "note": {"type": "string"}},
                "required": ["application_id", "note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_interview",
            "description": "Persist an interview slot HR has already agreed with the candidate. For finding slots from scratch use propose_slots first. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "scheduled_at": {"type": "string", "description": "ISO-8601 UTC timestamp."},
                    "meeting_link": {"type": "string"},
                },
                "required": ["application_id", "scheduled_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_slots",
            "description": "Generate candidate interview slots based on panel availability. Read-only; HR confirms one of the slots, then schedule_interview.",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}, "count": {"type": "integer", "default": 3}},
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_meeting_slots",
            "description": "Recommend interview slots over the next few business days (Mon-Fri only). Read-only. Use this to offer the recruiter time options before scheduling or rescheduling a meeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_days": {"type": "integer", "default": 5, "description": "How many business days ahead to cover."},
                    "limit": {"type": "integer", "default": 6, "description": "Max number of slots to return."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_meeting",
            "description": "Book an interview meeting (Google Meet) for a candidate: creates the calendar event, emails the candidate and panel an invite, and arms the recording bot. Use when the recruiter gives a candidate, a round, a time, and panel emails. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string", "description": "The candidate's application id."},
                    "round": {"type": "string", "enum": ["technical", "ceo", "hr"], "description": "Interview round."},
                    "scheduled_at": {"type": "string", "description": "ISO-8601 start time, ideally with timezone offset, e.g. 2026-06-23T15:00:00+05:30."},
                    "panel_emails": {"type": "array", "items": {"type": "string"}, "description": "Email addresses of the interview panel members."},
                    "duration_minutes": {"type": "integer", "default": 45},
                },
                "required": ["application_id", "round", "scheduled_at", "panel_emails"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_meeting",
            "description": "Move an existing interview meeting to a new time and send fresh invites to everyone. Identify the meeting by meeting_session_id, or by application_id (plus round if known). Use this when a candidate requested a reschedule. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_scheduled_at": {"type": "string", "description": "ISO-8601 new start time, ideally with timezone offset."},
                    "application_id": {"type": "string", "description": "The candidate's application id (if meeting_session_id is unknown)."},
                    "meeting_session_id": {"type": "string", "description": "The meeting session id, if known."},
                    "round": {"type": "string", "enum": ["technical", "ceo", "hr"], "description": "Round to disambiguate when only application_id is given."},
                    "duration_minutes": {"type": "integer", "description": "Optional; defaults to the original duration."},
                    "panel_emails": {"type": "array", "items": {"type": "string"}, "description": "Optional; defaults to the original panel."},
                },
                "required": ["new_scheduled_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_panel_member",
            "description": "Assign a panel member to a role for a given round (technical | hr | ceo). Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "panel_member_id": {"type": "string"},
                    "round": {"type": "string", "enum": ["technical", "hr", "ceo"]},
                },
                "required": ["role_id", "panel_member_id", "round"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_panel_member",
            "description": "Add a new interviewer to the workspace panel directory. Use when HR mentions a new team member who should conduct interviews. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name"},
                    "email": {"type": "string", "description": "Work email"},
                    "role_type": {"type": "string", "enum": ["technical", "hr", "ceo"], "description": "Panel role type"},
                    "job_title": {"type": "string", "description": "e.g. Senior Backend Engineer"},
                    "expertise_tags": {"type": "array", "items": {"type": "string"}, "description": "Skills/expertise areas"},
                    "department": {"type": "string", "description": "e.g. Engineering, Product, Design"},
                    "seniority_level": {"type": "string", "enum": ["junior", "mid", "senior", "lead", "executive"]},
                },
                "required": ["name", "email", "role_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_candidates",
            "description": "(duplicate dropped, see above)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journey_report",
            "description": "Return the full markdown journey report for an application (built post-screening / interviews).",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "metrics_period",
            "description": "Funnel for the last N days. For the all-time pipeline use pipeline_metrics.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 30}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_meetings",
            "description": "Recent / upcoming Teams or in-person interview meetings.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 14}, "limit": {"type": "integer"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_voice_calls",
            "description": "Recent AI phone-screen voice calls.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 14}, "limit": {"type": "integer"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_audit",
            "description": "Audit log for ONE application (deeper than audit_tail).",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}, "limit": {"type": "integer", "default": 30}},
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_setting",
            "description": "Admin-only. Write a runtime config-settings key. Use sparingly. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {}},
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_attachment",
            "description": "Parse a PDF/DOCX/text file (by R2 file_ref returned from /upload) or fetch + clean a whitelisted URL. Returns the extracted text the agent can feed to create_role / get_candidate / etc.",
            "parameters": {
                "type": "object",
                "properties": {"file_ref": {"type": "string"}, "url": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Long-term per-recruiter memory write. Use ONLY when the user explicitly asks you to remember something or makes a clear standing preference. NEVER auto-remember chat content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "stable.dotted.key"},
                    "value": {},
                    "scope": {"type": "string", "default": "self"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_role_with_assignment",
            "description": "Create a role. Does NOT auto-generate a take-home assignment — the assignment is created separately via generate_assignment_for_role (or the recruiter uploads a PDF). If the recruiter explicitly described a brief, pass it as 'brief' (stored as text only). If the role's pipeline has an assignment stage but no PDF yet, the role is held as a draft until one is added; otherwise it goes live.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "jd_text": {"type": "string"},
                    "n_problems": {"type": "integer", "default": 2, "minimum": 1, "maximum": 8},
                    "ctc_min_lpa": {"type": "number"},
                    "ctc_max_lpa": {"type": "number"},
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string", "enum": ["onsite", "hybrid", "remote"]},
                    "max_notice_days": {"type": "integer"},
                    "screening_modality": {"type": "string", "default": "voice"},
                    "evaluation_spec": {"type": "object", "description": "Evaluation dimensions and knockouts for fit scoring. Include dimension keys, weights, rubrics."},
                    "company_context": {"type": "object", "description": "Role-specific grounding context — what matters here, hiring bar."},
                    "pipeline_template": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of pipeline step IDs. Presets: standard_engineer, senior_engineer, intern, executive, referral, contract, campus, internal_transfer, rehire. Or custom list from: fit_score, voice_screen, assignment, cognitive_test, technical_interview, hiring_manager, ceo_interview, hr_interview, panel_interview, bar_raiser, reference_check, background_check, offer. Must end with 'offer'.",
                    },
                    "time_budget_hours": {"type": "integer", "default": 6},
                    "deadline_days": {"type": "integer", "default": 7},
                    "brief": {"type": "string", "description": "Optional recruiter-provided assignment brief text. Stored as text only (no PDF). The assignment PDF is generated separately via generate_assignment_for_role."},
                },
                "required": ["title", "jd_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_assignment_for_role",
            "description": "YOU MUST CALL THIS TOOL to create a take-home assignment; it is JD-grounded from the role. Generates a full assignment AND persists it as a PDF by default (save=true). Only use save=false when the recruiter explicitly asks for a text preview first. If the recruiter described specific requirements in chat (problem types, focus areas, technologies, difficulty), pass those as user_brief — they become MANDATORY requirements that override the JD. Default n_problems=2, time_budget_hours=6, deadline_days=7.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string", "description": "UUID of the role. Look in the conversation history — it is in the tool result of the create_role or apply_role_draft call (field: role_id or id)."},
                    "n_problems": {"type": "integer", "default": 2, "minimum": 1, "maximum": 8},
                    "time_budget_hours": {"type": "integer", "default": 6},
                    "deadline_days": {"type": "integer", "default": 7},
                    "save": {"type": "boolean", "default": True, "description": "true = generate AND persist as PDF (correct default). false = text preview only, nothing saved — use only when recruiter explicitly asks for preview first."},
                    "user_brief": {"type": "string", "description": "Any specific assignment requirements, problem types, technologies, or focus areas the recruiter mentioned in this conversation. Pass verbatim — these override the JD and become mandatory constraints for problem generation."},
                },
                "required": ["role_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_linkedin_post",
            "description": "Draft a punchy founder-voice LinkedIn post for a role opening. Either role_id or role_title required. Optional 'angle' is a hook ('vibe coders', 'founder's office', etc). Returns {text, hashtags, char_count}. Does NOT publish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "role_title": {"type": "string"},
                    "angle": {"type": "string"},
                    "apply_url": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_linkedin_post",
            "description": "Publish a text post to LinkedIn on the configured author URN. Confirm-gated. Requires LINKEDIN_ACCESS_TOKEN + LINKEDIN_AUTHOR_URN env vars. Use draft_linkedin_post first to produce the body, then call this with the approved text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 80, "maxLength": 3000},
                    "visibility": {"type": "string", "enum": ["PUBLIC", "CONNECTIONS"], "default": "PUBLIC"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_defaults_for_role",
            "description": "Compute sensible defaults (CTC band, location, remote policy, screening modality, JD skeleton) for a new role based on seniority hints in the title and the closest existing role. Call this BEFORE create_role so you can fill in the create_role args without asking the user.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "List the recruiter's stored memory entries (optionally filtered by key prefix).",
            "parameters": {
                "type": "object",
                "properties": {"prefix": {"type": "string"}, "limit": {"type": "integer", "default": 50}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_role_draft",
            "description": (
                "Create or update the role draft and open the editable side panel. "
                "You MUST call this tool to produce a draft -- the draft and the panel "
                "do not exist until you do. Never tell the recruiter you drafted "
                "something without calling this in the same turn. Call it as soon as "
                "you have enough context (seniority, comp, location, must-haves); the "
                "system auto-generates the complete draft (title, JD, pipeline, "
                "evaluation spec, company context) from your conversation, so calling "
                "with NO arguments is the norm. Call again to revise the same artifact. "
                "Does NOT create the role; the user applies it from the panel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "jd_text": {"type": "string", "description": "Full JD, markdown."},
                    "ctc_min_lpa": {"type": "number"},
                    "ctc_max_lpa": {"type": "number"},
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string", "enum": ["onsite", "hybrid", "remote"]},
                    "max_notice_days": {"type": "integer"},
                    "pipeline": {
                        "type": "array",
                        "description": "Ordered stages. Each: {stage_key, stage_type, label, position, mode (auto|manual), is_enabled}. stage_type in: intake, parse, fit, screening, voice_screen, assignment, interview, decision, offer. The assignment stage auto-sends the take-home and then parks the candidate for HR review on submit (there is no separate review stage). Multiple 'interview' stages allowed (e.g. technical, ceo, hr).",
                        "items": {"type": "object"},
                    },
                    "evaluation_spec": {
                        "type": "object",
                        "description": "Role-specific scoring criteria derived from the ideal-candidate context. {dimensions: [{key, label, weight (sum ~100), what_good_looks_like[], anti_signals[]}], knockouts: [{key, rule}]}. Do NOT use generic 'builder mindset' style defaults unless the role truly calls for it.",
                    },
                    "company_context": {
                        "type": "object",
                        "description": "Role-tuned grounding context generated from the company persona + THIS role. {summary, what_matters_here[], hiring_bar}. This grounds every later stage (fit, voice, interviews).",
                    },
                    "assignment": {
                        "type": "object",
                        "description": "{enabled, brief, n_problems, time_budget_hours, deadline_days}. brief: the recruiter's verbatim assignment requirements from the conversation (problem types, technologies, features, focus areas). Capture any assignment ideas the recruiter described here — they override the JD during generation. Leave as '' if not mentioned.",
                    },
                    "notes": {"type": "string"},
                },
                "required": [],
            },
        },
    },
]


# Drop the duplicate placeholder schema (some servers reject duplicates).
RECRUITER_TOOLS = [t for i, t in enumerate(RECRUITER_TOOLS) if not (
    t.get("function", {}).get("name") == "search_candidates"
    and t["function"].get("description", "").startswith("(duplicate")
)]

RECRUITER_TOOLS.append({
    "type": "function",
    "function": {
        "name": _QR["name"],
        "description": _QR["description"],
        "parameters": _QR["input_schema"],
    },
})
