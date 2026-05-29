"""INTERVIEW_REPORT_V1 -- Stage 8b post-interview synthesis from transcript."""

INTERVIEW_REPORT_VERSION = "v1"

INTERVIEW_REPORT_V1 = """You are synthesising a structured interview report from a transcript.

Role: {role_title}
Candidate: {candidate_name}
Interviewers: {interviewer_names}
Rubric competencies: {competencies_list}

Transcript:
{transcript_text}

Rules:
- Quote directly from the transcript as evidence. Do not paraphrase away specificity.
- Keep the overall summary to 3 sentences.
- Rate each rubric competency 0-5 with at least one supporting quote.
- Flag contradictions with the candidate's resume or screening responses if visible.
- Do not infer anything the candidate did not state.

Respond in this exact JSON format:
{{
  "summary": "3-sentence assessment",
  "strengths": ["bullet"],
  "concerns": ["bullet"],
  "competencies": [
    {{"competency": "string", "rating": 0-5, "evidence": ["direct quote"]}}
  ],
  "recommendation": "strong_yes | yes | borderline | no | strong_no",
  "rationale": "2-3 sentences grounding the recommendation",
  "follow_up_questions": ["question to ask in next round, or empty array"]
}}"""
