# Prompts Reference — Hiring Agent

Every LLM prompt in the system: when it fires, what goes in, what comes out.

---

## Prompt Flow Overview

```
Career Form / Email
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. CLASSIFY_EMAIL_V1       (fast model, gpt-4o-mini)               │
│    Only for email-sourced apps. Decides "is this an application?"   │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. PARSE_RESUME_V1         (fast model, Claude Haiku)              │
│    Extract structured profile from resume text                      │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. FIT_SCORE_V1            (smart model, Claude Sonnet / gpt-4o)   │
│    Score profile vs JD → tier (GREEN / AMBER / RED)                │
└─────────────────────────────────────────────────────────────────────┘
    │
    ├── V1 Form Path ─────────────────────────────────────────────────┐
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │ 4a. SCREENING_GEN_V1    (fast model)                     │   │
    │  │     Generate 5-7 tailored questions                      │   │
    │  └──────────────────────────────────────────────────────────┘   │
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │ 5a. SCREENING_EVAL_V1    (smart model)                   │   │
    │  │     Evaluate submitted answers → verdict                 │   │
    │  │     + SCORE_OPEN_TEXT_V1 (per open-text answer)          │   │
    │  └──────────────────────────────────────────────────────────┘   │
    │                                                                  │
    ├── V2 Chat Path ─────────────────────────────────────────────────┐│
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 4b. TAILORED_QS_V1       (fast, prewarm)                 │   ││
    │  │     Generate 2 tailored open-text questions              │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 5b. CHAT_TURN_SYSTEM_V1   (smart, per-turn streaming)    │   ││
    │  │     Conversational screening + assignment delivery       │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 6b. EXTRACT_TURN_V1       (fast, per-turn)               │   ││
    │  │     Extract structured fields from candidate's message   │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 7b. ASSIGNMENT_GEN_V1     (smart, on transition)         │   ││
    │  │     Generate personalized assignment brief               │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │                                                                  │
    ├── Voice Screen Path ────────────────────────────────────────────┐│
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 4c. VOICE_SCREEN_GEN_V1    (fast model)                  │   ││
    │  │     Generate 5 spoken questions for AI agent             │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │  ┌──────────────────────────────────────────────────────────┐   ││
    │  │ 5c. VOICE_SCREEN_EVAL_V1   (smart model)                 │   ││
    │  │     Evaluate post-call transcript + paralinguistic       │   ││
    │  └──────────────────────────────────────────────────────────┘   ││
    │                                                                  │
    └──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 8. ASSIGNMENT_PARSE_V1     (fast model)                             │
│    Parse candidate's assignment submission (extract signal, no grade)│
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 9. JOURNEY_REPORT_V1       (smart model)                           │
│    Generate HR-facing markdown journey report                       │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼ (if interview round)
┌─────────────────────────────────────────────────────────────────────┐
│ 10a. MEETING_ANALYSIS_V1   (smart model)                           │
│     Analyze Teams interview transcript → scores + verdict          │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼ (if CEO round)
┌─────────────────────────────────────────────────────────────────────┐
│ 10b. CEO_BRIEF_V1          (smart model)                           │
│     Aggregate all rounds → CEO-facing markdown brief               │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼ (if rejection)
┌─────────────────────────────────────────────────────────────────────┐
│ 11. REJECTION_MESSAGE_V1   (fast model)                            │
│     Draft respectful rejection email body                          │
└─────────────────────────────────────────────────────────────────────┘

Recruiter-side (on demand):
┌─────────────────────────────────────────────────────────────────────┐
│ 12. ROLE_DRAFTING PROMPTS  (smart model)                           │
│     Conversational role creation + LinkedIn post generation        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Prompt Catalog

---

### 1. CLASSIFY_EMAIL_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/classify_email.py` |
| **Version** | `v1` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | Stage 2 — every inbound email before intake. Only for email-sourced applications. |
| **Invoked by** | `activities/classify.py` → `classify_activity` |
| **Pipeline stage** | `classified` |
| **Extraction** | Pydantic (`response_model=ClassificationResult`) |

**Purpose:** Determine if an inbound email is a job application, which role it targets, and confidence level.

**Input format placeholders:**
```
- {open_roles_list}     — list of currently open role titles
- {subject}             — email subject line
- {sender_email}        — sender's email address
- {body_text}           — email body
- {attachment_filenames}— list of attachment filenames
```

**Output structure:**
```json
{
  "is_application": true,
  "confidence": 0.95,
  "detected_role": "Senior Backend Engineer",
  "detected_role_confidence": 0.88,
  "is_referral": false,
  "referrer_indicators": null,
  "reasoning": "Email attaches a resume and mentions applying for the backend role posted on LinkedIn.",
  "flags": []
}
```

**Routing logic:**
- `is_application=false` → message archived (no application created)
- `confidence < 0.7` → `needs_hr_review`
- `detected_role=Unknown` → `needs_hr_review`
- `is_application=true, confidence >= 0.7` → continue to intake

---

### 2. PARSE_RESUME_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/parse_resume.py` |
| **Version** | `v2` |
| **Model** | Fast (Claude Haiku / gpt-4o-mini) |
| **When invoked** | Stage 3 — after intake, resume uploaded to R2 and text extracted |
| **Invoked by** | `activities/parse_resume.py` → `parse_resume_activity` |
| **Pipeline stage** | `resume_parsed` |
| **Extraction** | Pydantic (`response_model=CandidateProfile`) |

**Purpose:** Extract a fully structured `CandidateProfile` from raw resume text (PDF, DOCX, image via OCR).

**Input format placeholders:**
```
- {resume_text}          — raw text extracted from resume file
```

