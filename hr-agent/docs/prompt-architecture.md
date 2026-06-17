# HR Agent — Prompt & Pipeline Architecture

## Pipeline Flow (Stage-by-Stage)

```mermaid
flowchart TD
    subgraph INTAKE["1. Email Intake"]
        A1[📧 Email received<br/>mail_ingest service] --> A2[Create Candidate<br/>sender name/email]
    end

    subgraph CLASSIFY["2. Email Classification"]
        B1[CLASSIFY_EMAIL_V1<br/>model: fast] --> B2{Is application?}
        B2 -->|Yes + role matched| B3[CLASSIFIED]
        B2 -->|Low confidence| B4[NEEDS_HR_REVIEW]
        B2 -->|Not application| B5[Discarded]
    end

    subgraph PARSE["3. Resume Parsing"]
        C1[Extract PDF text<br/>PyMuPDF + Tesseract] --> C2[PARSE_RESUME_V1<br/>model: fast]
        C2 --> C3[CandidateProfile JSON<br/>skills, CTC, phone, email]
        C3 --> C4[Dedup + Merge candidates]
        C4 --> C5[Generate embedding<br/>text-embedding-3-small]
    end

    subgraph FIT["4. Fit Scoring"]
        D1[FIT_SCORE_V1<br/>model: smart] --> D2{Tier?}
        D2 -->|score >= 70, no knockouts| D3[🟢 GREEN<br/>Auto-advance]
        D2 -->|score >= 50, no knockouts| D4[🟡 AMBER<br/>HR review]
        D2 -->|knockouts or score < 50| D5[🔴 RED<br/>Auto-reject]
    end

    subgraph VOICE["5. Voice Phone Screen"]
        E1[VOICE_SCREEN_GEN_V1<br/>Generate 5 questions] --> E2[Build voice system prompt<br/>voice_prompts.py]
        E2 --> E3[ElevenLabs ConvAI call<br/>Pipecat voice agent]
        E3 --> E4[Transcript + paralinguistics]
        E4 --> E5[Voicemail classifier]
        E5 -->|Real person| E6[VOICE_SCREEN_EVAL_V1<br/>model: smart]
        E5 -->|Voicemail| E7[Retry / callback]
        E6 --> E8{Verdict}
        E8 -->|clear_pass + score >= threshold| E9[Auto-advance]
        E8 -->|needs_hr_review| E10[NEEDS_HR_REVIEW]
        E8 -->|clear_reject| E11[REJECTED]
    end

    subgraph ASSIGN["6. Assignment"]
        F1[Send assignment email<br/>brief + instructions] --> F2[Candidate submits]
        F2 --> F3[ASSIGNMENT_PARSE_V1<br/>Extract quality signals]
        F3 --> F4[ASSESSMENT_SUBMITTED<br/>HR reviews]
    end

    subgraph TECH["7. Technical Interview"]
        G1[Schedule Teams meeting<br/>auto_progress + Recall.ai] --> G2[Meeting happens<br/>bot records + diarizes]
        G2 --> G3[MEETING_ANALYSIS_V1<br/>round=technical, model: smart]
        G3 --> G4{Verdict}
        G4 -->|clear_pass| G5[TECHNICAL_PENDING_APPROVAL]
        G4 -->|clear_reject| G6[REJECTED]
        G4 -->|needs_hr_review| G7[NEEDS_HR_REVIEW]
    end

    subgraph CEO["8. CEO Interview"]
        H0[CEO_BRIEF_V1<br/>Aggregate all prior data] --> H1[Schedule CEO meeting]
        H1 --> H2[Meeting happens<br/>bot records + diarizes]
        H2 --> H3[MEETING_ANALYSIS_V1<br/>round=ceo, model: smart]
        H3 --> H4{Verdict}
        H4 -->|clear_pass| H5[CEO_MEETING_COMPLETED]
        H4 -->|clear_reject| H6[REJECTED]
        H4 -->|needs_hr_review| H7[NEEDS_HR_REVIEW]
    end

    subgraph HR["9. HR Discussion"]
        I1[Schedule HR meeting] --> I2[Meeting happens]
        I2 --> I3[HR_EVALUATED]
        I3 --> I4{Admin decision}
        I4 -->|Hired| I5[OFFER_EXTENDED]
        I4 -->|Rejected| I6[REJECTED]
    end

    subgraph REJECT["Rejection (any stage)"]
        R1[REJECTION_MESSAGE_V1<br/>model: fast, temp: 0.3] --> R2[Warm rejection email]
    end

    subgraph REPORTS["Reports (non-blocking)"]
        RP1[INTERVIEW_REPORT_V1<br/>Post-technical transcript synthesis]
        RP2[JOURNEY_REPORT_V1<br/>Full applicant journey report]
        RP3[CEO_BRIEF_V1<br/>2-min CEO read brief]
    end

    INTAKE --> CLASSIFY --> PARSE --> FIT
    FIT -->|GREEN| VOICE
    VOICE -->|pass| ASSIGN
    ASSIGN -->|reviewed| TECH
    TECH -->|approved| CEO
    CEO -->|approved| HR
    HR -->|hired| I5

    FIT -->|RED| R1
    VOICE -->|reject| R1
    TECH -->|reject| R1
    CEO -->|reject| R1
    HR -->|reject| R1

    TECH -.->|generates| RP1
    ASSIGN -.->|generates| RP2
    CEO -.->|pre-generates| RP3

    style INTAKE fill:#e3f2fd,stroke:#1565c0
    style CLASSIFY fill:#e8f5e9,stroke:#2e7d32
    style PARSE fill:#e8f5e9,stroke:#2e7d32
    style FIT fill:#fff3e0,stroke:#e65100
    style VOICE fill:#fce4ec,stroke:#c62828
    style ASSIGN fill:#f3e5f5,stroke:#6a1b9a
    style TECH fill:#e0f2f1,stroke:#00695c
    style CEO fill:#fff8e1,stroke:#f57f17
    style HR fill:#e8eaf6,stroke:#283593
    style REJECT fill:#ffebee,stroke:#b71c1c
    style REPORTS fill:#f5f5f5,stroke:#757575
```

