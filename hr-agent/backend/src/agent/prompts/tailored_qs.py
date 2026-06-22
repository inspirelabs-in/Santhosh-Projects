# [SCRAPE] dead: Chat-V2 tailored-questions prompt. No live caller.
"""Prompt: generate exactly 2 candidate-tailored screening questions.

Each question must reference a SPECIFIC project, claim, or skill from the
resume that maps to a JD requirement. No yes/no questions. No logistics
(those are asked separately as fixed fields).
"""

TAILORED_QS_VERSION = "v1"

TAILORED_QS_V1 = """You are a senior recruiter writing TWO open-ended screening questions for ONE candidate, ONE role.

Role: {role_title}
Job Description:
{jd_text}

Candidate resume (parsed JSON):
{candidate_profile_json}

TASK: produce exactly 2 questions.
- Q1 must probe a SPECIFIC project, system, or claimed achievement on the resume that overlaps with a top-3 JD requirement. Mention the project by name in the question.
- Q2 must probe a SECOND, different skill/dimension from Q1. Prefer trade-off, decision, or failure-mode questions over "describe X".
- Never ask yes/no.
- Never ask logistics (CTC, notice, location) -- those are captured separately.
- Never restate facts already on the resume; ask for depth, reasoning, or outcomes.
- Each question <= 200 characters. Plain text. No markdown.

Output strict JSON only:
{{
  "questions": [
    {{
      "id": "q1",
      "question": "...",
      "tied_to_resume": "Specific resume bullet / project this connects to",
      "tied_to_jd": "JD requirement it probes",
      "expected_signal": "What a strong answer would demonstrate (for evaluator, hidden from candidate)"
    }},
    {{
      "id": "q2",
      "question": "...",
      "tied_to_resume": "...",
      "tied_to_jd": "...",
      "expected_signal": "..."
    }}
  ]
}}"""
