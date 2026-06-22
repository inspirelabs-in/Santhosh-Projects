"""VOICE_SCREEN_GEN_V1 + VOICE_SCREEN_EVAL_V1.

Spoken-screening question generation and post-call answer evaluation.
Conversational tone -- shorter than the email questionnaire because the
candidate is answering live on a phone call (no copy-paste from ChatGPT).
"""

VOICE_SCREEN_GEN_VERSION = "v5"
VOICE_SCREEN_EVAL_VERSION = "v5"


VOICE_SCREEN_GEN_V1 = """You design a spoken phone-screen for ONE candidate applying to ONE role.

=== ABOUT THE COMPANY & ROLE (role-tuned, generated at JD time) ===
Use THIS context to frame company-fit questions. Do NOT inject generic culture pillars (ownership / proof of work / builder mindset) unless this context explicitly calls for them -- they are wrong for many roles.
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
Design EXACTLY 7 questions for an outbound AI agent to ASK ALOUD. The agent is calling this candidate — keep the tone warm, direct, and conversational. Under 30 words per question. ONE topic per question except Q7.

BACKGROUND (Q1–Q2) — type=background | open: open with warmth, understand where they are today:
Q1. Current situation: ask them to walk through their current role — what they own day-to-day, their team size or scope, and total years of relevant experience. Open-ended. Let them set the frame. Do NOT ask yes/no.
Q2. Motivation: ask what's prompting them to explore new opportunities right now. You want to hear pull factors (growth, impact, ownership, new domain) — not just push factors (bad manager, better pay). If they only give compensation as reason, that's a weak signal. Do NOT ask yes/no.

WORK EXPERIENCE (Q3–Q4) — type=work_experience | skill_probe | depth: resume-anchored, specific, non-generic:
Q3. Pick ONE specific project, product feature, or technical problem from the candidate's resume above. Ask what they personally built or solved — the hardest call they had to make and the outcome. Name the project or employer explicitly so the candidate knows you read their profile. Tie the probe to a concrete requirement in the JD.
Q4. Pick a DIFFERENT entry from the resume — a different employer, product, or challenge. Probe on one of: a failure and what they learned, a moment they had to push back or change direction, or a trade-off between speed and quality. Must reference something from the resume, not a generic "tell me about a challenge."

COMPANY FIT (Q5–Q6) — type=company_fit | behavioral: test real interest in the company and alignment with the role criteria above:
Q5. Research signal: ask what they know about the company and this role, or why they specifically applied. A strong answer shows they did their homework on the company context above. A weak answer is "I saw it on LinkedIn." Do NOT lead the answer — let them show what they found.
Q6. Role-fit test: pick the most important dimension from the evaluation criteria above and ask for a concrete example that surfaces it (what they chose, what they decided, the outcome). Tie it to a real requirement of THIS role, not a generic "tell me about a fast-paced project."

LOGISTICS (Q7) — type=logistics: all three gates in one tight question:
Q7. Ask a single tight logistics question covering: current CTC + expected CTC for this role, notice period, and — ONLY when a real work location is known — alignment with the location. The role location is "{role_location}" ({remote_policy}). If that location value is "n/a", empty, or unknown, DROP the location part entirely and ask only about CTC and notice period (never say "based in None" or "based in n/a"). Example when location is known: "Quick logistics check — what's your current CTC and what are you expecting for this role, what's your notice period, and the role is based in {role_location} ({remote_policy}); are you aligned with that?"

=== RULES ===
- Every question must sound natural when spoken aloud by an AI on a phone call. No bullet points, no em-dashes, no markdown.
- Q3 and Q4 MUST reference a specific project, employer, or accomplishment named in the candidate profile. Generic questions are rejected.
- Never ask a yes/no question on Q1–Q6.
- expected_signal and follow_up_hint are for the evaluator only — never spoken to the candidate.
- Output MUST contain exactly 7 question objects.

Output strict JSON only:
{{
  "questions": [
    {{
      "id": "q1",
      "question": "Spoken text of the question — natural, conversational, under 30 words",
      "type": "background | work_experience | company_fit | logistics | skill_probe | depth | behavioral | open",
      "expected_signal": "What a strong answer looks like and why it signals a good hire",
      "follow_up_hint": "One-line probe the agent can use if the candidate gives a thin or evasive answer"
    }}
  ]
}}"""


VOICE_SCREEN_EVAL_V1 = """You evaluate a phone-screen transcript answer-by-answer for ONE candidate against this role.

NOTE: This is a TEXT-ONLY fallback evaluation — no audio recording was available.
Evaluate the transcript content only; do not speculate about tone or voice quality.

## Company & role context (role-tuned, generated at JD time)
Ground every judgment in THIS role's context below. Do NOT apply generic culture assumptions (e.g. "ownership", "builder mindset", "proof of work") unless this context explicitly calls for them -- those are wrong for many roles (a coupon editor is not judged like a builder).
{company_context_json}

## Role-specific evaluation criteria
Judge the candidate against THESE dimensions and signals, not generic defaults. Where a dimension lists what_good_looks_like / anti_signals, weigh them directly. If this object is empty, fall back to the JD with a neutral, role-appropriate stance (never invent a culture bias):
{evaluation_spec_json}

Role: {role_title}
JD (excerpt):
{jd_text}

Logistics gates:
- CTC range: {ctc_min_lpa}-{ctc_max_lpa} LPA
- Max notice: {max_notice_days} days
- Location: {role_location} ({remote_policy})

Candidate answers (transcribed from phone call):
{answers_json}

Score each answer 0-100 against the question's expected signal.
Weight: content quality (65%) > logistics fit (35%).
Note ONLY findings supported by the transcript — do NOT invent specifics.
Penalize evasive non-answers, contradictions with the resume, and logistics deal-breakers.

Verdict rules (THREE tiers):
- clear_pass: candidate answered most questions with real examples, English is coherent, no explicit hard logistics block. Score >= 50.
- clear_reject: consistently vague across most questions with no role-relevant specifics, OR English completely unintelligible, OR candidate explicitly ruled out a logistics requirement with zero flexibility stated. Score < 50 with no recovery signals.
- needs_hr_review: genuinely borderline — some strong and some weak answers, an unverifiable claim, or an ambiguous logistics signal that a human should confirm. Use this sparingly; when truly uncertain between pass and reject, prefer needs_hr_review over guessing.

EXTRACT every concrete logistic the candidate mentioned. Use null when not stated.
Do NOT guess — wrong values corrupt downstream scheduling.

Output strict JSON only:
{{
  "overall_score": 0,
  "per_question": [
    {{"question_id": "q1", "score": 0, "relevance": "low|medium|high", "notes": "evidence from transcript"}}
  ],
  "red_flags": ["..."],
  "strengths": ["..."],
  "verdict": "clear_pass | needs_hr_review | clear_reject",
  "verdict_rationale": "1-2 sentences citing concrete transcript evidence",
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
