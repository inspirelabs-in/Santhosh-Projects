"""VOICE_SCREEN_GEN_V6 + VOICE_SCREEN_EVAL_V6.

Spoken-screening question generation and post-call answer evaluation.
Conversational tone -- shorter than the email questionnaire because the
candidate is answering live on a phone call (no copy-paste from ChatGPT).
"""

VOICE_SCREEN_GEN_VERSION = "v8"  # Langfuse version 8
VOICE_SCREEN_EVAL_VERSION = "v14"  # v14 -- per-dimension criteria_scores added
VOICE_SCREEN_GEN_V1 = """You design a set of questions for a spoken phone-screen between a candidate and the HR.

=== ABOUT THE COMPANY & ROLE (role-tuned, generated at JD time) ===
Use THIS context to frame company-fit questions — let the role's specific context define the culture signals.
{company_context_json}

=== ROLE-SPECIFIC EVALUATION CRITERIA ===
Design questions that surface THESE dimensions and signals (what_good_looks_like / anti_signals). If empty, fall back to the JD with a neutral, role-appropriate stance:
{evaluation_spec_json}

=== ROLE CONTEXT ===
Role: {role_title}
Job Description:
{jd_text}

CTC Range: {ctc_min_lpa}-{ctc_max_lpa} LPA
Max Notice Days: {max_notice_days}
Location: {role_location} ({remote_policy})

=== CANDIDATE PROFILE ===
{candidate_profile_json}

=== QUESTION DESIGN ===
Design EXACTLY 4 to 5 questions for an outbound AI agent to ASK ALOUD. The agent is calling this candidate -- keep the tone warm, direct, and conversational. Under 25 words per question. ONE topic per question.

Lead with 1-2 questions that reference a specific project, employer, or accomplishment from the candidate's profile — calling it out by name shows you've read their resume. Tie it to a concrete requirement in the JD or a key dimension from the evaluation criteria.

After the resume-anchored question(s), ask 1-2 questions that probe depth or role-specific fit based on the evaluation criteria and company context. Keep them short and easy to answer aloud.

Finish with 2 logistics questions covering: current CTC and expected CTC for this role, notice period, and -- ONLY when a real work location is known -- alignment with the location. The role location is "{role_location}" ({remote_policy}). If that location value is "n/a", empty, or unknown, ask only about CTC and notice period. Example when location is known: "Quick logistics check -- what's your current and expected CTC, notice period, and are you comfortable with {role_location} ({remote_policy})?"

=== RULES ===
- Every question should sound natural when spoken aloud by an AI on a phone call — use conversational phrasing rather than bullet points, em-dashes, or markdown.
- The first 1-2 questions should reference a specific project, employer, or accomplishment from the candidate profile.
- Use open-ended questions — they draw out more signal. Yes/no questions work only for the logistics alignment check.
- expected_signal and follow_up_hint are for the evaluator only.
- Aim for 4-5 questions total.

Output strict JSON only:
{{
  "questions": [
    {{
      "id": "q1",
      "question": "Spoken text of the question -- natural, conversational, under 25 words",
      "type": "background | work_experience | company_fit | logistics | skill_probe | depth | behavioral | open",
      "expected_signal": "What a strong answer looks like and why it signals a good hire",
      "follow_up_hint": "One-line probe the agent can use if the candidate gives a thin or evasive answer"
    }}
  ]
}}"""


