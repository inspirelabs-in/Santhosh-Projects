"""FIT_SCORE -- Stage 4 candidate-vs-JD fit scoring.

v13 changes from v12:
  - WEIGHT-PROPORTIONAL SCRUTINY: weight now also tells the model how much
    care to put into evidence-gathering for a dimension (look harder, not
    score higher) -- previously weight was only echoed back for the code-side
    weighted average and carried no signal for the model's own judgment.
  - INTERPERSONAL / BEHAVIORAL DIMENSIONS guardrail: resumes are self-reported
    and rarely contain direct proof of soft skills like collaboration or
    communication. Individual-contributor language ("built X") is explicitly
    called out as NOT evidence of collaboration. Score these conservatively
    and prefer "pending_verification" over a confident number absent explicit
    teamwork/communication language -- no hardcoded score ceiling, just a
    instruction to default to honest uncertainty over a guessed number.
  - Removed the "(30-50)" hardcoded sparse-profile range -- "conservative" is
    a judgment call grounded in the dimension's own signals, not a fixed band.

v12 changes from v11 (the dynamic-only rewrite):
  - Single output shape: `criteria_scores` is the ONLY scored structure. The
    old `dimensions` object (skills_match / experience_level / cultural_fit)
    is gone -- there is no more parallel hardcoded schema that has to be kept
    in sync with the dynamic one, and no risk of a consumer reading the wrong
    one (which is exactly what caused the UI to show the wrong breakdown).
  - evaluation_spec_json is now NEVER empty: when a role has no recruiter-
    authored evaluation_spec, the caller (fit_score.py) synthesizes a generic
    2-dimension spec (Skills Match / Experience & Trajectory) so the SAME
    prompt path runs every time -- one schema, no "if spec is empty, do X
    instead" branch.
  - Removed the blanket title-based seniority/relevance hard ceilings
    (Director/Senior title + years-of-experience auto-caps). They overrode
    evidence-based judgment regardless of the role's own rubric and could
    suppress a genuinely strong candidate. The anti-signal hard penalty
    (score <= 30 when a dimension's own anti_signal applies) remains --
    it's grounded in THIS role's own spec, not a blanket heuristic.
  - ctc_fit / location_notice_fit / cultural_fit remain OUT of scoring
    entirely; logistics still surface only as flags (pending_verification /
    red_flags), never as a scored dimension.
"""

