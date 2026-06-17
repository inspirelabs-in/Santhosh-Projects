"""CEO_BRIEF_V1 -- aggregate every round into a single narrative for the CEO.

Renders a markdown brief the CEO can read in under 2 minutes before the
final interview. Cites the artifact behind every claim.
"""

CEO_BRIEF_VERSION = "v1"


CEO_BRIEF_V1 = """You write the final brief for GrabOn's CEO (Ashok Reddy) before the last-stage interview.

## Company Context
GrabOn (InspireLabs) values: ownership, learning velocity, builder mindset, low ego, high accountability. Proof of work > credentials. The CEO cares about: Does this person own outcomes? Can they move fast with ambiguity? Do they build, not just coordinate? Are they curious and data-driven?

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

Assessment results (PI Behavioral + Cognitive):
{assessment_json}

Technical interview analysis:
{technical_meeting_json}

Output a markdown brief in this structure:

# Final Brief: {{candidate_name}} -- {{role_title}}

## Recommendation
One line: hire / borderline / no-hire, with the single most decisive reason.

## At a glance
- Resume fit: ...
- Phone screen: ...
- Assessments: ...
- Technical interview: ...

## What stood out (positive)
Three bullets, each with a quote or metric.

## What worried us
Three bullets, each with a quote or metric. If empty, write "Nothing concrete."

## Cultural Fit (GrabOn Values)
Assess against GrabOn's core traits: ownership, learning velocity, builder mindset, low ego, accountability. Cite evidence from screening answers, interview transcript, or assignment. One paragraph.

## Suggested CEO questions (3)
Specific, sharp -- aimed at the one or two unresolved gaps. At least one should probe ownership or builder mindset.

## Why this candidate vs. the median applicant
One paragraph, plain prose.

Do NOT invent numbers. Quote the artifacts you were given. Plain markdown.
"""
