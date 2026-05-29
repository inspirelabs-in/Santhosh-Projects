"""SCORE_OPEN_TEXT_V1 -- Stage 6 open-text screening answer rubric scoring."""

SCORE_OPEN_TEXT_VERSION = "v1"

SCORE_OPEN_TEXT_V1 = """Score this candidate's answer to a screening question.

Question: {question_text}
Expected quality indicators: {rubric_description}
Maximum score: 10

Candidate's answer:
{answer_text}

Score based on:
- Specificity: Does the answer reference concrete examples, numbers, or projects? (not vague generalities)
- Relevance: Does the answer address the question asked?
- Depth: Does the answer demonstrate genuine experience, not just awareness?
- Red flags: Copy-pasted text, AI-generated boilerplate, contradictions with resume

Respond in this exact JSON format:
{{
  "score": 0-10,
  "rationale": "2-3 sentence justification",
  "evidence_quality": "strong, moderate, or weak",
  "flags": ["any concerns, or empty array"]
}}"""