## Role Creation Flow (Recruiter Chat)

```mermaid
flowchart TD
    subgraph ROLE_DRAFT["Role Drafting (5-turn conversation)"]
        T1["Turn 1: User describes role<br/>ROLE_DRAFT_SYSTEM + TURN_TEMPLATE<br/>model: smart"]
        T1 --> T1A{Input type?}
        T1A -->|Full JD paste >= 250w| T1B[Parse, extract fields<br/>Jump to Turn 3]
        T1A -->|Vague description| T1C[Propose 2-3 titles<br/>Wait for pick]
        T1A -->|One-liner with title| T1D[Standard draft flow]
        T1A -->|Internship/Contract| T1E[Set employment_type<br/>Adjust defaults]

        T1B & T1C & T1D & T1E --> T2

        T2["Turn 2: Fill gaps<br/>Ask salary band, location, remote policy"]
        T2 --> T3

        T3["Turn 3: Interview rounds<br/>Present numbered list with quick replies<br/>Auto-decide technical vs non-technical<br/>Confirm voice screening + auto-schedule"]
        T3 --> T4

        T4["Turn 4: Assessment setup<br/>PI Cognitive link + Take-home assignment"]
        T4 --> T5

        T5["Turn 5: Final confirmation<br/>ready_to_save = true"]
        T5 --> SAVE[Save Role + Pipeline Template]
    end

    subgraph LINKEDIN["LinkedIn Caption Generation"]
        L1[LINKEDIN_POST_SYSTEM<br/>model: smart] --> L2["200-300 word post<br/>No em dashes, human voice<br/>Includes apply instructions:<br/>careers@grabon.in"]
    end

    subgraph SECTION_EDIT["JD Section Editing"]
        S1["SECTION_REWRITE_SYSTEM<br/>Rewrite single section<br/>(About/What you'll do/Must-haves/etc.)"]
    end

    SAVE --> LINKEDIN
    SAVE -.->|user requests| SECTION_EDIT
```

