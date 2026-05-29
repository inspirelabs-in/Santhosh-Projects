"""MEETING_ANALYSIS_V1 -- score the diarized Teams transcript.

Used for the technical round and reused for the CEO round (with different
``focus`` text). Output: structured ``MeetingAnalysis``.
"""

MEETING_ANALYSIS_VERSION = "v1"


MEETING_ANALYSIS_V1 = """You evaluate an interview between an employer panel and a candidate.

Round: {round}                 # technical | ceo
Role: {role_title}
Job Description (excerpt):
{jd_text}

Scoring rubric (set by HR for this role):
{scoring_rubric_json}

Diarized transcript (newest at the bottom). Each turn is JSON with
{{"speaker": "candidate|panel", "t_start": float, "t_end": float, "text": "..."}}:
{transcript_json}

Paralinguistic features computed from the candidate audio (may be partial):
{paralinguistic_json}

Instructions:
1. Judge ONLY what the transcript supports. Do not invent specifics.
2. Score communication and confidence based on candidate turns: clarity,
   specificity, hedging, structure of answers.
3. Score technical depth against the rubric. Cite evidence quotes.
4. Note red flags: contradictions, evasive non-answers, dishonest claims,
   panel-detected misalignment.
5. Build an emotion timeline limited to the candidate -- coarse buckets
   (calm, engaged, anxious, frustrated, evasive). Group adjacent turns of
   the same emotion into one entry.
6. End with a one-paragraph summary in plain prose.

Output strict JSON only:
{{
  "technical_score": 0,
  "communication_score": 0,
  "confidence_score": 0,
  "overall_score": 0,
  "strengths": ["..."],
  "red_flags": ["..."],
  "highlights": ["..."],
  "candidate_emotion_timeline": [
    {{"t_start_sec": 0, "t_end_sec": 0, "speaker": "candidate", "emotion": "calm", "confidence": 0.0}}
  ],
  "summary": "...",
  "verdict": "clear_pass | needs_hr_review | clear_reject"
}}"""