**Output structure (37+ fields):**
```json
{
  "name": "Rahul Sharma",
  "email": "rahul.sharma@email.com",
  "phone": "+919876543210",
  "linkedin_url": "https://linkedin.com/in/rahul-sharma",
  "github_url": "https://github.com/rahulsharma",
  "portfolio_url": null,
  "location": "Bangalore, Karnataka",
  "current_role": "Senior Software Engineer",
  "current_company": "TechCorp India",
  "current_ctc_lpa": 24.0,
  "expected_ctc_lpa": 30.0,
  "notice_period_days": 60,
  "availability": null,
  "preferred_work_mode": "hybrid",
  "willing_to_relocate": true,
  "references_available": null,
  "total_years_experience": 5.5,
  "headline": "Senior Backend Engineer · Go / Postgres / AWS",
  "summary": "Experienced backend engineer with 5+ years building scalable microservices...",
  "education": [
    {"degree": "B.Tech Computer Science", "institution": "IIT Hyderabad", "year": 2019}
  ],
  "skills": ["Go", "PostgreSQL", "Redis", "Kafka", "Kubernetes", "Docker", "AWS"],
  "tools": ["GitHub Actions", "Terraform", "Grafana"],
  "soft_skills": ["Cross-team collaboration"],
  "domains": ["Fintech", "E-commerce"],
  "work_history": [
    {
      "company": "TechCorp India",
      "role": "Senior Software Engineer",
      "duration": "2021-present",
      "highlights": ["Built payment orchestration layer handling 50k TPS"]
    }
  ],
  "projects": [],
  "achievements": [],
  "publications": [],
  "patents": [],
  "open_source": [],
  "certifications": ["AWS Solutions Architect Associate"],
  "languages": [{"language": "English", "proficiency": "fluent"}, {"language": "Hindi", "proficiency": "native"}],
  "volunteer_experience": [],
  "extracurriculars": [],
  "field_confidence": {
    "name": 1.0, "email": 1.0, "phone": 1.0,
    "current_ctc_lpa": 0.9, "notice_period_days": 0.8,
    "total_years_experience": 0.95, "skills": 1.0
  }
}
```

---

### 3. FIT_SCORE_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/fit_score.py` |
| **Version** | `v2` |
| **Model** | Smart (Claude Sonnet / gpt-4o) |
| **When invoked** | Stage 4 — after resume parsed, before screening |
| **Invoked by** | `activities/fit_score.py` → `fit_score_activity` |
| **Pipeline stage** | `fit_scored` |
| **Extraction** | Pydantic (`response_model=FitAssessment`) |

**Purpose:** Score the candidate against the job description across 4 weighted dimensions. Produce a tier for routing.

**Input format placeholders:**
```
- {jd_text}                — full job description
- {role_title}             — role title
- {ctc_min_lpa}            — min CTC band
- {ctc_max_lpa}            — max CTC band
- {max_notice_days}        — max allowed notice period
- {role_location}          — location string
- {remote_policy}          — onsite / hybrid / remote
- {candidate_profile_json} — parsed CandidateProfile as JSON
- {skills_weight}          — dimension weight (default 40)
- {experience_weight}      — dimension weight (default 25)
- {ctc_weight}             — dimension weight (default 20)
- {logistics_weight}       — dimension weight (default 15)
```

**Output structure:**
```json
{
  "overall_score": 78,
  "dimensions": {
    "skills_match": {
      "score": 82,
      "rationale": "Strong Go backend experience with relevant stack",
      "evidence": ["5+ years Go", "Built payment orchestration with Kafka"],
      "missing_skills": ["GraphQL"]
    },
    "experience_level": {
      "score": 75,
      "rationale": "5.5 yrs relevant, steady progression",
      "evidence": ["Senior SE at TechCorp since 2021"],
      "trajectory": "accelerating"
    },
    "ctc_fit": {
      "score": 70,
      "rationale": "Expected 30 LPA within 25-35 range, acceptable",
      "evidence": ["expected_ctc_lpa: 30"]
    },
    "location_notice_fit": {
      "score": 85,
      "rationale": "Bangalore, willing to relocate, 60d notice acceptable",
      "evidence": ["location: Bangalore", "willing_to_relocate: true"]
    }
  },
  "red_flags": ["No GraphQL experience for a role requiring it"],
  "green_flags": ["Payment domain experience directly relevant"],
  "skill_gap_analysis": {
    "required_and_present": ["Go", "PostgreSQL", "Kafka", "AWS"],
    "required_and_missing": ["GraphQL"],
    "bonus_skills": ["Terraform"]
  },
  "recommended_tier": "green",
  "summary": "Strong backend candidate with relevant domain experience. Main gap is GraphQL."
}
```

**Tier routing:**
- `recommended_tier=green` → auto-advance to screening
- `recommended_tier=amber` → `needs_hr_review`
- `recommended_tier=red` → auto-reject (if `auto_reject_clear_reject` enabled) or `needs_hr_review`

---

### 4a. SCREENING_GEN_V1 (V1 Form Path)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/screening_gen.py` |
| **Version** | `v2` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | After fit score (V1 form screening path only) |
| **Invoked by** | `activities/v1_generate_screening.py` (runs as BackgroundTask) |
| **Pipeline stage** | `screening_sent` (questions generated before email sent) |
| **Extraction** | Pydantic (`response_model=GeneratedScreeningSet`) |

**Purpose:** Generate 5-7 tailored screening questions for one candidate, mixing skill probes, logistics, open-text, and gap probes.