## Voice Agent System (ElevenLabs ConvAI)

```mermaid
flowchart LR
    subgraph VOICE_TYPES["Voice Call Types (voice_prompts.py)"]
        VT1[SCREENING<br/>5 questions, handle voicemail]
        VT2[CONFIRMATION<br/>Confirm meeting time]
        VT3[MEETING_SCHEDULE<br/>Suggest slots, book]
        VT4[STATUS_UPDATE<br/>Inform candidate of stage]
        VT5[JOINING_DETAILS<br/>Onboarding info, joining date]
        VT6[GENERAL_QUERY<br/>Answer any candidate question]
    end

    subgraph VOICE_RULES["Shared Voice Rules"]
        VR1[No internal scores/evals shared]
        VR2[Voicemail detection + handling]
        VR3[Callback/reschedule handling]
        VR4[Critical hangup rules]
    end

    VT1 & VT2 & VT3 & VT4 & VT5 & VT6 --> PIPECAT[Pipecat Voice Agent<br/>ElevenLabs ConvAI]
    VOICE_RULES --> PIPECAT
    PIPECAT --> TRANSCRIPT[Transcript + Paralinguistics]
```

## Chat Agent (V2 LangGraph)

```mermaid
flowchart TD
    subgraph AGENT["V2 Chat Agent (agent/graph.py)"]
        AG1[User message] --> AG2[extract node<br/>EXTRACT_TURN_V1<br/>model: fast]
        AG2 --> AG3[persist_screening<br/>Save extracted data]
        AG3 --> AG4[route node<br/>Heuristic stage check]
        AG4 -->|Screening complete| AG5{All collected?}
        AG5 -->|Yes| AG6[Evaluate screening<br/>Deterministic knockout check]
        AG6 --> AG7[maybe_gen_assignment<br/>ASSIGNMENT_GEN_V1<br/>model: smart]
        AG7 --> AG8[reply node<br/>Stream response]
        AG5 -->|No| AG8
        AG4 -->|More questions needed| AG8
    end

    subgraph TAILORED["Tailored Questions"]
        TQ1[gen_tailored_questions<br/>TAILORED_QS_V1<br/>model: fast]
        TQ1 --> TQ2[2-3 skill/depth questions<br/>+ expected signals]
    end

    TAILORED -.->|feeds into| AG2
```

## Recruiter Agent (Pulse)

```mermaid
flowchart TD
    subgraph RECRUITER["Recruiter Agent (recruiter_agent/)"]
        RA1[User message] --> RA2[RECRUITER_SYSTEM_V1<br/>+ RECRUITER_TOOLS schema<br/>model: smart, streaming]
        RA2 --> RA3{Response type?}
        RA3 -->|Text reply| RA4[Stream tokens to client]
        RA3 -->|Tool calls| RA5[Dispatch tools<br/>max 5 hops]
        RA5 --> RA6[Feed results back to model]
        RA6 --> RA3
    end

    subgraph TOOLS["Recruiter Tools"]
        RT1[Search candidates<br/>embedding similarity]
        RT2[Compose email drafts]
        RT3[Extract candidate facts]
        RT4[Analyze assessments]
        RT5[Role drafting<br/>chat_role_draft]
    end

    RA5 --> RT1 & RT2 & RT3 & RT4 & RT5
```

## Complete Prompt Registry

