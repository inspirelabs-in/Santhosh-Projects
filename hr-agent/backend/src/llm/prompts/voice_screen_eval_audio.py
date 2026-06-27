"""VOICE_SCREEN_EVAL_AUDIO_V1 — Gemini audio-enabled voice-screen evaluator.

Used by ``gemini_audio_eval.py`` (primary eval path with audio recording).
The text-only fallback lives in ``voice_screening.py`` (``VOICE_SCREEN_EVAL_V1``).
"""

VOICE_SCREEN_EVAL_AUDIO_VERSION = "v16"  # v16 -- removed IC-engineer culture lens + hardcoded bands/70-30 split; defer to spec
VOICE_SCREEN_EVAL_AUDIO_V1 = """You evaluate a phone-screen recording for ONE candidate against this role.

## Company & role context (role-tuned, generated at JD time)
Ground company-fit and culture judgments in THIS context. Let the role's specific context — not a generic individual-contributor or engineering lens — define what "good" looks like here (a manager, coordinator, or operator role values different signals than a builder role).
{company_context_json}

## Role-specific evaluation criteria
Weigh answers against THESE dimensions (what_good_looks_like / anti_signals). If empty, assess against the JD with a neutral stance:
{evaluation_spec_json}

Role: {role_title}
JD:
{jd_text}

Logistics gates:
- CTC range: {ctc_min_lpa}-{ctc_max_lpa} LPA
- Max notice: {max_notice_days} days
- Location: {role_location} ({remote_policy})

Candidate answers (transcript -- secondary reference only):
{answers_json}

Audio recording: attached above as your PRIMARY input. Evaluate authenticity, engagement, and how the candidate defends their answers directly from the audio. The transcript is a fallback for extracting logistics numbers only. If audio and transcript conflict, trust the audio.

---
CONTENT EVALUATION:
Strong signals: specific numbers/timelines, real decisions with trade-offs, learning from failures, direct ownership of outcomes.
Weak signals: vague generalities, no specifics under follow-up, inconsistency with resume claims, credentials cited without demonstrated depth.

AUDIO EVALUATION (evaluate from the recording — never invent):

CONFIDENCE & CONVICTION -- read this carefully before scoring:

Speech disfluency (stuttering, false starts, word repetition, "um/uh", call anxiety, nervous pauses) is a speech pattern — evaluate confidence from content only. Many capable people stutter or stumble on phone calls -- this tells you nothing about their ability or honesty.

True confidence is CONTENT-based. What counts as a strong vs weak signal is defined by the role's own "Role-specific evaluation criteria" (what_good_looks_like / anti_signals) and "Company & role context" above — read those first and let them set the bar. Do NOT assume an individual-contributor "I built it myself" frame; for a manager, coordinator, or operator role the strong signal may be driving others, coordinating, or making a call across a team. In general:
- Holds a specific position when probed or challenged, and backs it with concrete evidence → strong
- Gives direct, specific answers (real numbers, dates, decisions, outcomes) without hedging → strong
- Backtracks, softens a specific claim under mild challenge, or goes vague about their own role/decisions → weak
- Energy and substance drop specifically on depth questions about their own work → possible gap

WORD CHOICE -- interpret through the spec, not a fixed vocabulary:
- Read the candidate's framing against the what_good_looks_like / anti_signals for this role. The specific phrasings that signal genuine contribution differ by role — what matters is concrete, evidenced engagement with their own scope, not any particular set of "ownership" words.
- Emotional investment: do they speak with genuine engagement about their work, or describe it like a bystander?

ANSWER ALIGNMENT:
- Did the candidate actually answer the question asked, or drift to a safer/adjacent topic?
- Flag pivots: candidate sidesteps the specific project/decision asked about and substitutes a generic story
- Note if a follow-up was needed to get a direct answer

These are irrelevant to hiring signal and should be ignored: accent, stuttering, filler words ("um", "like"), false starts, nervousness, quiet delivery, call anxiety, non-native fluency.

ENGLISH CLARITY:
Only affects verdict if: candidate answers entirely in a non-English language and cannot switch when asked, OR speech is so fragmented it cannot be understood at all. Stuttering and accented English are never a clarity failure.

SCORING (0-100 per question):
Score each question on absolute merit, calibrated to THIS role's bar — the higher the answer clears the what_good_looks_like signals (and the further it stays from the anti_signals) for this role, the higher the score; a non-answer, clear dodge, or completely unintelligible response sits at the bottom. Do not apply a one-size-fits-all band table; the role's criteria and context define what a strong vs weak answer is. A downstream system owns the pass / needs-review / reject decision from your scores, and the role-weighted overall is recomputed in code from criteria_scores — so score honestly per the role's bar rather than to hit a target number.

Each per-question score should reflect both what the candidate said (content quality, specificity, relevance to the role) and the conviction signal from the audio (holding a position when probed, giving specifics, engaging concretely with their own scope). Delivery smoothness, accent, and fluency are excluded from scoring.

overall_score = the mean of the per_question scores, rounded to an integer. Compute it from the per-question scores; do not eyeball a separate number.

Logistics factors (compensation, notice period, location) are extracted and flagged only — they do not affect the score. A candidate with a high salary expectation who answers well still scores high.

LOGISTICS HANDLING -- read carefully:
- CTC: if the candidate states a number AND expresses any flexibility ("open to discussion", "willing to negotiate", "based on the overall package") → this is not a logistics failure. Extract the number, note the flexibility. Only flag if they state a number and explicitly say it is non-negotiable.
- Notice period: only flag if candidate confirms it cannot be reduced and it clearly exceeds the limit.
- Location: only flag if candidate explicitly refuses to work from the required location.
- When uncertain whether a logistics gate is truly breached, record what was said in extracted_facts for HR without penalising or flagging it.

Output scoring only — a downstream system routes the candidate from overall_score.

WRITE A SNAPSHOT OF THIS CANDIDATE, NEVER A NARRATION OF THE SCREEN. Every note and the verdict_rationale are about what THIS candidate actually said and how they held up, for a reviewer who already knows the role. Lead with the candidate. Never open by describing the company, the role, or what the screen is "checking for". Name the strongest answer, the weakest, what the candidate owned or deflected, and what most needs HR follow-up.

WRITING THE PER-QUESTION NOTES: for each question, the notes must EXPLAIN the score, not just label it. In 2-3 sentences say how high or low the answer scored against this role's bar and why: the specific content evidence (the example given, the number cited, the decision owned) that raised it, and what specifically held it back (vagueness, a deflection, a missing specific, a softened claim under probing). Reference the conviction signal from the audio (held a position / gave specifics / engaged concretely with their own scope). Never mention stuttering, accent, or delivery.

EXTRACTION: Extract only what was explicitly stated on the call. Use null when not mentioned.

## Role-specific criteria scoring

The "Role-specific evaluation criteria" section above contains a JSON array of dimensions. For EACH object in that array, score it 0-100 against ONLY its what_good_looks_like (positive signals) and anti_signals (hard disqualifiers; if an anti_signal clearly applies, score <= 30), using the evidence from the call audio and transcript. Echo key/label/weight unchanged. Return one entry per dimension in `criteria_scores` (same order as the array). If the array is empty, return `criteria_scores: []`.

Output strict JSON only:
{{
  "overall_score": 0,
  "criteria_scores": [
    {{
      "key": "...",
      "label": "...",
      "weight": 0,
      "score": 0,
      "rationale": "2-3 sentences: what the candidate said and how the conviction signal landed for this dimension, what raised the score, and what held it back. Name the anti_signal if one applied.",
      "evidence": ["quote or paraphrase"],
      "data_status": "verified"
    }}
  ],
  "per_question": [
    {{
      "question_id": "q1",
      "score": 0,
      "relevance": "low|medium|high",
      "notes": "2-3 sentences: how it scored against the role's bar and why -- the content evidence that raised the score and what specifically held it back, plus the conviction signal. No mention of stuttering or delivery."
    }}
  ],
  "red_flags": ["specific concern with the evidence behind it"],
  "strengths": ["specific strength with the evidence behind it"],
  "verdict_rationale": "3-4 sentences: the overall content quality and conviction picture, naming the strongest answer and the weakest, and what most needs HR follow-up -- scoring only, no pass/fail call",
  "extracted_facts": {{
    "current_ctc_lpa": null,
    "expected_ctc_lpa": null,
    "notice_period_days": null,
    "current_location": null,
    "preferred_location": null,
    "willing_to_relocate": null,
    "work_authorization": null,
    "total_experience_years": null,
    "relevant_experience_years": null,
    "current_employer": null,
    "current_title": null,
    "highest_qualification": null,
    "primary_skills": [],
    "languages_spoken": [],
    "notes": null
  }}
}}"""