**Input format placeholders:**
```
- {role_title}             — role title
- {jd_text}                — job description
- {ctc_min_lpa}            — min CTC band
- {ctc_max_lpa}            — max CTC band
- {max_notice_days}        — max allowed notice period
- {role_location}          — location string
- {remote_policy}          — onsite / hybrid / remote
- {candidate_profile_json} — parsed CandidateProfile as JSON
```

**Output structure:**
```json
{
  "questions": [
    {
      "id": "q1",
      "question": "You built a payment orchestration layer at TechCorp handling 50k TPS. Walk me through the architecture — how did you handle idempotency and failure recovery?",
      "type": "skill_probe",
      "expected_signal": "Deep understanding of distributed systems trade-offs, idempotency keys, retry strategies",
      "required": true
    },
    {
      "id": "q2",
      "question": "What is your current CTC (in LPA) and what are you expecting for this role?",
      "type": "logistics",
      "expected_signal": "Clear numbers within or near the band",
      "required": true
    },
    {
      "id": "q3",
      "question": "What is your current notice period, and when could you join?",
      "type": "logistics",
      "expected_signal": "Specific duration and start date",
      "required": true
    },
    {
      "id": "q4",
      "question": "You're based in Bangalore. The role is Hyderabad (onsite). Are you open to relocating?",
      "type": "logistics",
      "expected_signal": "Clear yes/no with any conditions",
      "required": true
    },
    {
      "id": "q5",
      "question": "The role requires GraphQL experience, which isn't on your resume. Tell me about any exposure you have with GraphQL — even personal projects or POCs.",
      "type": "gap_probe",
      "expected_signal": "Honest assessment of familiarity level; learning agility if no production experience",
      "required": true
    }
  ]
}
```

---

### 5a. SCREENING_EVAL_V1 (V1 Form Path)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/screening_eval.py` |
| **Version** | `v3` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | Candidate submits screening form → `POST /apply/{token}/screening` |
| **Invoked by** | `activities/v1_evaluate_screening.py` |
| **Pipeline stage** | `screening_evaluated` |
| **Extraction** | Pydantic (`response_model=ScreeningEvaluation`) |

**Purpose:** Evaluate screening responses, perform contradiction checks, produce verdict.

**Input format placeholders:**
```
- {role_title}             — role title
- {jd_text}                — job description
- {ctc_min_lpa}            — min CTC band
- {ctc_max_lpa}            — max CTC band
- {max_notice_days}        — max allowed notice period
- {candidate_profile_json} — parsed CandidateProfile as JSON
- {questions_json}         — the questions that were asked (with expected_signal)
- {responses_json}         — the candidate's answers
```

**Output structure:**
```json
{
  "overall_score": 72,
  "per_question": [
    {"question_id": "q1", "score": 8, "relevance": "high", "notes": "Detailed architecture walkthrough with specific tools named"},
    {"question_id": "q2", "score": 10, "relevance": "high", "notes": "CTC numbers clearly stated: 24 current, 30 expected"},
    {"question_id": "q5", "score": 4, "relevance": "medium", "notes": "Admits no GraphQL experience, mentions exposure to Apollo Client in a side project"}
  ],
  "logistics_check": {
    "ctc_in_range": true,
    "notice_acceptable": true,
    "location_workable": true,
    "rationale": "30 LPA expected within 25-35 band, 60d notice accepted, willing to relocate"
  },
  "logistics_values": {
    "current_ctc_lpa": 24.0,
    "expected_ctc_lpa": 30.0,
    "notice_period_days": 60,
    "current_location": "Bangalore",
    "willing_to_relocate": true
  },
  "red_flags": ["resume_screening_mismatch: claims GraphQL experience in screening but resume has none"],
  "strengths": ["Deep payment domain expertise", "Honest about skill gaps"],
  "verdict": "clear_pass",
  "verdict_rationale": "Strong overall with relevant domain depth. CTC and logistics fit. Main gap (GraphQL) acknowledged honestly."
}
```

**Verdict rules:**
- `clear_pass`: score >= 75 AND no red_flags AND logistics all true
- `clear_reject`: score < 40 OR 2+ logistics flags false
- `needs_hr_review`: everything else

---

### 5b. SCORE_OPEN_TEXT_V1 (sub-prompt of V1 evaluation)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/score_open_text.py` |
| **Version** | `v1` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | Per open-text question during V1 screening scoring (Stage 6) |
| **Invoked by** | `activities/score_responses.py` → `score_responses_activity` |
| **Extraction** | Pydantic (`response_model=OpenTextScore`) |

**Purpose:** Score one open-text screening answer (0-10) with rationale.

**Input format placeholders:**
```
- {question_text}        — the question asked
- {rubric_description}   — expected quality indicators
- {answer_text}          — candidate's answer
```

**Output structure:**
```json
{
  "score": 7,
  "rationale": "Good specific example of handling payment failures with retry logic, but lacks concrete numbers on throughput or error rates",
  "evidence_quality": "moderate",
  "flags": []
}
```

---

### 4b. TAILORED_QS_V1 (V2 Chat Path)

| Property | Value |
|----------|-------|
| **File** | `src/agent/prompts/tailored_qs.py` |
| **Version** | `v1` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | At `chat_invite.py` prewarm — before first chat message. Also reused if needed later |
| **Invoked by** | `agent/generators.py` → `gen_tailored_questions()` |
| **Extraction** | Pydantic (`response_model=TailoredQuestionsOut`) |

**Purpose:** Generate exactly 2 open-ended, resume-specific questions (not logistics — those are fixed fields in chat flow).

**Input format placeholders:**
```
- {role_title}             — role title
- {jd_text}                — job description
- {candidate_profile_json} — parsed CandidateProfile as JSON
```

