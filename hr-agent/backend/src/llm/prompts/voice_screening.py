"""VOICE_SCREEN_GEN_V1 + VOICE_SCREEN_EVAL_V1.

Spoken-screening question generation and post-call answer evaluation.
Conversational tone -- shorter than the email questionnaire because the
candidate is answering live on a phone call (no copy-paste from ChatGPT).
"""

VOICE_SCREEN_GEN_VERSION = "v3"
VOICE_SCREEN_EVAL_VERSION = "v3"


VOICE_SCREEN_GEN_V1 = """You design a SHORT spoken phone-screen for ONE candidate applying to ONE role at GrabOn (InspireLabs).

GrabOn Culture: High-ownership, builder-focused. Values proof of work, learning velocity, curiosity, accountability, and shipping over planning. Questions should probe for these traits alongside technical skills.

Role: {role_title}
Job Description:
{jd_text}

CTC Range: {ctc_min_lpa}-{ctc_max_lpa} LPA
Max Notice Days: {max_notice_days}
Location: {role_location} ({remote_policy})

Candidate Profile (parsed from resume):
{candidate_profile_json}

Design EXACTLY 5 questions for an outbound AI agent to ASK ALOUD. Rules:
1. Conversational. Under 25 words each. ONE topic per question.
2. Question 1 (resume project A): pick a SPECIFIC project, employer, or accomplishment from the candidate's resume above. Ask them to walk through what they built, the trade-off they made, or the outcome. Name the project explicitly so they know you read the resume. tie this to a JD requirement.
3. Question 2 (resume project B): pick a DIFFERENT project / role / skill from the resume. Probe deeper on technical decisions, scale, or failure recovery. Must reference something the candidate listed.
4. Question 3 (compensation): "What is your current CTC and what are you expecting for this role?" -- one question, two numbers.
5. Question 4 (notice period): "What is your current notice period, and when would you be able to join?"
6. Question 5 (location + relocation): "You're based in <city from resume if known>. The role is {role_location} ({remote_policy}). Are you open to relocating or attending onsite as required?"
7. Avoid yes/no on resume questions. Resume questions must NOT be generic ("tell me about a project") -- they must name a concrete thing from the resume.
8. Output MUST contain exactly 5 question objects, no more, no less.

Output strict JSON only:
{{
  "questions": [
    {{
      "id": "q1",
      "question": "Spoken text of the question",
      "type": "logistics | skill_probe | depth | behavioral | open",
      "expected_signal": "What a strong answer reveals (for the evaluator only)",
      "follow_up_hint": "If candidate gives a thin answer, the agent can probe with this"
    }}
  ]
}}"""


VOICE_SCREEN_EVAL_V1 = """You evaluate a phone-screen transcript answer-by-answer for ONE candidate at GrabOn (InspireLabs).

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

Paralinguistic features (from audio analysis -- may be partial):
{paralinguistic_json}

Score each answer 0-100 against expected_signal. Then produce an overall verdict.
Weight content quality higher than fluency. Note ONLY findings supported by the transcript --
do NOT invent specifics. Penalize evasive non-answers, contradictions with the resume,
and logistics deal-breakers.

Verdict rules (BINARY -- no middle ground):
- clear_pass: candidate meets role requirements AND logistics gates (CTC in range, notice acceptable, location OK). Score >= 60.
- clear_reject: candidate fails on technical fit, culture fit, OR any logistics gate (CTC way out of range, notice too long, won't relocate). Score < 60 or any hard deal-breaker.

Do NOT use "needs_hr_review". Every candidate gets a definitive pass or reject.

In addition to scoring, EXTRACT every concrete logistic the candidate
mentioned during the call. Use null when not stated. Do NOT guess --
extraction is for backfilling the candidate record, so wrong values
poison downstream scheduling.

Output strict JSON only:
{{
  "overall_score": 0,
  "per_question": [
    {{"question_id": "q1", "score": 0, "relevance": "low|medium|high", "notes": "evidence"}}
  ],
  "red_flags": ["..."],
  "strengths": ["..."],
  "verdict": "clear_pass | clear_reject",
  "verdict_rationale": "1-2 sentences citing concrete answers",
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
