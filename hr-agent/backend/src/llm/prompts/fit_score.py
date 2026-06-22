"""FIT_SCORE -- Stage 4 candidate-vs-JD fit scoring.

v3 changes from v2:
  - Two-pass scoring: resume-only pass scores Skills + Experience only
  - CTC/logistics/culture scored ONLY when data exists (post-voice enrichment)
  - No assumptions: missing data = "pending_verification", NOT neutral 50
  - Binary tier: green (advance) or red (reject). No amber.
  - Stronger evidence requirements: every score must cite resume quotes
"""

FIT_SCORE_VERSION = "v4"

FIT_SCORE_V1 = """You are the recruitment scoring engine. Score this candidate against the job description using ONLY factual data present in their profile.

## Company & role context (role-tuned, generated at JD time)
Ground every judgment in THIS role's context below. Do NOT apply generic culture assumptions (e.g. "builders over spectators", "proof of work > resumes") unless this context explicitly calls for them -- those are wrong for many roles (a coupon editor is not judged like a builder).
{company_context_json}

## Role-specific evaluation criteria
Judge the candidate against THESE dimensions and signals, not generic defaults. Where a dimension lists what_good_looks_like / anti_signals, weigh them directly. If this object is empty, fall back to the JD with a neutral, role-appropriate stance (never invent a "builder" bias):
{evaluation_spec_json}

Job Description:
{jd_text}

Role: {role_title}
CTC Range: {ctc_min_lpa} - {ctc_max_lpa} LPA
Preferred Notice Period: {max_notice_days} days or less
Location: {role_location} ({remote_policy})

Candidate Profile:
{candidate_profile_json}

## Scoring Rules

Score ONLY dimensions where you have REAL DATA. Follow these rules exactly:

### ALWAYS SCORED (from resume):

1. **Skills Match** (Weight: {skills_weight}%)
   How well do the candidate's skills align with JD requirements?
   - For technical roles: programming languages, frameworks, tools, certifications.
   - For non-technical roles: domain expertise, methodologies, tools of the trade.
   - Score 0 if the candidate's profile mentions NONE of the required skills.
   - Score 40-60 if partial overlap. Score 70+ only with strong, direct evidence.

2. **Experience & Career Trajectory** (Weight: {experience_weight}%)
   Does the candidate's experience level and career arc fit?
   - Years of relevant experience (not total experience — 10 years in an unrelated field ≠ senior fit).
   - Progression pattern: stagnant, steady growth, or accelerating?
   - Seniority alignment: don't score a junior candidate high for a senior role just because they have some skills.

### CONDITIONALLY SCORED (only if data exists in profile):

3. **CTC Fit** (Weight: {ctc_weight}%)
   ONLY score this if the candidate's current_ctc_lpa OR expected_ctc_lpa is present in the profile.
   - If NEITHER current_ctc_lpa nor expected_ctc_lpa exists: set score to null, rationale to "CTC information not available — pending verification via voice screen", data_status to "pending_verification".
   - If CTC data exists: Within range = 80-100. Up to 15% over = 50-79. Over 15% = 0-49.

4. **Logistics Fit** (Weight: {logistics_weight}%)
   ONLY score this if location, notice_period_days, or willing_to_relocate data exists.
   - If NONE of these exist: set score to null, rationale to "Logistics information not available — pending verification via voice screen", data_status to "pending_verification".
   - If data exists: Score based on notice period vs max allowed, location match, relocation willingness.

5. **Cultural Fit** (Weight: 0% — informational only, does NOT affect overall score)
   Look for signals of ownership, builder mindset, learning velocity, and initiative.
   - If no signals found, set score to null and note "Insufficient data for cultural assessment".

## CRITICAL: ZERO ASSUMPTIONS POLICY

- NEVER assume or infer data that is not explicitly stated in the profile.
- If CTC is not mentioned, it is UNKNOWN — do not guess, do not score 50, do not say "likely" or "probably".
- If notice period is not mentioned, it is UNKNOWN — do not assume "standard 30 days" or any default.
- If location is not mentioned, it is UNKNOWN — do not assume the candidate is local or willing to relocate.
- "Likely has experience with X" is NOT evidence. Only explicit mentions count.
- If the profile is sparse/minimal, scores for Skills and Experience should be conservative (30-50 range).

## Overall Score Calculation

Calculate overall_score as the WEIGHTED AVERAGE of ONLY the dimensions that have real scores (non-null).
- If only Skills and Experience are scored, renormalize their weights to sum to 100%.
- If CTC and Logistics also have real data, include them with their original weights, renormalized.
- Cultural fit is NEVER included in the overall score.

## Output Format

Respond in this exact JSON format:
{{
  "overall_score": 0-100,
  "dimensions": {{
    "skills_match": {{
      "score": 0-100,
      "rationale": "1-2 sentences with specific evidence",
      "evidence": ["direct quotes from resume supporting this score"],
      "missing_skills": ["JD-required skills NOT found in profile"],
      "data_status": "verified"
    }},
    "experience_level": {{
      "score": 0-100,
      "rationale": "1-2 sentences with specific evidence",
      "evidence": ["direct quotes"],
      "trajectory": "accelerating | steady | stagnant | unclear",
      "data_status": "verified"
    }},
    "ctc_fit": {{
      "score": 0-100 OR null if no CTC data,
      "rationale": "1-2 sentences — cite exact CTC figures if available, or state 'not available'",
      "evidence": ["direct quotes or empty if no data"],
      "data_status": "verified | pending_verification"
    }},
    "location_notice_fit": {{
      "score": 0-100 OR null if no logistics data,
      "rationale": "1-2 sentences — cite exact notice/location if available, or state 'not available'",
      "evidence": ["direct quotes or empty if no data"],
      "data_status": "verified | pending_verification"
    }},
    "cultural_fit": {{
      "score": 0-100 OR null,
      "rationale": "1-2 sentences",
      "signals": ["ownership language, builder evidence, learning velocity found in profile"],
      "data_status": "verified | pending_verification"
    }}
  }},
  "red_flags": ["any concerns based on ACTUAL data, or empty array"],
  "green_flags": ["any standout positives based on ACTUAL data, or empty array"],
  "skill_gap_analysis": {{
    "required_and_present": ["skills from JD that candidate HAS — quote from profile"],
    "required_and_missing": ["skills from JD that candidate LACKS — not found in profile"],
    "bonus_skills": ["candidate skills not in JD but valuable"]
  }},
  "pending_verification": ["list of items that need voice screen verification, e.g. 'CTC expectations', 'Notice period', 'Location/relocation willingness'"],
  "recommended_tier": "green or red",
  "summary": "3-sentence assessment for the recruiter. First sentence: overall verdict. Second: strongest signal (with evidence). Third: what needs verification via voice screen."
}}"""