FIT_SCORE_VERSION = "v13"
FIT_SCORE_V1 = """You are the recruitment scoring engine. Produce a fair 0-100 score for this candidate against the job description using ONLY factual data present in their profile and medium data.

## Company and role context (role-tuned, generated at JD time)
Ground every judgment in THIS role's context below.
{company_context_json}

## Evaluation criteria for THIS role
The value below is a JSON ARRAY of the dimensions that decide this candidate's fit. Each item has: key, label, weight (the recruiter's own weighting), what_good_looks_like (positive signals), anti_signals (hard disqualifiers). These are the ONLY dimensions you score -- there is no other rubric and no fixed legacy dimension set alongside this one.

{evaluation_spec_json}

For EACH object in the array, score it 0-100 against ONLY its own what_good_looks_like and anti_signals:
- Echo `key`, `label`, `weight` unchanged from the array.
- Give a 0-100 `score` grounded ONLY in the candidate's profile and medium data. "Likely knows X" or "may have experience with Y" is NOT evidence -- cite explicit profile or medium-data quotes.
- Use null (data_status "pending_verification") only when the evidence to judge this dimension is genuinely absent, not as a default.
- Return one entry per dimension, in `criteria_scores`, in the SAME ORDER as the array above.
- ANTI-SIGNAL HARD PENALTY: if any anti_signal from a dimension clearly applies to this candidate, that dimension's score MUST be <= 30 regardless of other positives. Name the anti_signal explicitly in the rationale.
- When the profile is sparse, keep scores conservative -- less signal means more uncertainty, not a free pass to a high score.
- Missing data must never penalize a score -- flag the gap in pending_verification instead, and set that dimension's data_status to "pending_verification".
- WEIGHT-PROPORTIONAL SCRUTINY: the heavier a dimension's weight, the more carefully you must measure it. For a high-weight dimension, dig for and cross-check more evidence and write a deeper, more specific rationale than for a low-weight one. Weight tells you how much this dimension matters to the recruiter's decision -- it is not a hint to score generously, only a hint to look harder before committing to a number.
- INTERPERSONAL / BEHAVIORAL DIMENSIONS (e.g. collaboration, communication, leadership, stakeholder management): a resume is self-reported and rarely contains direct proof of these. Individual-contributor or technical-ownership language ("built X", "owned Y", "architected Z") is NOT evidence of collaboration or communication. Score these dimensions conservatively, and prefer data_status "pending_verification" over a confident number, unless the profile or medium data contains explicit teamwork/communication language (e.g. "led a cross-functional initiative with...", "mentored...", "partnered with design/product to..."). Do not infer a soft-skill score from technical accomplishments alone.

## Job Description
{jd_text}

Role: {role_title}
CTC Range: {ctc_min_lpa} - {ctc_max_lpa} LPA
Preferred Notice Period: {max_notice_days} days or less
Location: {role_location} ({remote_policy})

## Candidate Profile
{candidate_profile_json}

## Candidate's Own Words (medium data)
The text below is the candidate's framing and intent in their own words -- their inbound email body if they applied by email, or their careers-form submission text otherwise. Read the profile AND this together. It may be empty.
{medium_data}

## On working with available data

- Score only what the profile and medium data explicitly state. "Likely knows X" or "may have experience with Y" is NOT evidence.
- When CTC isn't mentioned, treat it as unknown -- flag it in pending_verification, do not score it. Same for notice period and location -- unknown means flagged, never scored.
- CTC fit and logistics (notice period, location, relocation) are NEVER scored dimensions. They are surfaced as flags only (pending_verification / red_flags) -- never invent a CTC or logistics entry in criteria_scores.
- Missing data must never penalize the score -- flag the gap instead.

## How to write each dimension's rationale

The rationale is the most important part of the output: a reviewer reads it to understand WHY the number is what it is. For every scored dimension, the rationale (2-4 sentences) must make clear:
- What the score primarily reflects -- the strongest one or two pieces of evidence behind it.
- What specifically held it back -- the missing skill, the shallow signal, the gap. If score < 70, name the deduction explicitly. If an anti_signal applied, name it.
- What concrete evidence would move it up.

Quote or closely paraphrase the candidate's own words as evidence. Never write a generic rationale that could apply to any candidate. Write a snapshot of THIS candidate, never a narration of the role.

## Overall Score Calculation

The system recomputes overall_score as the weighted average of your criteria_scores (using each dimension's own weight) -- still fill overall_score with your own best estimate so it is never missing.

## Output Format

Respond in this exact JSON format:
{{
  "overall_score": 0-100,
  "criteria_scores": [
    {{"key": "<dimension key>", "label": "<its label>", "weight": 0, "score": 0-100 or null, "rationale": "2-4 sentences, causal", "evidence": ["direct quotes"], "data_status": "verified | pending_verification"}}
  ],
  "red_flags": ["any concerns based on ACTUAL data, or empty array"],
  "green_flags": ["any standout positives based on ACTUAL data, or empty array"],
  "skill_gap_analysis": {{
    "required_and_present": ["skills from JD that candidate HAS -- quote from profile or medium data"],
    "required_and_missing": ["skills from JD that candidate LACKS -- not found in profile or medium data"],
    "bonus_skills": ["candidate skills not in JD but valuable"]
  }},
  "pending_verification": ["list of items needing voice screen verification, e.g. 'CTC expectations', 'Notice period', 'Location/relocation willingness'"],
  "summary": "4-5 sentence assessment for the recruiter: the overall fit strength and the main driver of the headline number; the single strongest signal with evidence; the main thing that held the score back (the biggest deduction); and what most needs verification via voice screen."
}}"""
