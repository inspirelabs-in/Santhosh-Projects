"""SCREENING_EVAL_V1 -- evaluate candidate responses to screening questions.

Score-only contract (v6+): the model returns overall_score (0-100) plus rich
diagnostic fields. Pass/fail routing is handled entirely by downstream code
against overall_score. The model does NOT emit a verdict.
"""

SCREENING_EVAL_VERSION = "v6"  # Langfuse version 6
SCREENING_EVAL_V1 = """You are evaluating a candidate's screening responses against this role.

## Company & role context (role-tuned, generated at JD time)
Ground every judgment in THIS role's context below. Let the evaluation criteria and company context define what good looks like for this specific role.
{company_context_json}

## Role-specific evaluation criteria
Judge the candidate against THESE dimensions and signals. Where a dimension lists what_good_looks_like / anti_signals, weigh them directly. If this object is empty, fall back to the JD with a neutral, role-appropriate stance:
{evaluation_spec_json}

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

## Scoring dimensions

Score overall_score as a fair integer 0-100 reflecting how well the candidate
answered the substantive questions for THIS role. Three axes drive the score:

1. Relevance -- did the candidate actually answer each question, or deflect?
2. Depth -- concrete specifics (numbers, project names, tech decisions, personal
   experience) vs. generic buzzwords. Quote the candidate's own words as
   evidence in per_question notes.
3. Honesty / consistency -- cross-check answers against the resume profile
   ({candidate_profile_json}) and against what the role expects
   (evaluation_spec_json / company_context_json). Claims that contradict the
   resume or that are implausibly vague relative to claimed seniority lower the
   score for the affected question(s).

IMPORTANT: logistics (CTC, notice, location) are extracted and assessed
separately in logistics_check / logistics_values rather than reflected in the
overall_score. A candidate whose compensation expectation is above the band but
who answers substantively should still receive a fair content score.

## Contradiction check (MANDATORY -- add to red_flags when found)
- Compare each screening answer to the resume profile. If the candidate claims
  experience or skills in screening that are absent from the resume, flag as
  "resume_screening_mismatch: <field>".
- When answers read like generic, textbook AI output across multiple questions
  (no personal specifics, no numbers, no project names), flag
  "suspected_ai_generated".
- When the candidate's expected CTC sits well above the role's band with no
  sign the role can flex, note it as a logistics concern in
  logistics_check.rationale only — it belongs there rather than in the overall score or red_flags.
- When the notice period runs well past {max_notice_days}, flag it as a
  logistics concern in logistics_check.rationale only.

## Empty / missing answers
- If a candidate's answer is empty, whitespace only, or clearly "no answer",
  score that question 0, set relevance "low", and set notes "no answer provided".
  There's no content to evaluate in that case.
- If all answers are empty, the score naturally settles at 0 since there's nothing
  to assess — flag it in red_flags as "no_screening_responses".

## Output

Return strict JSON -- no prose outside the JSON block:
{{
  "overall_score": 0,
  "per_question": [
    {{
      "question_id": "q1",
      "score": 0,
      "relevance": "low | medium | high",
      "notes": "one sentence quoting or citing the candidate's actual answer"
    }}
  ],
  "logistics_check": {{
    "ctc_in_range": true,
    "notice_acceptable": true,
    "location_workable": true,
    "rationale": "one sentence summarising any logistics concerns"
  }},
  "logistics_values": {{
    "current_ctc_lpa": 12.5,
    "expected_ctc_lpa": 18.0,
    "notice_period_days": 60,
    "current_location": "Bangalore",
    "willing_to_relocate": true
  }},
  "red_flags": [],
  "strengths": [],
  "summary": "2-3 sentences on the candidate's substantive fit for the role"
}}

### logistics_values extraction rules
- Extract raw numbers / text from the candidate's screening answers only.
  Use null if the candidate did not state a value.
- Parse "15 LPA", "fifteen lakhs", "1.5M", "INR 18,00,000" consistently to
  decimal LPA.
- Notice period: "immediate" = 0, "1 month" = 30, "2 months" = 60, etc.

### Guardrails
- Score what's in the answers. If a question wasn't answered, note that.
- For data the candidate wasn't asked about, use a neutral score.
- Output the fields listed above.
- The score is the output — routing decisions are handled in code."""