| # | Prompt | File | Model | Pipeline Stage | Purpose |
|---|--------|------|-------|----------------|---------|
| 1 | `CLASSIFY_EMAIL_V1` | `llm/prompts/classify_email.py` | fast | Intake → Classified | Classify incoming email as application |
| 2 | `PARSE_RESUME_V1` | `llm/prompts/parse_resume.py` | fast | Classified → Parsed | Extract structured profile from resume PDF |
| 3 | `FIT_SCORE_V1` | `llm/prompts/fit_score.py` | smart | Parsed → Scored | Score candidate vs JD (0-100 + tier) |
| 4 | `SCREENING_GEN_V1` | `llm/prompts/screening_gen.py` | auto | Pre-screening | Generate 5-7 tailored screening questions |
| 5 | `SCREENING_EVAL_V1` | `llm/prompts/screening_eval.py` | smart | Screening → Evaluated | Evaluate screening responses (verdict) |
| 6 | `SCORE_OPEN_TEXT_V1` | `llm/prompts/score_open_text.py` | smart | Per-question | Score individual open-text answer (0-10) |
| 7 | `VOICE_SCREEN_GEN_V1` | `llm/prompts/voice_screening.py` | auto | Pre-voice screen | Design 5 spoken phone-screen questions |
| 8 | `VOICE_SCREEN_EVAL_V1` | `llm/prompts/voice_screening.py` | smart | Post-voice call | Evaluate phone-screen transcript |
| 9 | `ASSIGNMENT_PARSE_V1` | `llm/prompts/assignment_parse.py` | auto | Assignment submitted | Parse assignment submission quality |
| 10 | `MEETING_ANALYSIS_V1` | `llm/prompts/meeting_analysis.py` | smart | Post-interview | Score diarized interview transcript |
| 11 | `INTERVIEW_REPORT_V1` | `llm/prompts/interview_report.py` | smart | Post-technical | Synthesize interview into report |
| 12 | `CEO_BRIEF_V1` | `llm/prompts/ceo_brief.py` | smart | Pre-CEO round | Aggregate all data into CEO brief |
| 13 | `JOURNEY_REPORT_V1` | `llm/prompts/journey_report.py` | smart | Any stage | Full applicant journey report |
| 14 | `REJECTION_MESSAGE_V1` | `llm/prompts/rejection_message.py` | fast | Any rejection | Draft warm rejection email |
| 15 | `ROLE_DRAFT_SYSTEM` | `llm/prompts/role_drafting.py` | smart | Role creation | 5-turn conversational JD builder |
| 16 | `LINKEDIN_POST_SYSTEM` | `llm/prompts/role_drafting.py` | smart | Post-role creation | Generate LinkedIn job post |
| 17 | `SECTION_REWRITE_SYSTEM` | `llm/prompts/role_drafting.py` | smart | JD editing | Rewrite single JD section |
| 18 | `EXTRACT_TURN_V1` | `agent/prompts.py` | fast | Chat screening | Extract data from candidate message |
| 19 | `TAILORED_QS_V1` | `agent/prompts.py` | fast | Chat screening | Generate role-specific follow-up Qs |
| 20 | `ASSIGNMENT_GEN_V1` | `agent/prompts.py` | smart | Post-screening | Generate take-home assignment brief |
| 21 | `RECRUITER_SYSTEM_V1` | `recruiter_agent/prompts.py` | smart | Recruiter chat | Recruiter agent system prompt |
| 22 | Voice system prompts | `services/voice_prompts.py` | N/A | Voice calls | 6 call-type system prompts for Pipecat |

## Verdict Decision Matrix

| Stage | clear_pass | needs_hr_review | clear_reject |
|-------|-----------|-----------------|--------------|
| **Fit Score** | score >= 70, no knockouts | score >= 50, no knockouts | knockouts OR score < 50 |
| **Screening Eval** | score >= 75, no red flags, all logistics true | Everything else | score < 40 OR 2+ logistics false |
| **Voice Screen** | Clear answers, no knockout logistics, score >= threshold | Borderline | Contradiction, unrecoverable issue |
| **Technical Interview** | High scores, confident, JD-aligned | Borderline | Major red flags |
| **CEO Interview** | High scores, leadership aligned | Borderline | Major red flags |

## Key Infrastructure

- **LLM Client** (`llm/client.py`): All calls → `litellm.acompletion()` with structured output validation, retry, Langfuse tracing
- **Prompt Manager** (`llm/prompt_manager.py`): DB-stored prompt overrides with hardcoded fallbacks
- **Auto-Progress** (`services/auto_progress.py`): Automatic stage advancement with circuit breaker + fallback
- **Stall Detector** (`services/stall_detector.py`): Alerts when candidates stuck at a stage too long
- **Voice Provider** (`services/voice_provider.py`): ElevenLabs ConvAI integration with circuit breaker
