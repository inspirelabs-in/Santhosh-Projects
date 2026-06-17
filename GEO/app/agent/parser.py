import asyncio
import json
import re
import logging
import time
from app.models import ExtractedData
from app.config import get_settings
from app.database import run_db

log = logging.getLogger("geo.parser")


# ══════════════════════════════════════════════════════════════════════
#  Self-learning regex parser
#  Learns new brands, sentiment patterns, and coupon codes from
#  successful OpenAI parses. Gets smarter over time.
# ══════════════════════════════════════════════════════════════════════

KNOWN_BRANDS = [
    "GrabOn", "CouponDunia", "DesiDime", "CashKaro", "GoPaisa", "PaisaWapas",
    "Zingoy", "MagicPin", "CouponzGuru", "Zoutons", "WeThrift", "CouponFollow",
    "Groupon", "RetailMeNot", "Honey", "Woohoo", "Gyftr",
    "Flipkart", "Amazon", "Myntra", "Ajio", "Nykaa", "Meesho", "Tata CLiQ",
    "Swiggy", "Zomato", "BookMyShow", "MakeMyTrip", "Goibibo", "Cleartrip",
    "Paytm", "PhonePe", "MobiKwik", "Freecharge", "Amazon Pay", "Google Pay",
    "HDFC", "ICICI", "SBI", "Axis Bank", "Kotak", "Citi",
    "Airtel", "Jio", "Vi", "BSNL",
    "OYO", "Uber", "Ola", "Rapido", "RedBus", "AbhiBus",
    "Air India", "IndiGo", "SpiceJet", "Vistara",
    "BigBasket", "JioMart", "Blinkit", "Zepto", "Dunzo",
    "Domino's", "Pizza Hut", "McDonald's", "KFC", "Burger King",
    "HealthKart", "PharmEasy", "Netmeds", "1mg", "Apollo",
    "Lenskart", "Titan", "Boat", "Noise", "Realme", "Samsung",
    "GoDaddy", "Hostinger", "Bluehost", "Namecheap",
    "Urban Company", "Cashify", "Croma", "Reliance Digital",
    "FirstCry", "Mamaearth", "WOW Skin Science", "mCaffeine",
    "Shoppers Stop", "Lifestyle", "Pepperfry", "Urban Ladder",
    "Reddit", "Quora",
]

NEGATIVE_WORDS = {
    "worst", "terrible", "avoid", "scam", "fake", "fraud", "unreliable",
    "bad", "poor", "useless", "disappointing", "horrible", "never", "don't",
    "spam", "misleading", "outdated", "expired", "doesn't work", "not working",
    "waste", "ripoff", "rip-off", "beware", "sketchy", "shady",
}
POSITIVE_WORDS = {
    "best", "great", "excellent", "reliable", "trusted", "legit", "legitimate",
    "top", "recommend", "popular", "verified", "working", "active", "good",
    "genuine", "authentic", "official", "reputable", "leading", "favorite",
    "famous", "well-known", "widely used", "worth", "helpful", "effective",
    "savings", "discount", "cashback", "deals",
}

COUPON_PATTERN = re.compile(r'\b([A-Z][A-Z0-9]{2,14})\b')
COUPON_CONTEXT_PATTERN = re.compile(
    r'(?:code|coupon|promo|voucher|offer|discount code|use|apply|enter)[:\s\-]*["\']?([A-Z][A-Z0-9]{2,14})["\']?\b',
    re.IGNORECASE,
)
SKIP_CODES = {
    "CEO", "CFO", "CTO", "COO", "USA", "USD", "INR", "OTP", "EMI", "GST",
    "PIN", "APK", "APP", "FAQ", "URL", "API", "PDF", "HTML", "CSS", "UPI",
    "SBI", "RBI", "NRI", "PAN", "KYC", "ATM", "NEFT", "RTGS", "IMPS",
    "VISA", "HDFC", "ICICI", "BSNL", "IRCTC", "BOGO",
    "THE", "AND", "FOR", "ARE", "NOT", "YOU", "CAN", "HAS", "HAD", "HIS",
    "HER", "ITS", "OUR", "WHO", "ALL", "NEW", "GET", "USE", "HOW", "WHY",
    "ALSO", "JUST", "LIKE", "MAKE", "KNOW", "TAKE", "COME", "SOME", "THAN",
    "THEM", "THEN", "WHAT", "WHEN", "FROM", "WILL", "EACH", "BEEN", "HAVE",
    "WITH", "THIS", "THAT", "THEY", "DOES", "MUCH", "VERY", "MOST", "MANY",
    "ONLY", "OVER", "SUCH", "INTO", "YEAR", "YOUR", "MORE", "WERE",
    "INDIA", "HERE", "FIND", "BEST", "SAVE", "GRAB", "DEAL", "FREE",
    "FLAT", "SITE", "SIGN", "SHOP", "LOOK", "HELP", "NEED", "STEP",
    "WORK", "CLICK", "VISIT", "CHECK", "APPLY", "ENTER", "USING",
    "FIRST", "OFFER", "PRICE", "ORDER", "TODAY", "VALID", "ABOUT",
    "BELOW", "ABOVE", "AFTER", "UNDER", "THESE", "THOSE", "OTHER",
    "WHERE", "WHICH", "WHILE", "BEING", "STILL", "MIGHT", "WOULD",
    "COULD", "SHOULD", "EVERY", "NEVER", "OFTEN", "SINCE",
}


