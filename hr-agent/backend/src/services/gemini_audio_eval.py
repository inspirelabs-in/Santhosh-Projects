"""Unified Gemini 2.5 Flash evaluator for voice-screen recordings.

Gemini receives the audio file (primary) + transcript (secondary) in one call
and returns a complete VoiceCallScore — no separate paralinguistic step.

Audio is the primary signal: tone, English clarity, confidence, engagement.
Transcript is secondary: used to verify content, extract logistics facts,
and catch claims the audio alone can't confirm (numbers, names, dates).

Returns None when GEMINI_API_KEY is unset, recording unavailable, or on any
unrecoverable error — the caller falls back to text-only LiteLLM eval.
"""

from __future__ import annotations

import io
import json
import logging
from uuid import UUID

from src.config import get_settings
from src.models.v1 import VoiceCallScore

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-2.5-flash"

_EVAL_PROMPT = """You evaluate a phone-screen recording for ONE candidate at GrabOn (InspireLabs).

ABOUT GRABON:
India's largest cashback and coupons platform. Parent: InspireLabs. High-traffic consumer tech, affiliate marketing, and SaaS. Culture: ownership, proof of work, learning velocity, builder mindset.

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

Speech disfluency (stuttering, false starts, word repetition, "um/uh", call anxiety, nervous pauses) is NOT a confidence signal. Ignore it completely. Many capable people stutter or stumble on phone calls -- this tells you nothing about their ability or honesty.

True confidence is CONTENT-based. Look only for:
- Holds a specific position when probed or challenged → strong
- Gives a direct "I decided / I built / I shipped" answer without deflecting → strong
- Provides specific numbers, dates, or outcomes without hedging → strong
- Backtracks or softens a specific claim when mildly challenged → weak
- Becomes vague specifically when asked about their own decisions → weak
- Energy and substance drop specifically on depth questions about their own work → possible gap

WORD CHOICE -- map these patterns:
- Ownership vocabulary: "I decided", "I built", "I pushed back", "I shipped", "I changed" → strong
- Passive/deflecting vocabulary: "we kind of", "it was decided", "I was involved in", "I supported" → weak
- Emotional investment: do they speak with genuine energy about their work, or describe it like a bystander?

ANSWER ALIGNMENT:
- Did the candidate actually answer the question asked, or drift to a safer/adjacent topic?
- Flag pivots: candidate sidesteps the specific project/decision asked about and substitutes a generic story
- Note if a follow-up was needed to get a direct answer

Do NOT flag or penalise: accent, stuttering, filler words ("um", "like"), false starts, nervousness, quiet delivery, call anxiety, non-native fluency. These are irrelevant to hiring signal.

ENGLISH CLARITY:
Only affects verdict if: candidate answers entirely in a non-English language and cannot switch when asked, OR speech is so fragmented it cannot be understood at all. Stuttering and accented English are never a clarity failure.

SCORING (0-100 per question):
85-100: Specific, owned, directly relevant to JD
65-84: Solid, mostly on topic, minor gaps
40-64: Generic or vague, limited specificity
20-39: Evasive, inconsistent, or platitude-heavy
0-19: Non-answer, clear dodge, or completely unintelligible

Weight: Content quality 60% · Audio authenticity 30% · Logistics alignment 10%
Audio authenticity = conviction signals only (holding position, giving specifics, ownership language). NOT delivery smoothness.

LOGISTICS HANDLING -- read carefully:
- CTC: if the candidate states a number AND expresses any flexibility ("open to discussion", "willing to negotiate", "based on the overall package") → this is NOT a logistics failure. Extract the number, note the flexibility. Only flag if they state a number and explicitly say it is non-negotiable.
- Notice period: only flag if candidate confirms it cannot be reduced and it clearly exceeds the limit.
- Location: only flag if candidate explicitly refuses to work from the required location.
- When uncertain whether a logistics gate is truly breached, do not penalise. The fit re-score handles hard knockouts separately.

VERDICT -- two tiers only:
- clear_pass: candidate answered most questions with real examples, showed up coherently in English, no explicit hard logistics block. When uncertain, default to clear_pass -- subsequent rounds will filter further. Score >= 50.
- clear_reject: consistent failure across most questions (vague on everything, no specifics, no ownership), OR English completely unintelligible, OR candidate explicitly ruled out a logistics requirement with zero flexibility. Score < 50 with no recovery signals.

EXTRACTION: Only extract what was explicitly stated on the call. Use null when not mentioned.
Do not infer from resume. Wrong values corrupt downstream scheduling.

Output strict JSON only:
{{
  "overall_score": 0,
  "per_question": [
    {{
      "question_id": "q1",
      "score": 0,
      "relevance": "low|medium|high",
      "notes": "conviction signal observed + content evidence -- no mention of stuttering or delivery"
    }}
  ],
  "red_flags": ["..."],
  "strengths": ["..."],
  "verdict": "clear_pass|clear_reject",
  "verdict_rationale": "2-3 sentences: content quality first, conviction signals second, logistics third",
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


async def evaluate_voice_call_with_audio(
    *,
    recording_r2_key: str,
    answers: list[dict],
    role_title: str,
    jd_text: str | None = None,
    ctc_min_lpa: float | None = None,
    ctc_max_lpa: float | None = None,
    max_notice_days: int | None = None,
    role_location: str | None = None,
    remote_policy: str | None = None,
    application_id: UUID | None = None,
) -> VoiceCallScore | None:
    """Evaluate the voice call using Gemini 2.5 Flash with audio + transcript.

    Returns a fully populated VoiceCallScore or None on failure.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.debug("GEMINI_API_KEY not set — skipping Gemini eval")
        return None

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types as gtypes  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("google-genai not installed — skipping Gemini eval")
        return None

    # Download audio from R2
    try:
        from src.services.file_storage import download
        bucket = settings.r2_bucket_resumes
        audio_bytes = await download(bucket, recording_r2_key)
    except Exception as exc:
        logger.warning("audio download failed for %s: %s", recording_r2_key, exc)
        return None

    if not audio_bytes:
        logger.warning("empty audio bytes for %s", recording_r2_key)
        return None

    client = genai.Client(api_key=settings.gemini_api_key)
    gemini_file = None

    try:
        # Detect MIME type from key extension; default to mp3
        key_lower = recording_r2_key.lower()
        if key_lower.endswith(".wav"):
            mime = "audio/wav"
        elif key_lower.endswith(".ogg"):
            mime = "audio/ogg"
        elif key_lower.endswith(".m4a"):
            mime = "audio/mp4"
        elif key_lower.endswith(".webm"):
            mime = "audio/webm"
        else:
            mime = "audio/mpeg"

        # Upload to Gemini Files API
        gemini_file = await client.aio.files.upload(
            file=io.BytesIO(audio_bytes),
            config=gtypes.UploadFileConfig(mime_type=mime),
        )
        logger.info("uploaded audio to Gemini Files: %s (app=%s)", gemini_file.name, application_id)

        # Build prompt with transcript + role context
        answers_json = json.dumps(answers, ensure_ascii=False)[:8000]
        prompt_text = _EVAL_PROMPT.format(
            role_title=role_title,
            jd_text=(jd_text or "")[:3000],
            ctc_min_lpa=ctc_min_lpa if ctc_min_lpa is not None else "n/a",
            ctc_max_lpa=ctc_max_lpa if ctc_max_lpa is not None else "n/a",
            max_notice_days=max_notice_days if max_notice_days is not None else "n/a",
            role_location=role_location or "n/a",
            remote_policy=remote_policy or "n/a",
            answers_json=answers_json,
        )

        response = await client.aio.models.generate_content(
            model=_GEMINI_MODEL,
            contents=[
                gtypes.Part.from_uri(file_uri=gemini_file.uri, mime_type=mime),
                prompt_text,
            ],
        )

        raw_text = response.text.strip()
        # Strip markdown fences if model wrapped the JSON
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(ln for ln in lines if not ln.startswith("```")).strip()

        data = json.loads(raw_text)
        score = VoiceCallScore.model_validate(data)
        logger.info(
            "Gemini eval done: app=%s verdict=%s score=%d",
            application_id, score.verdict, score.overall_score,
        )
        return score

    except Exception as exc:
        logger.warning(
            "Gemini voice eval failed for app=%s key=%s: %s",
            application_id, recording_r2_key, exc,
        )
        return None

    finally:
        # Best-effort cleanup of the uploaded Gemini file
        if gemini_file is not None:
            try:
                await client.aio.files.delete(name=gemini_file.name)
            except Exception as del_exc:
                logger.debug("Gemini file cleanup failed: %s", del_exc)