**Output structure:**
```json
{
  "questions": [
    {
      "id": "q1",
      "question": "Your resume mentions building a payment orchestration layer handling 50k TPS. Walk me through how you designed for idempotency and what happened when a downstream gateway timed out.",
      "tied_to_resume": "Payment orchestration at TechCorp with 50k TPS throughput",
      "tied_to_jd": "JD requires 'experience with distributed transaction processing'",
      "expected_signal": "Deep knowledge of idempotency keys, retry strategies, circuit breakers"
    },
    {
      "id": "q2",
      "question": "You've used both Kafka and Redis at work. Tell me about a decision where you chose one over the other — what was the trade-off and did you regret it?",
      "tied_to_resume": "Skills section lists both Kafka and Redis",
      "tied_to_jd": "JD requires 'strong event-driven architecture skills'",
      "expected_signal": "Understanding of different consistency/throughput/ordering guarantees"
    }
  ]
}
```

---

### 5b. CHAT_TURN_SYSTEM_V1 (V2 Chat Path)

| Property | Value |
|----------|-------|
| **File** | `src/agent/prompts/chat_turn.py` |
| **Version** | `v1` |
| **Model** | Smart (gpt-4o), streaming |
| **When invoked** | Every chat turn — system prompt for the conversational agent |
| **Invoked by** | `agent/runner.py` via `agent/graph.py` → `node_reply()` |
| **Pipeline stage** | `screening_sent` through `submitted` |
| **Extraction** | Raw text (streamed via `stream_chat()`, no `response_model`) |

**Purpose:** Drive the entire candidate-facing chat conversation. One question per turn, warm but tight. State machine-driven via injected `stage`, `already_captured_json`, `pending_tailored_json`, `next_field`.

**Input format placeholders:**
```
- {company_name}          — company name (e.g., "GrabOn")
- {role_title}            — role title
- {stage}                 — current stage: intake | screening | assignment | submitted
- {already_captured_json} — JSON of fields already collected
- {pending_tailored_json} — JSON of unanswered tailored questions (q1/q2)
- {next_field}            — next field to ask: q1|q2|ctc_current|ctc_expected|notice|relocate|none
- {role_location}         — role location for relocate question
- {review_sla_days}       — days for review turnaround
```

**Output:** Streamed plain conversational text. No structured JSON. The model is instructed to output natural language replies, one question per turn.

**Flow control (state-driven):**
- Stage `intake`/`screening`: ask pending fields in order — q1 → q2 → ctc_current → ctc_expected → notice → relocate → none (transition to assignment)
- Stage `assignment`: deliver brief orientation (UI renders the structured assignment card)
- Stage `submitted`: confirm receipt and next steps

---

### 6b. EXTRACT_TURN_V1 (V2 Chat Path)

| Property | Value |
|----------|-------|
| **File** | `src/agent/prompts/extract.py` |
| **Version** | `v1` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | After every user chat message, before the reply generation |
| **Invoked by** | `agent/graph.py` → `node_extract()` |
| **Pipeline stage** | Any chat stage |
| **Extraction** | Pydantic (`response_model=ExtractedTurn`) |

**Purpose:** Pull structured screening fields from the candidate's free-form chat message. Idempotent — only emits fields clearly stated, never re-extracts already-captured fields.

**Input format placeholders:**
```
- {already_captured_json}  — JSON of fields already collected (do not re-extract)
- {pending_questions_json} — JSON of unanswered tailored questions
- {candidate_message}      — the user's latest message
- {recent_history}         — last 4 turns of conversation context
```

**Output structure:**
```json
{
  "tailored_a1": "I built the payment orchestration layer using event sourcing with Kafka...",
  "tailored_a2": null,
  "current_ctc_lpa": 24.0,
  "expected_ctc_lpa": null,
  "notice_period_days": null,
  "willing_to_relocate": null,
  "intent": "answer",
  "next_question_to_ask": "ctc_expected"
}
```

**Fields:**
- `tailored_a1` / `tailored_a2`: verbatim answer text if the candidate addressed a pending tailored question
- `current_ctc_lpa` / `expected_ctc_lpa`: numeric LPA or null
- `notice_period_days`: integer days (converts "1 month" → 30, "immediate" → 0) or null
- `willing_to_relocate`: true / false / null
- `intent`: `answer` | `ask_clarification` | `out_of_scope` | `request_pause`
- `next_question_to_ask`: next field in order (q1 → q2 → ctc_current → ctc_expected → notice → relocate → none)

---

### 7b. ASSIGNMENT_GEN_V1 (V2 Chat Path)

| Property | Value |
|----------|-------|
| **File** | `src/agent/prompts/assignment.py` |
| **Version** | `v2.2` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | On first transition from screening → assignment stage in V2 chat |
| **Invoked by** | `agent/graph.py` → `node_maybe_gen_assignment()` |
| **Pipeline stage** | `assignment_sent` |
| **Extraction** | Pydantic (`response_model=AssignmentBriefOut`) |

**Purpose:** Generate a rich, multi-part take-home assignment document with 2 hard problems, scoring rubric, and submission requirements.

**Input format placeholders:**
```
- {role_title}              — role title
- {jd_text}                 — job description
- {candidate_profile_json}  — parsed CandidateProfile as JSON
- {screening_answers_json}  — screening answers if any
- {time_budget_hours}       — total time budget (2-8 hours)
- {deadline_days}           — submission deadline in days
```

