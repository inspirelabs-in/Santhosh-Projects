"""VOICE_SCREEN_GEN_V1 + VOICE_SCREEN_EVAL_V1.

Spoken-screening question generation and post-call answer evaluation.
Conversational tone -- shorter than the email questionnaire because the
candidate is answering live on a phone call (no copy-paste from ChatGPT).
"""

VOICE_SCREEN_GEN_VERSION = "v4"
VOICE_SCREEN_EVAL_VERSION = "v4"


VOICE_SCREEN_GEN_V1 = """You design a spoken phone-screen for ONE candidate applying to ONE role at GrabOn (InspireLabs).

=== ABOUT GRABON ===
GrabOn (grabon.in) is India's largest cashback and coupons platform with 15M+ users. Parent company: InspireLabs Solutions Pvt. Ltd., Hyderabad.

Business model: affiliate marketing + SaaS (Cashback API sold to banks & fintechs) + consumer deals platform. Revenue comes from merchants paying for traffic and conversions. The team is lean and technical — engineers here own product decisions, not just tickets.

Culture pillars (use these to frame company-fit questions):
- OWNERSHIP: people here run things end-to-end. "I was involved in" is a red flag. "I owned and shipped" is the bar.
- PROOF OF WORK: credentials don't matter, shipped products do. What have you actually built that real users touched?
- LEARNING VELOCITY: the stack, the market, and the product all move fast. Curiosity and adaptability beat deep specialisation.
- BUILDER MINDSET: we move fast with limited resources. People who wait for perfect requirements don't thrive here.
- ACCOUNTABILITY: mistakes are fine, blame-shifting is not. Strong candidates own outcomes — good and bad.

The company values people who ask "why are we building this?" before "how do I build this?" and who push back when direction is wrong.

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

COMPANY FIT (Q5–Q6) — type=company_fit | behavioral: test real interest in GrabOn and culture alignment:
Q5. Research signal: ask what they know about GrabOn — the business model, what the platform does, or why they specifically applied. A strong answer shows they did their homework (cashback/affiliate model, Hyderabad, SaaS API, lean team). A weak answer is "I saw it on LinkedIn." Do NOT lead the answer — let them show what they found.
Q6. Builder/ownership test: GrabOn is lean and moves fast. Ask for a concrete example where they had to ship something under ambiguity, incomplete requirements, or with limited resources — specifically what they chose to cut, what they kept, and how they decided. This directly tests builder mindset and ownership. Avoid asking a generic "tell me about a fast-paced project."

LOGISTICS (Q7) — type=logistics: all three gates in one tight question:
Q7. "Quick logistics check — three things: what's your current CTC and what are you expecting for this role, what's your notice period, and the role is based in {role_location} ({remote_policy}) — are you aligned with that?"

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


VOICE_SCREEN_EVAL_V1 = """You evaluate a phone-screen transcript answer-by-answer for ONE candidate at GrabOn (InspireLabs).

NOTE: This is a TEXT-ONLY fallback evaluation — no audio recording was available.
Evaluate the transcript content only; do not speculate about tone or voice quality.

GrabOn Culture: Ownership > task completion. Learning velocity > static expertise. Builders > coordinators. Proof of work > credentials. Look for: ownership language ("I built", "I decided", "I shipped"), learning examples, data-driven decisions, builder mindset. Flag: passive language ("I was assigned"), credential-heavy with no proof of work, process-over-outcome focus.

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

Verdict rules (TWO tiers — no HR review):
- clear_pass: candidate answered most questions with real examples, English is coherent, no explicit hard logistics block. When uncertain, default to clear_pass. Score >= 50.
- clear_reject: consistently vague across most questions with no ownership or specifics, OR English completely unintelligible, OR candidate explicitly ruled out a logistics requirement with zero flexibility stated. Score < 50 with no recovery signals.

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
  "verdict": "clear_pass | clear_reject",
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
