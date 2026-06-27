"""SCORE_OPEN_TEXT_V1 -- Stage 6 open-text screening answer rubric scoring."""

SCORE_OPEN_TEXT_VERSION = "v2"  # rationale now explains what raised and held back the score

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

Do not penalize for writing style or language fluency — only content quality matters.

Respond in this exact JSON format:
{{
  "score": 0-10,
  "rationale": "2-3 sentences: what specific content in the answer earned the score (the example, number, or decision named), and what held it back (the vagueness, the deflection, the missing proof).",
  "evidence_quality": "strong, moderate, or weak",
  "flags": ["any concerns, or empty array"]
}}"""