# ── Self-learning state ──────────────────────────────────────────────

class _RegexBrain:
    """Accumulates knowledge from successful LLM parses to improve regex."""

    def __init__(self):
        self.brand_patterns: list[tuple[re.Pattern, str]] = []
        self.known_lower: set[str] = set()
        self.learned_brands: set[str] = set()
        self.learned_sentiment: dict[str, list[str]] = {}
        self.learned_coupons: set[str] = set()
        self.learned_merchants: dict[str, str] = {}
        self._initialized = False
        self._last_db_refresh = 0.0
        self._db_refresh_interval = 300

    def _build_base_patterns(self):
        if self.brand_patterns:
            return
        for brand in KNOWN_BRANDS:
            self._add_brand_pattern(brand)

    def _add_brand_pattern(self, brand: str):
        brand_lower = brand.lower()
        if brand_lower in self.known_lower:
            return
        self.known_lower.add(brand_lower)
        escaped = re.escape(brand)
        pat = re.compile(r'\b' + escaped + r'(?:\.(?:com|in|co\.in))?\b', re.IGNORECASE)
        self.brand_patterns.append((pat, brand))

    async def refresh_from_db(self):
        now = time.time()
        if now - self._last_db_refresh < self._db_refresh_interval:
            return
        self._last_db_refresh = now

        try:
            def _query(conn):
                rows = conn.execute(
                    """SELECT brand_name, COUNT(*) as cnt
                       FROM brand_mentions
                       GROUP BY brand_name HAVING COUNT(*) >= 3
                       ORDER BY cnt DESC LIMIT 300"""
                ).fetchall()
                return rows
            rows = await run_db(_query)
            new_count = 0
            for r in rows:
                name = r["brand_name"]
                if name.lower() not in self.known_lower and len(name) > 2:
                    self._add_brand_pattern(name)
                    self.learned_brands.add(name)
                    new_count += 1
            if new_count:
                log.info(f"[regex-brain] Learned {new_count} new brands from DB (total: {len(self.brand_patterns)})")
        except Exception as e:
            log.warning(f"[regex-brain] DB refresh failed: {e}")

        try:
            def _query_coupons(conn):
                rows = conn.execute(
                    """SELECT coupon_code, associated_merchant
                       FROM ai_hallucinated_coupons
                       WHERE status_flag = 'Active-Valid'
                         AND created_at >= NOW() - INTERVAL '30 days'
                       GROUP BY coupon_code, associated_merchant
                       ORDER BY MAX(created_at) DESC LIMIT 500"""
                ).fetchall()
                return rows
            coupon_rows = await run_db(_query_coupons)
            for r in coupon_rows:
                code = r["coupon_code"].upper()
                if len(code) >= 3 and code not in SKIP_CODES:
                    self.learned_coupons.add(code)
                    if r["associated_merchant"] and r["associated_merchant"] != "Unknown":
                        self.learned_merchants[code] = r["associated_merchant"]
            if coupon_rows:
                log.info(f"[regex-brain] Loaded {len(self.learned_coupons)} known-good coupon codes from DB")
        except Exception as e:
            log.warning(f"[regex-brain] Coupon refresh failed: {e}")

        try:
            def _query_sentiment(conn):
                rows = conn.execute(
                    """SELECT brand_name, sentiment, COUNT(*) as cnt
                       FROM brand_mentions
                       WHERE created_at >= NOW() - INTERVAL '7 days'
                       GROUP BY brand_name, sentiment
                       ORDER BY brand_name, cnt DESC"""
                ).fetchall()
                return rows
            sent_rows = await run_db(_query_sentiment)
            brand_sent: dict[str, dict[str, int]] = {}
            for r in sent_rows:
                bn = r["brand_name"].lower()
                if bn not in brand_sent:
                    brand_sent[bn] = {}
                brand_sent[bn][r["sentiment"]] = r["cnt"]
            self.learned_sentiment = {}
            for bn, sents in brand_sent.items():
                dominant = max(sents, key=sents.get)
                total = sum(sents.values())
                if sents[dominant] / total >= 0.6 and total >= 5:
                    self.learned_sentiment[bn] = dominant
            if self.learned_sentiment:
                log.info(f"[regex-brain] Learned sentiment trends for {len(self.learned_sentiment)} brands")
        except Exception as e:
            log.warning(f"[regex-brain] Sentiment refresh failed: {e}")

    def learn_from_llm_result(self, raw_text: str, result: ExtractedData):
        new_brands = 0
        for m in result.brand_mentions:
            name = m.get("brand_name") if isinstance(m, dict) else m.brand_name
            if not name or len(name) < 2:
                continue
            if name.lower() not in self.known_lower:
                if re.search(r'\b' + re.escape(name) + r'\b', raw_text, re.IGNORECASE):
                    self._add_brand_pattern(name)
                    self.learned_brands.add(name)
                    new_brands += 1

        coupons_list = result.ai_hallucinated_coupons
        for c in coupons_list:
            code = (c.get("coupon_code") if isinstance(c, dict) else c.coupon_code) or ""
            merchant = (c.get("associated_merchant") if isinstance(c, dict) else c.associated_merchant) or ""
            code = code.upper()
            if len(code) >= 3 and code not in SKIP_CODES:
                self.learned_coupons.add(code)
                if merchant and merchant != "Unknown":
                    self.learned_merchants[code] = merchant

        if new_brands:
            log.info(f"[regex-brain] Learned {new_brands} new brands from LLM parse")

    async def ensure_ready(self):
        self._build_base_patterns()
        await self.refresh_from_db()


