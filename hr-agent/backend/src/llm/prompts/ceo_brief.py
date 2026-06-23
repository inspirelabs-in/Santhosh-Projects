"""CEO brief prompt — aggregate every round into a single narrative.

v2: removed hardcoded company culture; sections are conditional on
DATA_NOT_AVAILABLE sentinel so the LLM never hallucinates missing data.
"""

CEO_BRIEF_VERSION = "v6"  # Langfuse version 6
CEO_BRIEF_V1 = """You write the final hiring brief before the last-stage interview.

Role: {role_title}
Job Description (excerpt):
{jd_text}

Candidate snapshot:
{candidate_json}

Resume fit score: {fit_score} / 100 ({fit_tier})

Email screening evaluation:
{screening_evaluation_json}

Voice phone screen (post-call evaluation):
{voice_call_json}

Assessment results:
{assessment_json}

Technical interview analysis:
{technical_meeting_json}

**IMPORTANT: Any section whose data is "DATA_NOT_AVAILABLE" MUST be omitted entirely from the brief. Do NOT invent, guess, or hallucinate content for unavailable data. Only write sections for which you have real data above.**

Output a markdown brief in this structure:

# Final Brief: {{candidate_name}} -- {{role_title}}

## Recommendation
One line: hire / borderline / no-hire, with the single most decisive reason.

## At a glance
Include only items for which data was provided above. Omit any line whose data was DATA_NOT_AVAILABLE.

## What stood out (positive)
Up to three bullets, each with a quote or metric from the available data. Fewer if data is limited.

## What worried us
Up to three bullets, each with a quote or metric. If nothing concrete, write "Nothing concrete from available data."

## Cultural Fit
Assess against the values and traits implied by the job description. Cite evidence from the available data (screening, interview, assignment). One paragraph. Skip entirely if insufficient data.

## Suggested interview questions (3)
Specific, sharp -- aimed at the one or two unresolved gaps from the available data.

## Why this candidate vs. the median applicant
One paragraph, plain prose.

Do NOT invent numbers. Quote only the artifacts you were given. Plain markdown.
"""