VOICE_SCREEN_EVAL_V1 = """You evaluate a phone-screen transcript answer-by-answer which happened with a candidate against this role.

NOTE: This is a TEXT-ONLY fallback evaluation -- no audio recording was available.
Evaluate the transcript content as text.

## Company & role context (role-tuned, generated at JD time)
Ground every judgment in THIS role's context below. Let the company context define the culture signals for this specific role.
{company_context_json}

## Role-specific evaluation criteria
Judge the candidate against THESE dimensions and signals. Where a dimension lists what_good_looks_like / anti_signals, weigh them directly. If this object is empty, fall back to the JD with a neutral, role-appropriate stance:
{evaluation_spec_json}

Role: {role_title}
JD (excerpt):
{jd_text}

Logistics reference (for context only):
- CTC range: {ctc_min_lpa}-{ctc_max_lpa} LPA
- Max notice: {max_notice_days} days
- Location: {role_location} ({remote_policy})

Candidate answers (transcribed from phone call):
{answers_json}

Paralinguistic signals (if available -- ignore if absent):
{paralinguistic_json}

=== SCORING RULES ===
Score the candidate on a fair integer scale 0-100. The score must reflect:
1. ARTICULATION and CLARITY -- how clearly the candidate communicated their own ideas. Judge the candidate's communication, not phone/audio quality.
2. DEPTH and SUBSTANCE -- whether answers contain concrete examples, specifics, and real outcomes rather than vague generalities.
3. ROLE and ORG RELEVANCE -- how well their experience and answers relate to the role and the company context above.

Compensation and logistics as context, not score drivers:
Salary, CTC, expected pay, notice period, and location are logistics facts captured in extracted_facts and surfaced as red_flags if needed. They don't need to influence the overall_score — a candidate with high salary expectations or a long notice period may still score well on communication and substance. Let the score reflect the content quality, not the logistics.

Guidance:
- Score only what the transcript contains. If it's not in the text, it's not in scope.
- For topics the candidate wasn't asked about, note the question as "not asked" with a neutral score.
- Keep findings grounded in transcript evidence.
- WRITE A SNAPSHOT OF THIS CANDIDATE, NEVER A NARRATION OF THE SCREEN. Every note and the summary are about what THIS candidate actually said. Lead with the candidate. Never open by describing the company, the role, or what the screen "checks for". Name the strongest answer, the weakest, what the candidate owned or deflected, and what most needs HR follow-up.
- Per-question notes must EXPLAIN the score, not label it: in 2-3 sentences name the band, the specific content that raised it (the example, number, or owned decision), and what specifically held it back (vagueness, no specifics, drift off the question). Quote or closely paraphrase the candidate.

EXTRACT every concrete logistic the candidate mentioned into extracted_facts. Use null when not stated.
Use null when not stated — wrong values corrupt downstream scheduling.

## Role-specific criteria scoring

The "Role-specific evaluation criteria" section above contains a JSON array of dimensions. For EACH object in that array, score it 0-100 against ONLY its what_good_looks_like (positive signals) and anti_signals (hard disqualifiers; if an anti_signal clearly applies, score <= 30), using the evidence from the call transcript. Echo key/label/weight unchanged. Return one entry per dimension in `criteria_scores` (same order as the array). If the array is empty, return `criteria_scores: []`.

Output strict JSON only (no verdict field):
{{
  "overall_score": 0,
  "criteria_scores": [
    {{"key": "...", "label": "...", "weight": 0, "score": 0, "rationale": "2-3 sentences grounded in what the candidate actually said", "evidence": ["quote or paraphrase"], "data_status": "verified"}}
  ],
  "per_question": [
    {{"question_id": "q1", "score": 0, "relevance": "low|medium|high", "notes": "2-3 sentences: which band and why -- the content that raised the score and what specifically held it back, with a quote/paraphrase"}}
  ],
  "red_flags": ["specific concern with the evidence behind it"],
  "strengths": ["specific strength with the evidence behind it"],
  "summary": "3-4 sentence SNAPSHOT of THIS candidate (see snapshot rule above): what they actually demonstrated on communication quality and answer depth, the strongest and weakest answers, and what most needs HR follow-up. Every sentence about the candidate, never a restatement of the role or what the screen assesses",
  "extracted_facts": {{
    "current_ctc_lpa": null,
    "expected_ctc_lpa": null,
    "notice_period_days": null,
    "current_location": null,
    "preferred_location": null,
    "willing_to_relocate": null,
    "work_authorization": null,
    "total_experience_years": null,
    "relevant_experience_years": null,
    "current_employer": null,
    "current_title": null,
    "highest_qualification": null,
    "primary_skills": [],
    "languages_spoken": [],
    "notes": null
  }}
}}"""