_brain = _RegexBrain()


# ── Regex extraction functions ───────────────────────────────────────

def _extract_context(text: str, match_start: int, match_end: int, window: int = 120) -> str:
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    snippet = text[start:end].strip()
    snippet = re.sub(r'\s+', ' ', snippet)
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet[:300]


def _detect_sentiment(brand_name: str, context: str) -> str:
    bn_lower = brand_name.lower()
    if bn_lower in _brain.learned_sentiment:
        return _brain.learned_sentiment[bn_lower]

    ctx_lower = context.lower()
    neg_score = sum(1 for w in NEGATIVE_WORDS if w in ctx_lower)
    pos_score = sum(1 for w in POSITIVE_WORDS if w in ctx_lower)

    ranking_match = re.search(r'(?:#\s*1|number\s*one|top\s+pick|first\s+choice|best\s+(?:overall|option))', ctx_lower)
    if ranking_match and brand_name.lower() in ctx_lower[max(0, ranking_match.start()-50):ranking_match.end()+50].lower():
        pos_score += 2

    warning_phrases = ["be careful", "check before", "not always", "may not", "some users report"]
    for phrase in warning_phrases:
        if phrase in ctx_lower:
            neg_score += 1

    if neg_score > pos_score:
        return "Negative"
    if pos_score > neg_score:
        return "Positive"
    return "Neutral"


def _find_merchant_for_coupon(text: str, code: str, code_pos: int) -> str:
    if code in _brain.learned_merchants:
        return _brain.learned_merchants[code]

    search_window = text[max(0, code_pos - 200):code_pos + 200]
    for pat, name in _brain.brand_patterns:
        if pat.search(search_window):
            return name
    return "Unknown"


