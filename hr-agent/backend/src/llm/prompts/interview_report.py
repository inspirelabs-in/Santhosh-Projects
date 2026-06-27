"""INTERVIEW_REPORT_V1 -- Stage 8b post-interview synthesis from transcript."""



# [TO_FIX] SM-10: DEAD prompt — its only activity (activities/report.py) is
# [SCRAPE]-marked and off the live V2 path (post-interview synthesis is
# v1_meeting_analysis + v1_journey_report). Edits here are inert at runtime. Either
# revive a caller or stop maintaining. Left as-is per scope.
INTERVIEW_REPORT_VERSION = "v7"  # overall_score + criteria_scores; competency rationale explains score
INTERVIEW_REPORT_V1 = """You are synthesizing a structured interview report from an interview transcript.

## Company & role context (role-tuned, generated at JD time)
Ground cultural alignment assessment in THIS role's context. Do NOT apply generic culture assumptions unless the context explicitly calls for them.
{company_context_json}

## Role-specific evaluation criteria
Judge against THESE dimensions. Where a dimension lists what_good_looks_like / anti_signals, weigh them directly:
{evaluation_spec_json}

Role: {role_title}
Candidate: {candidate_name}
Interviewers: {interviewer_names}
Rubric competencies: {competencies_list}

Transcript:
{transcript_text}

Rules:
- Quote directly from the transcript as evidence. Do not paraphrase away specificity.
- Rate each rubric competency 0-5 with a 1-2 sentence rationale: what the candidate said that earned this rating and what held it back. A quote alone is not enough.
- For each evaluation_spec dimension in criteria_scores, score 0-100 and explain what in the transcript raised the score and what held it back. Name the anti_signal if one applied.
- overall_score is your holistic 0-100 read of this candidate against the role — weight the evaluation_spec dimensions most heavily.
- Flag contradictions with the candidate's resume or screening responses if visible.
- Do not infer anything the candidate did not state.
- Write a SNAPSHOT of this candidate: every sentence in the summary is about what they said and did, not about the role or what the round is designed to assess.

Respond in this exact JSON format:
{{
  "overall_score": 0,
  "summary": "3-4 sentence snapshot of this candidate: what they demonstrated, the strongest moment, the weakest, and the open question for the next round.",
  "strengths": ["specific strength — evidence: direct quote"],
  "concerns": ["specific concern — evidence: direct quote"],
  "criteria_scores": [
    {{"key": "...", "label": "...", "weight": 0, "score": 0, "rationale": "2 sentences: what in the transcript raised this score and what held it back. Name the anti_signal if one applied.", "evidence": ["direct quote"], "data_status": "verified"}}
  ],
  "competencies": [
    {{"competency": "string", "rating": 0-5, "rationale": "1-2 sentences: what demonstrated this rating and what held it back.", "evidence": ["direct quote"]}}
  ],
  "recommendation": "strong_yes | yes | borderline | no | strong_no",
  "rationale": "2-3 sentences grounding the recommendation in the strongest and weakest signals from the transcript.",
  "follow_up_questions": ["question to ask in next round, or empty array"]
}}"""

