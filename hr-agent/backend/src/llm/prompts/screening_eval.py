"""SCREENING_EVAL_V1 -- evaluate candidate responses to screening questions.

Verdict buckets: clear_pass | needs_hr_review | clear_reject.
V1 rule: only clear_pass auto-advances. Everything else → needs_hr_review.
clear_reject is advisory; HR still decides.
"""

SCREENING_EVAL_VERSION = "v3"

SCREENING_EVAL_V1 = """You are evaluating a candidate's written screening responses against the role they applied for.

Role: {role_title}
Job Description:
{jd_text}

CTC Range: {ctc_min_lpa}-{ctc_max_lpa} LPA
Max Notice Days: {max_notice_days}

Candidate Profile Summary:
{candidate_profile_json}

Questions Asked (with expected signals):
{questions_json}

Candidate Responses:
{responses_json}

Evaluate each response for:
- Relevance: did they actually answer the question?
- Depth: concrete example, numbers, tech specifics vs generic buzzwords?
- Logistics fit: CTC within range? notice period acceptable? location workable?
- Red flags: contradictions with resume, copy-pasted AI answers, missing mandatory info.

Contradiction check (MANDATORY -- add to red_flags when found):
- Compare each screening answer to the resume profile. If the candidate claims
  experience or skills in screening that are absent from the resume, flag as
  "resume_screening_mismatch: <field>".
- If CTC expected > role.ctc_max_lpa by >30%, flag "ctc_unrealistic".
- If notice period exceeds role.max_notice_days by >50%, flag "notice_excessive".
- If answers are suspiciously similar to textbook LLM phrasing across 3+
  questions (no personal specifics, no numbers, no project names), flag
  "suspected_ai_generated".

Scoring rules for empty / missing answers (STRICT):
- If a candidate's answer is empty, whitespace only, or clearly "no answer",
  score that question 0, relevance "low", and notes "no answer provided".
  Do NOT invent content and do NOT score it as if answered.
- If all answers are empty, set overall_score <= 10, verdict "needs_hr_review",
  and list "no_screening_responses" in red_flags.

Output strict JSON:
{{
  "overall_score": 0-100,
  "per_question": [
    {{
      "question_id": "q1",
      "score": 0-10,
      "relevance": "low | medium | high",
      "notes": "one sentence"
    }}
  ],
  "logistics_check": {{
    "ctc_in_range": true,
    "notice_acceptable": true,
    "location_workable": true,
    "rationale": "one sentence"
  }},
  "logistics_values": {{
    "current_ctc_lpa": 12.5,
    "expected_ctc_lpa": 18.0,
    "notice_period_days": 60,
    "current_location": "Bangalore",
    "willing_to_relocate": true
  }},
  "red_flags": ["..."],
  "strengths": ["..."],
  "verdict": "clear_pass | needs_hr_review | clear_reject",
  "verdict_rationale": "2-3 sentences justifying the bucket"
}}

For `logistics_values`:
- Extract raw numbers / text from the candidate's answers. Use null if the
  candidate did not say. Never guess. Don't copy from the resume — only use
  what the candidate actually wrote in their screening responses.
- Parse "15 LPA", "fifteen lakhs", "1.5M", "INR 18,00,000" consistently to
  decimal LPA.
- Notice period "immediate" = 0, "1 month" = 30, "2 months" = 60, etc.

Verdict rules:
- clear_pass: overall_score >= 75 AND no red_flags AND logistics_check all true.
- clear_reject: overall_score < 40 OR logistics_check has 2+ false AND the role cannot flex on them.
- Everything else: needs_hr_review."""
