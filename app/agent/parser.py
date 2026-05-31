import asyncio
import json
import logging
from app.models import ExtractedData
from app.config import get_settings
from app.database import run_db

log = logging.getLogger("geo.parser")

COST_PER_MILLION = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
}


async def _log_cost(provider: str, model: str, input_tokens: int, output_tokens: int):
    rates = COST_PER_MILLION.get(model, {"input": 0, "output": 0})
    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    if cost <= 0:
        return
    def _insert(conn):
        conn.execute(
            """INSERT INTO api_costs (provider, model, input_tokens, output_tokens, cost_usd, purpose)
               VALUES (%s, %s, %s, %s, %s, 'parser')""",
            (provider, model, input_tokens, output_tokens, cost),
        )
        conn.commit()
    try:
        await run_db(_insert)
    except Exception as e:
        log.warning(f"Failed to log API cost: {e}")


SYSTEM_PROMPT = """You are an expert SEO Auditing LLM Judge.
Analyze the provided conversational/search response text from a Generative AI engine and extract:

1. All brand mentions (names like GrabOn, CashKaro, CouponDunia, DesiDime, Grabon.in, etc.).
   Assign rank placement order (1-indexed based on first appearance).
   Identify sentiment and pull exact context snippets.

2. Any coupon/promo codes mentioned, matching them with their merchant, classifying status:
   - 'Active-Valid': marked as working, active, or verified
   - 'Expired-On-Site': marked as expired or old
   - 'Hallucinated': fabricated, unrecognized, or no evidence of validity

Always output valid JSON conforming to the requested schema.

JSON schema:
{
  "brand_mentions": [
    {"rank_position": int, "brand_name": str, "sentiment": "Positive"|"Neutral"|"Negative", "context_snippet": str, "cited_url": str|null}
  ],
  "ai_hallucinated_coupons": [
    {"coupon_code": str, "associated_merchant": str, "status_flag": "Active-Valid"|"Expired-On-Site"|"Hallucinated"}
  ]
}"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "brand_mentions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "rank_position": {"type": "INTEGER"},
                    "brand_name": {"type": "STRING"},
                    "sentiment": {"type": "STRING", "enum": ["Positive", "Neutral", "Negative"]},
                    "context_snippet": {"type": "STRING"},
                    "cited_url": {"type": "STRING"},
                },
                "required": ["rank_position", "brand_name", "sentiment", "context_snippet"],
            },
        },
        "ai_hallucinated_coupons": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "coupon_code": {"type": "STRING"},
                    "associated_merchant": {"type": "STRING"},
                    "status_flag": {
                        "type": "STRING",
                        "enum": ["Active-Valid", "Expired-On-Site", "Hallucinated"],
                    },
                },
                "required": ["coupon_code", "associated_merchant", "status_flag"],
            },
        },
    },
    "required": ["brand_mentions", "ai_hallucinated_coupons"],
}


def _build_user_content(engine_name: str, prompt_text: str, raw_text: str) -> str:
    return (
        f'Engine: {engine_name}\n'
        f'Prompt asked: "{prompt_text}"\n\n'
        f'Response text:\n"""\n{raw_text}\n"""'
    )


def _parse_json_result(text: str) -> ExtractedData | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    data = json.loads(text)
    mentions = [m for m in data.get("brand_mentions", []) if m.get("brand_name")]
    coupons = [c for c in data.get("ai_hallucinated_coupons", []) if c.get("coupon_code")]
    result = ExtractedData(
        brand_mentions=mentions,
        ai_hallucinated_coupons=coupons,
    )
    log.info(f"Parsed {len(result.brand_mentions)} brand mentions, {len(result.ai_hallucinated_coupons)} coupons")
    return result


GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
]


async def _parse_with_groq(user_content: str, api_key: str) -> ExtractedData | None:
    from groq import Groq

    client = Groq(api_key=api_key)
    for model in GROQ_MODELS:
        try:
            def _sync(m=model):
                return client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=4096,
                )

            response = await asyncio.to_thread(_sync)
            text = response.choices[0].message.content
            if not text:
                continue
            return _parse_json_result(text)
        except Exception as e:
            log.warning(f"Groq {model} failed: {e}")
            continue
    return None


GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
]


async def _parse_with_gemini(user_content: str, api_key: str) -> ExtractedData | None:
    from google import genai

    client = genai.Client(api_key=api_key)
    for model in GEMINI_MODELS:
        try:
            def _sync(m=model):
                return client.models.generate_content(
                    model=m,
                    contents=user_content,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=RESPONSE_SCHEMA,
                    ),
                )

            response = await asyncio.to_thread(_sync)
            text = response.text
            if not text:
                continue
            return _parse_json_result(text)
        except Exception as e:
            log.warning(f"Gemini {model} failed: {e}")
            continue
    return None


async def _parse_with_openai(user_content: str, api_key: str) -> ExtractedData | None:
    from openai import OpenAI

    model = "gpt-4o-mini"
    client = OpenAI(api_key=api_key)
    try:
        def _sync():
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4096,
            )

        response = await asyncio.to_thread(_sync)
        usage = response.usage
        if usage:
            await _log_cost("openai", model, usage.prompt_tokens, usage.completion_tokens)
        text = response.choices[0].message.content
        if not text:
            return None
        return _parse_json_result(text)
    except Exception as e:
        log.warning(f"OpenAI {model} failed: {e}")
        return None


async def parse_response(engine_name: str, prompt_text: str, raw_text: str) -> ExtractedData | None:
    settings = get_settings()
    user_content = _build_user_content(engine_name, prompt_text, raw_text)

    # OpenAI first (reliable, cheap gpt-4o-mini), then free fallbacks
    if settings.openai_api_key:
        try:
            result = await _parse_with_openai(user_content, settings.openai_api_key)
            if result:
                return result
        except Exception as e:
            log.warning(f"OpenAI parsing failed ({e}), falling back to Groq")

    if settings.groq_api_key:
        try:
            result = await _parse_with_groq(user_content, settings.groq_api_key)
            if result:
                return result
        except Exception as e:
            log.warning(f"Groq parsing failed ({e}), falling back to Gemini")

    if settings.gemini_api_key:
        try:
            return await _parse_with_gemini(user_content, settings.gemini_api_key)
        except Exception as e:
            log.error(f"Gemini parsing failed: {e}")
            return None

    log.error("No parser API key configured (set OPENAI_API_KEY, GROQ_API_KEY, or GEMINI_API_KEY)")
    return None
