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
    "CouponAnnie", "SimplyCodes", "Knoji", "Offers.com",
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
]

# AI engine names and generic non-brand terms that must never be matched as brands.
# Social platforms (Reddit, YouTube, etc.) are NOT blocked — if an AI engine
# recommends them, that's trackable data.
BRAND_BLOCKLIST = {
    "gemini", "claude", "chatgpt", "perplexity", "bing", "bard",
    "google", "google ai", "openai", "anthropic", "meta ai",
    "wikipedia", "wikihow", "medium", "substack", "wordpress",
    "visa", "mastercard", "apple",
}

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
_COUPON_MUST_HAVE_DIGIT = True
COUPON_CONTEXT_PATTERN = re.compile(
    r'\b(?:code|coupon|promo|voucher|discount code|use code|apply code|enter code)[:\s\-]+["\']?([A-Z][A-Z0-9]{2,14})["\']?\b',
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
    "CODE", "CODES", "COUPON", "COUPONS", "PROMO", "PROMOS",
    "VOUCHER", "VOUCHERS", "DISCOUNT", "DISCOUNTS",
    "DUNIA", "KARO", "GURU", "DIME", "MART", "RAJA", "WAPAS",
    "TIONAL", "TIONS", "IONAL", "ALLY", "MENT", "NESS",
    "AVAILABLE", "MENTIONED", "PROMOTIONAL", "INTERNATIONAL",
    "COUPONCODE", "PROMOCODE", "VOUCHERCODE", "DISCOUNTCODE",
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
        if brand_lower in BRAND_BLOCKLIST:
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
                if name.lower() in BRAND_BLOCKLIST:
                    continue
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
                       WHERE status_flag IN ('Active-Valid', 'AI-Mentioned')
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
            if name.lower() in BRAND_BLOCKLIST:
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

    def is_confident(self, text: str, regex_result: "ExtractedData | None" = None) -> bool:
        """Return True if regex brain captured enough of the text's brand landscape."""
        if len(self.learned_brands) < 20:
            return False

        regex_count = len(regex_result.brand_mentions) if regex_result and regex_result.brand_mentions else 0

        # Heuristic 1: numbered/bulleted lists suggest structured recommendations.
        # Count list items and compare to brands found.
        list_items = len(re.findall(r'(?m)^(?:\d+[\.\)]\s|[-*]\s)', text))
        if list_items >= 3 and regex_count < list_items - 1:
            return False

        # Heuristic 2: long text with very few brands — likely missed some.
        if len(text) > 600 and regex_count <= 1:
            return False

        # Heuristic 3: text has bold/header markers (**Name** or ### Name)
        # hinting at brand names the regex may not know.
        bold_names = re.findall(r'\*\*([A-Z][A-Za-z0-9. ]{2,25})\*\*', text)
        unknown_bolds = [n for n in bold_names
                         if n.lower().strip() not in self.known_lower
                         and n.lower().strip() not in BRAND_BLOCKLIST]
        if len(unknown_bolds) >= 2:
            return False

        # Heuristic 4: URLs pointing to domains regex doesn't know.
        url_domains = re.findall(r'https?://(?:www\.)?([a-z0-9-]+)\.[a-z]{2,}', text.lower())
        unknown_urls = [d for d in set(url_domains)
                        if d not in self.known_lower
                        and d not in BRAND_BLOCKLIST
                        and d not in {"google", "youtube", "wikipedia", "github", "t", "bit"}]
        if len(unknown_urls) >= 2 and regex_count < len(unknown_urls):
            return False

        return regex_count >= 2

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
    raw_matches = []
    seen_brands = set()

    for pat, canonical_name in _brain.brand_patterns:
        for match in pat.finditer(raw_text):
            brand_lower = canonical_name.lower()
            if brand_lower in seen_brands:
                continue
            seen_brands.add(brand_lower)
            raw_matches.append((match.start(), canonical_name, match.start(), match.end()))
            break

    raw_matches.sort(key=lambda x: x[0])

    mentions = []
    for rank, (_, canonical_name, m_start, m_end) in enumerate(raw_matches, 1):
        context = _extract_context(raw_text, m_start, m_end)
        sentiment = _detect_sentiment(canonical_name, context)
        cited_url = None
        url_match = re.search(
            r'https?://[^\s\)"\',<>]+' + re.escape(canonical_name.lower().replace(" ", "")),
            raw_text[:m_end + 500], re.IGNORECASE
        )
        if url_match:
            cited_url = url_match.group(0).rstrip(".,;:)")

        mentions.append({
            "rank_position": rank,
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
        coupons.append({
            "coupon_code": code,
            "associated_merchant": merchant,
            "status_flag": "AI-Mentioned",
        })

    for match in COUPON_PATTERN.finditer(raw_text):
        code = match.group(1)
        if code in SKIP_CODES or code in seen_codes or len(code) < 4:
            continue
        if _COUPON_MUST_HAVE_DIGIT and not any(c.isdigit() for c in code):
            continue

        if code in _brain.learned_coupons:
            seen_codes.add(code)
            merchant = _find_merchant_for_coupon(raw_text, code, match.start())
            coupons.append({
                "coupon_code": code,
                "associated_merchant": merchant,
                "status_flag": "AI-Mentioned",
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
            "status_flag": "AI-Mentioned",
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
Analyze the provided response text from a Generative AI engine and extract brands and coupon codes.

CRITICAL RULES:
- Extract ALL brands, websites, and platforms that are mentioned, recommended, or compared in the response — including coupon sites like GrabOn, GrabOn.in, GrabOn.com, CashKaro, CouponDunia, etc.
- Extract social platforms (Reddit, YouTube, Instagram, etc.) if they are mentioned or recommended in the response.
- Do NOT extract the AI engine's own name (ChatGPT, Claude, Gemini, Perplexity, etc.).
- Do NOT infer or guess brands that are not literally present in the text.
- Assign rank_position by order of first meaningful appearance (1-indexed).

1. Brand mentions: extract brand_name, rank_position, sentiment (Positive/Neutral/Negative), context_snippet (the sentence where it appears), and cited_url if present.

2. Coupon/promo codes: extract coupon_code, associated_merchant, and set status_flag to 'AI-Mentioned'.

Always output valid JSON conforming to the requested schema.

JSON schema:
{
  "brand_mentions": [
    {"rank_position": int, "brand_name": str, "sentiment": "Positive"|"Neutral"|"Negative", "context_snippet": str, "cited_url": str|null}
  ],
  "ai_hallucinated_coupons": [
    {"coupon_code": str, "associated_merchant": str, "status_flag": "AI-Mentioned"}
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
    from openai import OpenAI, RateLimitError, APIStatusError

    model = "gpt-4o-mini"
    client = OpenAI(api_key=api_key)
    max_retries = 3
    backoff = 2

    for attempt in range(max_retries):
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
        except RateLimitError as e:
            wait = backoff ** (attempt + 1)
            log.warning(f"OpenAI rate limit (attempt {attempt+1}/{max_retries}), retrying in {wait}s: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
        except APIStatusError as e:
            if e.status_code >= 500:
                wait = backoff ** (attempt + 1)
                log.warning(f"OpenAI server error {e.status_code} (attempt {attempt+1}/{max_retries}), retrying in {wait}s")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
            else:
                log.warning(f"OpenAI parse failed (non-retryable): {e}")
                return None
        except Exception as e:
            log.warning(f"OpenAI parse failed: {e}")
            return None

    log.error("OpenAI parse failed after all retries")
    return None


# ══════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════

_SOURCE_LABEL_RE = re.compile(
    r'^(?:Source|Sources|Via|From|Cited from|Reference|References|Attribution'
    r'|Powered by|According to|Based on|Data from|Info from|Retrieved from)[:\s]',
    re.IGNORECASE,
)

def _pre_clean_for_parser(raw_text: str) -> str:
    """Strip residual noise before sending to LLM or regex parser."""
    text = re.sub(r"<tool_calls>.*?</tool_calls>", "", raw_text, flags=re.DOTALL)
    text = re.sub(r"<search_web>.*?</search_web>", "", text, flags=re.DOTALL)
    text = re.sub(r"<(?:thinking|reflection)>.*?</(?:thinking|reflection)>", "", text, flags=re.DOTALL)

    # Strip citation/source footnote sections (often at end of AI responses)
    text = re.sub(r'\n---+\s*\n.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\[?\d+\]\s*https?://\S+', '', text)

    lines = text.split("\n")
    seen = set()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)

        # Drop only AI engine source labels, not brand names we want to track
        if len(stripped.split()) <= 2 and stripped.lower() in BRAND_BLOCKLIST:
            continue
        if _SOURCE_LABEL_RE.match(stripped):
            continue

        cleaned.append(stripped)
    result = "\n".join(cleaned)
    if len(result) > 8000:
        result = result[:8000]
    return result


def _get_brand(m) -> str:
    return m.get("brand_name", "") if isinstance(m, dict) else getattr(m, "brand_name", "")


def _get_rank(m) -> int:
    return m.get("rank_position", 0) if isinstance(m, dict) else getattr(m, "rank_position", 0)


def _set_rank(m, rank: int):
    if isinstance(m, dict):
        m["rank_position"] = rank
    else:
        m.rank_position = rank


def _get_coupon(c) -> str:
    return c.get("coupon_code", "") if isinstance(c, dict) else getattr(c, "coupon_code", "")


def _validate_rank_positions(result: ExtractedData, raw_text: str) -> ExtractedData:
    """Re-rank brand mentions by their actual first appearance in the text."""
    if not result.brand_mentions or len(result.brand_mentions) <= 1:
        return result

    positioned = []
    for m in result.brand_mentions:
        brand = _get_brand(m)
        pos = -1
        for pat, canonical in _brain.brand_patterns:
            if canonical.lower() == brand.lower():
                match = pat.search(raw_text)
                if match:
                    pos = match.start()
                break
        if pos == -1:
            pos = raw_text.lower().find(brand.lower())
        if pos == -1:
            pos = len(raw_text)
        positioned.append((pos, m))

    positioned.sort(key=lambda x: x[0])

    for new_rank, (_, m) in enumerate(positioned, 1):
        old_rank = _get_rank(m)
        if old_rank != new_rank:
            log.debug(f"Rank corrected: {_get_brand(m)} #{old_rank} -> #{new_rank}")
        _set_rank(m, new_rank)

    result.brand_mentions = [m for _, m in positioned]
    return result


async def parse_response(engine_name: str, prompt_text: str, raw_text: str) -> ExtractedData | None:
    settings = get_settings()
    await _brain.ensure_ready()

    cleaned_text = _pre_clean_for_parser(raw_text)
    if len(cleaned_text) < 30:
        log.warning(f"[{engine_name}] Text too short after pre-clean ({len(cleaned_text)} chars), skipping parse")
        return ExtractedData(brand_mentions=[], ai_hallucinated_coupons=[])

    # Always run regex first (free, fast)
    regex_result = _parse_with_regex(engine_name, prompt_text, cleaned_text)

    # Check if regex captured enough — pass result so heuristics can inspect gaps
    if _brain.is_confident(cleaned_text, regex_result):
        if regex_result and regex_result.brand_mentions:
            log.info(f"[{engine_name}] Regex brain confident ({len(regex_result.brand_mentions)} brands) — skipped LLM")
            return _validate_rank_positions(regex_result, cleaned_text)

    # Regex missed something — call LLM for full extraction
    if settings.openai_api_key:
        try:
            user_content = _build_user_content(engine_name, prompt_text, cleaned_text)
            llm_result = await _parse_with_openai(user_content, settings.openai_api_key)
            if llm_result:
                _brain.learn_from_llm_result(cleaned_text, llm_result)
                # Merge: keep LLM brands but add any regex-only brands it missed
                if regex_result and regex_result.brand_mentions:
                    llm_brands = {_get_brand(m).lower() for m in llm_result.brand_mentions}
                    for rm in regex_result.brand_mentions:
                        if _get_brand(rm).lower() not in llm_brands:
                            llm_result.brand_mentions.append(rm)
                    llm_codes = {_get_coupon(c).upper() for c in llm_result.ai_hallucinated_coupons}
                    for rc in regex_result.ai_hallucinated_coupons:
                        if _get_coupon(rc).upper() not in llm_codes:
                            llm_result.ai_hallucinated_coupons.append(rc)
                return _validate_rank_positions(llm_result, cleaned_text)
        except Exception as e:
            log.warning(f"OpenAI failed ({e}), using regex result")

    # No API key or LLM failed — regex is all we have
    if regex_result and regex_result.brand_mentions:
        log.info(f"[{engine_name}] Regex fallback ({len(regex_result.brand_mentions)} brands)")
    return _validate_rank_positions(regex_result, cleaned_text)