**Output structure (large JSON, abbreviated here):**
```json
{
  "cover_title": "GrabOn | Senior Backend Engineer Challenge",
  "confidential_tag": "CONFIDENTIAL",
  "company_context": "GrabOn processes 10M+ daily deal activations...",
  "new_initiatives": "We are building a real-time deal recommendation engine...",
  "strategic_context": "Scalability and reliability are our top strategic levers...",
  "what_we_look_for": [
    "Ability to design for real-world failure modes, not just happy paths",
    "Product thinking — you understand why, not just how"
  ],
  "problems": [
    {
      "id": "p1",
      "title": "Idempotent Payment Reconciliation Service",
      "vertical": "Payments Reliability",
      "tags": ["Go", "Postgres", "Kafka", "Docker"],
      "difficulty": "Hard - 2.5 to 3 days",
      "challenge": "Design and implement a payment reconciliation service...",
      "why_it_matters": "Payment failures cost GrabOn 2% revenue...",
      "technical_requirements": [
        "Implement at-least-once delivery with idempotency keys",
        "Store reconciliation state in Postgres with proper indexing"
      ],
      "statement": "Build a service that ingests payment events from Kafka...",
      "expected_artifacts": ["src/", "README.md", "loom.mp4"],
      "what_to_submit": "Working code in a public GitHub repo",
      "evaluation_bullets": [
        "Does the design correctly handle duplicate events?",
        "Are failure modes documented in the README?"
      ],
      "tied_to_jd": "JD requires 'distributed transaction processing'",
      "estimated_minutes": 360
    }
  ],
  "submission_requirements": [
    "Public GitHub repo with clear folder structure",
    "README.md explaining architecture decisions + tradeoffs + how to run",
    "Loom video walkthrough (10-15 min) including at least one edge case demo"
  ],
  "evaluation_rubric": {
    "criteria": [
      {"name": "Technical Depth", "weight": 20, "description": "Correctness of core implementation..."},
      {"name": "Product Thinking", "weight": 20, "description": "Edge cases, error handling, monitoring..."},
      {"name": "Demo Quality", "weight": 20, "description": "Clarity of Loom walkthrough..."},
      {"name": "AI / Tool Usage", "weight": 20, "description": "Effective use of tools..."},
      {"name": "Code Quality & Documentation", "weight": 20, "description": "Readability, structure, README quality..."}
    ]
  },
  "brief_md": "## GrabOn | Senior Backend Engineer Challenge\n\nThis assignment tests...",
  "submission_format": {
    "type": "github_repo",
    "instructions": "Submit via the chat upload button or share your GitHub repo link",
    "deadline_days": 7
  }
}
```

---

### 4c. VOICE_SCREEN_GEN_V1 (Voice Path)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/voice_screening.py` |
| **Version** | `v3` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | Before an AI phone-screen call is dispatched |
| **Invoked by** | `activities/v1_voice_screening.py` → `dispatch_voice_screening()` |
| **Pipeline stage** | `voice_screen_scheduled` |
| **Extraction** | Pydantic (`response_model=_VoiceQuestionSet`) |

**Purpose:** Generate exactly 5 spoken questions (conversational, <25 words each) for the ElevenLabs AI agent to ask aloud during a phone call. First two are resume-specific deep-dives namedropping candidate projects.

**Input format placeholders:**
```
- {role_title}             — role title
- {jd_text}                — job description
- {ctc_min_lpa}            — min CTC band
- {ctc_max_lpa}            — max CTC band
- {max_notice_days}        — max allowed notice period
- {role_location}          — location string
- {remote_policy}          — onsite / hybrid / remote
- {candidate_profile_json} — parsed CandidateProfile as JSON
```

**Output structure:**
```json
{
  "questions": [
    {
      "id": "q1",
      "question": "Rahul, your resume mentions building a payment orchestration layer handling 50,000 transactions per second at TechCorp. Walk me through how you handled duplicate payments and what happened when a bank gateway went down.",
      "type": "skill_probe",
      "expected_signal": "Understanding of idempotency, retry strategies, circuit breakers",
      "follow_up_hint": "If they mention retries, ask about exponential backoff and dead-letter queues"
    },
    {
      "id": "q2",
      "question": "You also worked on migrating from a monolith to microservices. Tell me about one thing that broke during the migration and how you fixed it.",
      "type": "depth",
      "expected_signal": "Real experience with migration challenges",
      "follow_up_hint": "Probe on data consistency during migration"
    },
    {
      "id": "q3",
      "question": "What is your current CTC and what are you expecting for this role?",
      "type": "logistics",
      "expected_signal": "Clear numbers within or near band",
      "follow_up_hint": ""
    },
    {
      "id": "q4",
      "question": "What is your current notice period, and when would you be able to join?",
      "type": "logistics",
      "expected_signal": "Specific duration",
      "follow_up_hint": ""
    },
    {
      "id": "q5",
      "question": "You are based in Bangalore, but this role is in Hyderabad and requires being in office. Are you open to relocating?",
      "type": "logistics",
      "expected_signal": "Clear yes/no with any conditions",
      "follow_up_hint": ""
    }
  ]
}
```

---

### 5c. VOICE_SCREEN_EVAL_V1 (Voice Path)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/voice_screening.py` |
| **Version** | `v2` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | After voice call ends — webhook fires, transcript + emotion features ready |
| **Invoked by** | `activities/v1_evaluate_voice_call.py` → `evaluate_voice_call()` |
| **Pipeline stage** | `voice_screen_evaluated` |
| **Extraction** | Pydantic (`response_model=VoiceCallScore`) |

**Purpose:** Score the phone-screen transcript answer-by-answer, extract candidate facts for profile backfill, and produce verdict. Paralinguistic features from emotion-service are provided as optional context.