def _parse_with_regex(engine_name: str, prompt_text: str, raw_text: str) -> ExtractedData:
    mentions = []
    seen_brands = set()

    for pat, canonical_name in _brain.brand_patterns:
        for match in pat.finditer(raw_text):
            brand_lower = canonical_name.lower()
            if brand_lower in seen_brands:
                continue
            seen_brands.add(brand_lower)
            context = _extract_context(raw_text, match.start(), match.end())
            sentiment = _detect_sentiment(canonical_name, context)
            cited_url = None
            url_match = re.search(
                r'https?://[^\s\)"\',<>]+' + re.escape(canonical_name.lower().replace(" ", "")),
                raw_text[:match.end() + 500], re.IGNORECASE
            )
            if url_match:
                cited_url = url_match.group(0).rstrip(".,;:)")

            mentions.append({
                "rank_position": len(mentions) + 1,
                "brand_name": canonical_name,
                "sentiment": sentiment,
                "context_snippet": context,
                "cited_url": cited_url,
            })

    coupons = []
    seen_codes = set()

    for match in COUPON_CONTEXT_PATTERN.finditer(raw_text):
        code = match.group(1).upper()
        if code in SKIP_CODES or code in seen_codes or len(code) < 3:
            continue
        seen_codes.add(code)
        merchant = _find_merchant_for_coupon(raw_text, code, match.start())
        is_known = code in _brain.learned_coupons
        coupons.append({
            "coupon_code": code,
            "associated_merchant": merchant,
            "status_flag": "Active-Valid" if is_known else "Hallucinated",
        })

    for match in COUPON_PATTERN.finditer(raw_text):
        code = match.group(1)
        if code in SKIP_CODES or code in seen_codes or len(code) < 4:
            continue

        if code in _brain.learned_coupons:
            seen_codes.add(code)
            merchant = _find_merchant_for_coupon(raw_text, code, match.start())
            coupons.append({
                "coupon_code": code,
                "associated_merchant": merchant,
                "status_flag": "Active-Valid",
            })
            continue

        context_start = max(0, match.start() - 80)
        context = raw_text[context_start:match.end() + 80].lower()
        is_coupon_context = any(w in context for w in [
            "code", "coupon", "promo", "voucher", "discount", "offer",
            "apply", "use", "enter", "redeem", "get", "save", "%", "off",
            "cashback", "flat",
        ])
        if not is_coupon_context:
            continue
        seen_codes.add(code)
        merchant = _find_merchant_for_coupon(raw_text, code, match.start())
        coupons.append({
            "coupon_code": code,
            "associated_merchant": merchant,
            "status_flag": "Hallucinated",
        })

    log.info(f"[regex] Parsed {len(mentions)} brands, {len(coupons)} coupons "
             f"(brain: {len(_brain.brand_patterns)} patterns, {len(_brain.learned_coupons)} known codes)")
    return ExtractedData(brand_mentions=mentions, ai_hallucinated_coupons=coupons)


# ══════════════════════════════════════════════════════════════════════
#  OpenAI parser
# ══════════════════════════════════════════════════════════════════════

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
    log.info(f"[openai] Parsed {len(result.brand_mentions)} brands, {len(result.ai_hallucinated_coupons)} coupons")
    return result


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
        log.warning(f"OpenAI parse failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════

def _pre_clean_for_parser(raw_text: str) -> str:
    """Strip residual noise before sending to LLM or regex parser."""
    text = re.sub(r"<tool_calls>.*?</tool_calls>", "", raw_text, flags=re.DOTALL)
    text = re.sub(r"<search_web>.*?</search_web>", "", text, flags=re.DOTALL)
    text = re.sub(r"<(?:thinking|reflection)>.*?</(?:thinking|reflection)>", "", text, flags=re.DOTALL)
    lines = text.split("\n")
    seen = set()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in seen and len(stripped) < 120:
            continue
        seen.add(stripped)
        cleaned.append(stripped)
    return "\n".join(cleaned)


async def parse_response(engine_name: str, prompt_text: str, raw_text: str) -> ExtractedData | None:
    settings = get_settings()
    await _brain.ensure_ready()

    cleaned_text = _pre_clean_for_parser(raw_text)
    if len(cleaned_text) < 30:
        log.warning(f"[{engine_name}] Text too short after pre-clean ({len(cleaned_text)} chars), skipping parse")
        return ExtractedData(brand_mentions=[], ai_hallucinated_coupons=[])

    # Try OpenAI first
    if settings.openai_api_key:
        try:
            user_content = _build_user_content(engine_name, prompt_text, cleaned_text)
            llm_result = await _parse_with_openai(user_content, settings.openai_api_key)
            if llm_result:
                _brain.learn_from_llm_result(cleaned_text, llm_result)
                return llm_result
        except Exception as e:
            log.warning(f"OpenAI failed ({e}), using regex parser")

    # Regex fallback
    log.info(f"Using regex parser for {engine_name}")
    return _parse_with_regex(engine_name, prompt_text, cleaned_text)