**Input format placeholders:**
```
- {role_title}             — role title
- {jd_text}                — JD excerpt
- {ctc_min_lpa}            — min CTC band
- {ctc_max_lpa}            — max CTC band
- {max_notice_days}        — max notice period
- {role_location}          — location
- {remote_policy}          — remote policy
- {answers_json}           — transcribed answers from the call
- {paralinguistic_json}    — emotion-service output (pitch, rate, pause, emotion label)
```

**Output structure:**
```json
{
  "overall_score": 68,
  "per_question": [
    {"question_id": "q1", "score": 75, "relevance": "high", "notes": "Good idempotency discussion with real examples"},
    {"question_id": "q3", "score": 90, "relevance": "high", "notes": "CTC 24 current / 30 expected, within band"}
  ],
  "red_flags": ["Hesitated significantly on migration question — answer lacked specifics"],
  "strengths": ["Strong payment domain knowledge", "Clear and confident on compensation"],
  "verdict": "needs_hr_review",
  "verdict_rationale": "Good domain match but migration experience seems overstated. HR should probe further.",
  "extracted_facts": {
    "current_ctc_lpa": 24.0,
    "expected_ctc_lpa": 30.0,
    "notice_period_days": 60,
    "current_location": "Bangalore",
    "preferred_location": "Hyderabad",
    "willing_to_relocate": true,
    "work_authorization": "Indian citizen",
    "total_experience_years": 5.5,
    "relevant_experience_years": 5.0,
    "current_employer": "TechCorp India",
    "current_title": "Senior Software Engineer",
    "highest_qualification": "B.Tech CSE",
    "primary_skills": ["Go", "PostgreSQL", "Kafka"],
    "languages_spoken": ["English", "Hindi"],
    "notes": "Candidate mentioned leading a team of 3 engineers on the payment platform"
  }
}
```

**Verdict routing:** same as SCREENING_EVAL_V1.

---

### 8. ASSIGNMENT_PARSE_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/assignment_parse.py` |
| **Version** | `v2` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | Candidate submits assignment → `POST /apply/{token}/assignment` |
| **Invoked by** | `activities/v1_parse_assignment.py` |
| **Pipeline stage** | transitions from `assignment_submitted` |
| **Extraction** | Pydantic (`response_model=AssignmentParseResult`) |

**Purpose:** Extract structured signal from the candidate's assignment submission. Does NOT grade — only surfaces completeness, quality signals, highlights, concerns for HR.

**Input format placeholders:**
```
- {role_title}             — role title
- {assignment_brief}       — what was asked
- {assignment_instructions}— format constraints
- {project_choice}         — which option the candidate picked
- {links_json}             — submitted links (GitHub, Loom)
- {deployed_url}           — live deployment URL (optional)
- {file_texts_json}        — extracted text from uploaded files
- {candidate_notes}        — free-text notes from candidate
```

**Output structure:**
```json
{
  "completeness": {
    "followed_instructions": true,
    "covered_requirements": [
      "Kafka event ingestion -> addressed with Go consumer",
      "Idempotency -> idempotency keys in Postgres"
    ],
    "missing_items": []
  },
  "quality_signals": {
    "depth": "high",
    "originality": "medium",
    "clarity": "high",
    "technical_rigor": "high"
  },
  "highlights": [
    "Clean architecture with proper separation of concerns",
    "Comprehensive error handling and retry logic",
    "Excellent README with architecture diagram"
  ],
  "concerns": [
    "No test files included — asked for but not present in repo"
  ],
  "evidence_quotes": [
    "README: 'Used idempotency keys stored in a dedicated table with unique constraint on (key, status)'"
  ],
  "suggested_hr_focus": [
    "Verify whether the loom video covers edge cases mentioned in the requirements",
    "Ask about testing strategy in follow-up"
  ],
  "summary": "Strong submission demonstrating solid system design and implementation skills. Code is production-quality with good documentation. Testing is the main gap."
}
```

---

### 9. JOURNEY_REPORT_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/journey_report.py` |
| **Version** | `v3` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | After assignment parsed (V1 form path) or at any point for report generation |
| **Invoked by** | `activities/v1_journey_report.py` |
| **Pipeline stage** | `report_ready` |
| **Extraction** | Pydantic (`response_model=_MarkdownOut`) |

**Purpose:** Generate a clean, one-page markdown journey report for HR with strict formatting rules (no bold, italic, tables, emoji — plain markdown with ## headings and - bullets).

**Input format placeholders:**
```
- {role_title}                 — role title
- {candidate_name}             — candidate name
- {candidate_email}            — candidate email
- {applied_at}                 — application timestamp
- {source_channel}             — application source
- {profile_summary}            — resume-parsed profile summary
- {screening_questions_json}   — questions asked
- {screening_responses_json}   — answers (verbatim)
- {screening_evaluation_json}  — evaluation scores + verdict
- {logistics_values_json}      — extracted logistics
- {assignment_summary_json}    — assignment parse result
- {current_stage}              — current pipeline stage
```

**Output:** Markdown string with sections: Quick Verdict, Summary, Profile Snapshot, Screening Highlights, Assignment Review, Risk Assessment, Recommendation for HR.

**Sample output:**
```markdown
## Quick Verdict
LEAN HIRE with deep payment domain expertise; GraphQL gap needs confirmation.

## Summary
Rahul Sharma is a Senior Backend Engineer with 5.5 years of experience at TechCorp India, currently in screening stage. Strong payment orchestration background aligns well with the role's requirements. Main concern is the absence of GraphQL experience in a role where it's listed as a must-have.

## Profile Snapshot
- Current role: Senior Software Engineer at TechCorp India
- Total experience: 5.5 years
- Top 3 skills relevant to the role: Go, PostgreSQL, Kafka
- Current CTC: 24 LPA
- Expected CTC: 30 LPA
- Notice period: 60 days
- Location: Bangalore, Karnataka

## Screening Highlights
- Strengths: Detailed architecture walkthrough of payment orchestration layer with specific tools and patterns. Honest self-assessment of skill gaps.
- Concerns: No GraphQL experience despite being listed as a requirement.
- Verdict: clear_pass

## Assignment Review
- Strong submission with clean architecture and excellent documentation.
- Comprehensive error handling and retry logic demonstrated.
- No test files included — asked for but absent from repo.

## Risk Assessment
- Resume-screening consistency: All claims in screening align with resume.
- Flight risk: Expected CTC (30 LPA) within role band. Notice period (60 days) acceptable.
- The no-test gap in the assignment is a minor concern.

## Recommendation for HR
- Next step: advance to technical interview.
- Follow-up questions: Can you walk us through your approach to testing? Have you used GraphQL in any capacity — even evaluation or POCs?
- Compliance or logistics flags: None.
```

---

### 10a. MEETING_ANALYSIS_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/meeting_analysis.py` |
| **Version** | `v1` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | Meeting bot webhook fires after interview completes |
| **Invoked by** | `activities/v1_meeting_analysis.py` |
| **Pipeline stage** | `technical_evaluated` / `ceo_meeting_completed` |
| **Extraction** | Pydantic (`response_model=MeetingAnalysis`) |

**Purpose:** Score the interview transcript across 4 dimensions, build emotion timeline, produce verdict. Used for both technical and CEO rounds (different `{round}` value).

**Input format placeholders:**
```
- {round}                  — "technical" | "ceo"
- {role_title}             — role title
- {jd_text}                — JD excerpt
- {scoring_rubric_json}    — rubric from role config
- {transcript_json}        — diarized transcript with speaker, timestamps, text
- {paralinguistic_json}    — emotion-service output (optional)
```

**Output structure:**
```json
{
  "technical_score": 82,
  "communication_score": 75,
  "confidence_score": 70,
  "overall_score": 76,
  "strengths": [
    "Deep understanding of distributed systems trade-offs",
    "Clear communication on architecture decisions"
  ],
  "red_flags": [
    "Could not explain how they'd handle partitioning in Kafka"
  ],
  "highlights": [
    "Provided specific throughput numbers from current role"
  ],
  "candidate_emotion_timeline": [
    {"t_start_sec": 120, "t_end_sec": 300, "speaker": "candidate", "emotion": "calm", "confidence": 0.85},
    {"t_start_sec": 600, "t_end_sec": 900, "speaker": "candidate", "emotion": "anxious", "confidence": 0.72},
    {"t_start_sec": 1200, "t_end_sec": 1800, "speaker": "candidate", "emotion": "engaged", "confidence": 0.9}
  ],
  "summary": "Strong technical interview overall. Deep backend knowledge, but showed some nervousness around Kafka partitioning — worth probing in the CEO round.",
  "verdict": "clear_pass"
}
```

---

### 10b. CEO_BRIEF_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/ceo_brief.py` |
| **Version** | `v1` |
| **Model** | Smart (gpt-4o) |
| **When invoked** | Before CEO round — aggregates all prior round data |
| **Invoked by** | `activities/v1_ceo_brief.py` |
| **Pipeline stage** | `ceo_meeting_scheduled` |
| **Extraction** | Pydantic (`response_model=_Brief`) |

**Purpose:** Produce a 2-minute-read markdown brief for the CEO before the final interview.

**Input format placeholders:**
```
- {role_title}                 — role title
- {jd_text}                    — JD excerpt
- {candidate_json}             — candidate snapshot
- {fit_score} / {fit_tier}     — fit score and tier
- {screening_evaluation_json}  — screening evaluation
- {voice_call_json}            — voice call results
- {assessment_json}            — assessment results
- {technical_meeting_json}     — technical interview analysis
```

**Output:** Markdown brief with sections: Recommendation, At a glance, What stood out (positive), What worried us, Suggested CEO questions (3), Why this candidate vs the median applicant.

---

### 11. REJECTION_MESSAGE_V1

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/rejection_message.py` |
| **Version** | `v1` |
| **Model** | Fast (gpt-4o-mini) |
| **When invoked** | HR rejects a candidate → stage set to `rejected` |
| **Invoked by** | `activities/rejection.py` → `rejection_activity` |
| **Pipeline stage** | `rejected` |
| **Extraction** | Pydantic (`response_model=RejectionDraft`) |

**Purpose:** Draft a respectful, specific-but-safe rejection email. No scores, no rankings, no comparisons.

**Input format placeholders:**
```
- {name}                  — candidate name
- {role_title}            — role title
- {rejection_category}    — reason category (from safe-reasons library)
- {reason_template}       — template text for the reason
```

**Output structure:**
```json
{
  "body": "Hi Rahul, thanks for taking the time to go through our process and for sharing your work on the assignment. We decided to move forward with candidates whose experience more closely aligns with the specific technical requirements of this role at this time. We'd love to stay connected — opt into our talent pool here [link] to be considered for future opportunities that match your profile.",
  "category_used": "skill_alignment"
}
```

---

### 12. ROLE_DRAFTING PROMPTS (Conversational Role Creation)

| Property | Value |
|----------|-------|
| **File** | `src/llm/prompts/role_drafting.py` |
| **Version** | `v5` (system prompt) |
| **Model** | Smart (gpt-4o) |
| **When invoked** | HR opens role drafting chat (`POST /agentic/roles/chat`) |
| **Invoked by** | `activities/v1_role_drafting.py` |
| **Extraction** | Pydantic (all 3 sub-prompts use `response_model`) — see table below |

**Purpose:** Turn any input (one-liner, pasted JD, "same as last role") into a publish-ready JD in the fewest possible turns.

**Three sub-prompts:**

| Sub-prompt | File Section | Purpose |
|-----------|-------------|---------|
| `ROLE_DRAFT_SYSTEM` | `role_drafting.py` lines ~200-700 | Main system prompt — ~400 line agent with 5 routing lanes (A-E), multi-fact parsing, comp normalization, seniority-shift, PI/assignment config |
| `SECTION_REWRITE_TEMPLATE` | `role_drafting.py` | Rewrite a single JD section inline ("edit with AI") |
| `LINKEDIN_POST_SYSTEM` | `role_drafting.py` | Generate LinkedIn job post from the draft |

**Routing lanes (user intent detection):**
- **Lane A** — Full JD paste (>= 250 words): extract into 5-section structure, skip to turn 3
- **Lane B** — "Same as last": clone previous role, ask for title only
- **Lane C** — Vague / no title: suggest 2-3 titles, wait
- **Lane D** — Clear one-liner: standard drafting flow (4-5 turns)
- **Lane E** — Internship/contract: set employment_type + defaults

**Output structure (per turn):**
```json
{
  "message": "Locked salary and location — voice screening and auto-scheduling, both on?",
  "draft": {
    "title": "Senior Backend Engineer",
    "jd_text": "## About the role\n...\n## What you'll do\n...",
    "employment_type": "full_time",
    "ctc_min_lpa": 25,
    "ctc_max_lpa": 40,
    "max_notice_days": 60,
    "location": "Hyderabad",
    "remote_policy": "hybrid",
    "cut_line": 60,
    "assignment_deadline_days": 7,
    "pi_cognitive_url": null,
    "agentic": {"voice_screening_enabled": true, "meeting_bot_enabled": true},
    "scheduling": {"enabled": true, "rounds": {"technical": {...}, "ceo": {...}, "hr": {...}}}
  },
  "missing": ["PI cognitive link"],
  "quick_replies": ["Yes to both", "Voice screen only", "Auto-schedule only", "Neither, I'll handle it"],
  "ready_to_save": false
}
```

---

## Prompt Invocation Summary

| # | Prompt | Model Tier | Stage | Trigger | Extraction | I/O |
|---|--------|-----------|-------|---------|------------|-----|
| 1 | CLASSIFY_EMAIL_V1 | Fast | classified | Inbound email | Pydantic | Email → `{is_application, confidence, detected_role}` |
| 2 | PARSE_RESUME_V1 | Fast | resume_parsed | Resume uploaded | Pydantic | Resume text → `CandidateProfile` (37 fields) |
| 3 | FIT_SCORE_V1 | Smart | fit_scored | Profile ready | Pydantic | Profile + JD → `{dimension scores, recommended_tier}` |
| 4a | SCREENING_GEN_V1 | Fast | screening_sent | V1: fit scored | Pydantic | Profile + JD → `[5-7 questions]` |
| 4b | TAILORED_QS_V1 | Fast | screening_sent | V2: chat prewarm | Pydantic | Profile + JD → `[2 tailored questions]` |
| 4c | VOICE_SCREEN_GEN_V1 | Fast | voice_screen_scheduled | Voice: pre-call | Pydantic | Profile + JD → `[5 spoken questions]` |
| 5a | SCREENING_EVAL_V1 | Smart | screening_evaluated | V1: form submitted | Pydantic | Q&A + profile → `{verdict, scores, red_flags}` |
| 5b | SCORE_OPEN_TEXT_V1 | Smart | screening_evaluated | V1: per open-text | Pydantic | One Q&A → `{score 0-10, rationale}` |
| 5b | CHAT_TURN_SYSTEM_V1 | Smart | screening→assignment | V2: per chat turn | Raw text | State → streamed conversational text |
| 5c | VOICE_SCREEN_EVAL_V1 | Smart | voice_screen_evaluated | Voice: post-call | Pydantic | Transcript + paralinguistic → `{verdict, extracted_facts}` |
| 6b | EXTRACT_TURN_V1 | Fast | any chat stage | V2: per user message | Pydantic | Latest message + state → `{fields, intent, next}` |
| 7b | ASSIGNMENT_GEN_V1 | Smart | assignment_sent | V2: screening→assignment transition | Pydantic | Profile + JD + answers → full assignment brief |
| 8 | ASSIGNMENT_PARSE_V1 | Fast | post-assignment | Assignment submitted | Pydantic | Submission → `{completeness, quality_signals, concerns}` |
| 9 | JOURNEY_REPORT_V1 | Smart | report_ready | Post-assignment | Pydantic | All pipeline data → markdown report |
| 10a | MEETING_ANALYSIS_V1 | Smart | technical_evaluated | Meeting transcript ready | Pydantic | Transcript + rubric → `{scores, verdict, emotion_timeline}` |
| 10b | CEO_BRIEF_V1 | Smart | ceo_meeting_scheduled | Pre-CEO round | Pydantic | All rounds → CEO markdown brief |
| 11 | REJECTION_MESSAGE_V1 | Fast | rejected | HR rejects | Pydantic | Reason category → email body |
| 12 | ROLE_DRAFTING (3 prompts) | Smart | role creation | HR chat | Pydantic | Free-text → structured role draft |
