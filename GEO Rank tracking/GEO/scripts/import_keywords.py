"""
Import keywords from Excel into the prompts table.
Deduplicates, classifies, and assigns tiers.

Usage:
    python -m scripts.import_keywords
    python -m scripts.import_keywords --dry-run
"""

import re
import sys
import logging
import argparse
from pathlib import Path
from collections import defaultdict

import openpyxl
import psycopg
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from app.config import get_settings

log = logging.getLogger("import_keywords")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# ---------------------------------------------------------------------------
# Brand aliases: normalize variant spellings → canonical brand key
# ---------------------------------------------------------------------------
BRAND_ALIASES = {
    "amazon india": "amazon", "amazon prime": "amazon", "amazonprime": "amazon",
    "make my trip": "makemytrip", "mmt": "makemytrip",
    "red bus": "redbus",
    "big basket": "bigbasket",
    "jio mart": "jiomart", "jio recharge": "jio", "jiorecharge": "jio",
    "domino's": "dominos", "dominospizza": "dominos", "domino's pizza": "dominos",
    "mcdonald's": "mcdonalds",
    "dream 11": "dream11",
    "my 11 circle": "my11circle", "my11circle": "my11circle",
    "burger king": "burgerking",
    "pizza hut": "pizzahut",
    "urban company": "urbancompany", "urbanclap": "urbancompany",
    "swiggy instamart": "swiggy", "instamart": "swiggy",
    "paytm bus": "paytm", "paytmflight": "paytm", "paytm flight": "paytm",
    "dot and key": "dotandkey",
    "country delight": "countrydelight",
    "pocket fm": "pocketfm",
    "kuku fm": "kukufm",
    "chatgpt plus": "chatgpt",
    "booking.com": "bookingcom",
    "h&m": "hm", "handm": "hm",
    "air india": "airindia", "airindia": "airindia",
    "airtel recharge": "airtel", "airtelrecharge": "airtel",
    "tata neu": "tataneu", "tataneu": "tataneu",
    "trip.com": "tripcom",
    "zee5subscription": "zee5", "zee5 subscription": "zee5",
    "hostingerdomain": "hostinger", "hostinger domain": "hostinger",
    "apple store": "apple", "applestore": "apple",
    "home centre": "homecentre",
    "ferns n petals": "fnp", "ferns and petals": "fnp",
    "shoppers stop": "shoppersstop",
    "cult.fit": "cultfit", "cure.fit": "cultfit",
    "sony liv": "sonyliv",
}

# ---------------------------------------------------------------------------
# Tier 1 — top Indian brands (daily scrape)
# ---------------------------------------------------------------------------
TIER_1_BRANDS = {
    "amazon", "flipkart", "myntra", "ajio", "swiggy", "zomato", "zepto",
    "blinkit", "redbus", "dominos", "rapido", "paytm", "bookmyshow",
    "ola", "uber", "nykaa", "makemytrip", "goibibo", "indigo", "kfc",
    "jiomart", "dream11", "pizzahut", "burgerking", "urbancompany",
    "lenskart", "puma", "adidas", "nike", "firstcry", "samsung",
    "phonepe", "bigbasket", "croma", "decathlon", "jio",
    "bewakoof", "pvr", "netflix", "ikea", "hm", "westside", "meesho",
    "tatacliq", "boat", "mamaearth", "mcdonalds",
    "abhibus", "airindia", "airtel", "tataneu", "starbucks",
    "1mg", "zara", "uniqlo", "crocs",
}

# ---------------------------------------------------------------------------
# Tier 2 — medium brands (weekly scrape)
# ---------------------------------------------------------------------------
TIER_2_BRANDS = {
    "hostinger", "cashify", "agoda", "godaddy", "udemy", "porter",
    "kreditbee", "traya", "aha", "bookingcom", "testbook",
    "manychat", "flixbus", "pocketfm", "chaupal", "lenovo", "purple",
    "cinepolis", "beardo", "zee5", "mobikwik", "muscleblaze", "myprotein",
    "coursera", "foxtale", "imagica", "snitch", "kukufm", "radisson",
    "emirates", "chatgpt", "midjourney", "countrydelight", "dotandkey",
    "my11circle", "redrail", "deodap", "savana", "max", "district",
    "ticketnew", "cleartrip", "treebo", "oyo", "vi",
    "zudio", "reliance", "tanishq", "titan", "fabindia", "shoppersstop",
    "lifestyle", "sephora", "tira", "noise", "realme", "oneplus",
    "apple", "dell", "hp", "asus", "acer", "mi", "oppo", "vivo",
    "cultfit", "fnp", "igp",
    "sonyliv", "easemytrip", "ixigo", "yatra", "zoomcar", "indrive",
    "magicpin", "yesmadam", "nordvpn", "wonderla", "zostel",
    "giva", "gnc", "medplus", "freecharge", "homecentre", "tripcom",
    "astrotalk", "headphonezone", "steam",
}

# ---------------------------------------------------------------------------
# Brand → category mapping
# ---------------------------------------------------------------------------
BRAND_CATEGORY_MAP = {
    # Fashion
    "myntra": "Fashion", "ajio": "Fashion", "puma": "Fashion", "adidas": "Fashion",
    "nike": "Fashion", "hm": "Fashion", "westside": "Fashion", "bewakoof": "Fashion",
    "snitch": "Fashion", "meesho": "Fashion", "max": "Fashion", "zudio": "Fashion",
    "fabindia": "Fashion", "shoppers stop": "Fashion", "lifestyle": "Fashion",
    # Food & Delivery
    "swiggy": "Food", "zomato": "Food", "zepto": "Food", "blinkit": "Food",
    "dominos": "Food", "kfc": "Food", "burgerking": "Food", "pizzahut": "Food",
    "mcdonalds": "Food", "bigbasket": "Food", "jiomart": "Food",
    "countrydelight": "Food",
    # Electronics
    "amazon": "Electronics", "flipkart": "Electronics", "croma": "Electronics",
    "samsung": "Electronics", "lenovo": "Electronics", "ikea": "Electronics",
    "realme": "Electronics", "oneplus": "Electronics", "apple": "Electronics",
    "dell": "Electronics", "hp": "Electronics", "asus": "Electronics",
    "acer": "Electronics", "mi": "Electronics", "oppo": "Electronics",
    "vivo": "Electronics", "noise": "Electronics", "boat": "Electronics",
    "tatacliq": "Electronics",
    # Travel
    "redbus": "Travel", "makemytrip": "Travel", "goibibo": "Travel",
    "indigo": "Travel", "agoda": "Travel", "bookingcom": "Travel",
    "cleartrip": "Travel", "abhibus": "Travel", "rapido": "Travel",
    "ola": "Travel", "uber": "Travel", "emirates": "Travel",
    "radisson": "Travel", "treebo": "Travel", "oyo": "Travel",
    "flixbus": "Travel", "redrail": "Travel", "porter": "Travel",
    # Entertainment
    "bookmyshow": "Entertainment", "pvr": "Entertainment", "netflix": "Entertainment",
    "zee5": "Entertainment", "aha": "Entertainment", "chaupal": "Entertainment",
    "pocketfm": "Entertainment", "kukufm": "Entertainment",
    "cinepolis": "Entertainment", "imagica": "Entertainment",
    "ticketnew": "Entertainment",
    # Telecom
    "jio": "Telecom", "airtel": "Telecom", "vi": "Telecom", "paytm": "Telecom",
    "phonepe": "Telecom", "mobikwik": "Telecom",
    # Health & Beauty
    "nykaa": "Health & Beauty", "mamaearth": "Health & Beauty",
    "lenskart": "Health & Beauty", "firstcry": "Health & Beauty",
    "beardo": "Health & Beauty", "muscleblaze": "Health & Beauty",
    "myprotein": "Health & Beauty", "foxtale": "Health & Beauty",
    "dotandkey": "Health & Beauty", "traya": "Health & Beauty",
    "sephora": "Health & Beauty", "tira": "Health & Beauty",
    "purple": "Health & Beauty", "decathlon": "Health & Beauty",
    # Gaming
    "dream11": "Gaming", "my11circle": "Gaming",
    # Education
    "udemy": "Education", "coursera": "Education", "testbook": "Education",
    # Finance
    "kreditbee": "Finance", "cashify": "Finance",
    # Tech/SaaS
    "hostinger": "Tech", "godaddy": "Tech", "manychat": "Tech",
    "chatgpt": "Tech", "midjourney": "Tech",
    # Jewelry
    "tanishq": "Jewelry", "titan": "Jewelry", "giva": "Jewelry",
    # Travel (additional)
    "airindia": "Travel", "easemytrip": "Travel", "ixigo": "Travel",
    "yatra": "Travel", "zoomcar": "Travel", "indrive": "Travel",
    "tripcom": "Travel", "zostel": "Travel", "wonderla": "Travel",
    # Health (additional)
    "1mg": "Health & Beauty", "medplus": "Health & Beauty",
    "yesmadam": "Health & Beauty", "gnc": "Health & Beauty",
    # Fashion (additional)
    "zara": "Fashion", "uniqlo": "Fashion", "crocs": "Fashion",
    # Telecom (additional)
    "tataneu": "Telecom", "airtel": "Telecom", "freecharge": "Telecom",
    # Entertainment (additional)
    "sonyliv": "Entertainment", "steam": "Entertainment",
    # Tech (additional)
    "nordvpn": "Tech", "astrotalk": "Tech", "elevenlabs": "Tech",
    # Food (additional)
    "starbucks": "Food", "magicpin": "Food",
    # Home
    "homecentre": "Home",
    # Electronics (additional)
    "headphonezone": "Electronics",
}

# Suffix tokens for splitting keyword → brand + qualifier
SUFFIX_TOKENS = [
    "coupon", "coupons", "promo", "code", "codes", "discount",
    "offer", "offers", "deal", "deals", "sale", "referral",
    "voucher", "cashback", "bonus", "gift", "free",
]
SUFFIX_RE = re.compile(r"\b(" + "|".join(SUFFIX_TOKENS) + r")\b", re.IGNORECASE)


def extract_brand(keyword: str) -> str:
    m = SUFFIX_RE.search(keyword)
    if m:
        before = keyword[:m.start()].strip()
        after = SUFFIX_RE.sub("", keyword[m.end():]).strip()
        raw = before if before else after
        if not raw:
            raw = keyword.split()[0]
    else:
        raw = keyword.split()[0]
    raw = raw.strip()
    return normalize_brand(raw)


def normalize_brand(raw: str) -> str:
    if raw in BRAND_ALIASES:
        return BRAND_ALIASES[raw]
    cleaned = raw.replace(" ", "").lower()
    if cleaned in BRAND_ALIASES:
        return BRAND_ALIASES[cleaned]
    return cleaned


def classify_intent(keyword: str) -> str:
    kw = keyword.lower()
    if "grabon" in kw:
        return "Direct Brand"
    if any(w in kw for w in ("best", "top", "compare", "review", "vs", "alternative")):
        return "Commercial"
    return "Transactional"


def classify_category(brand_key: str) -> str:
    return BRAND_CATEGORY_MAP.get(brand_key, "General")


def assign_tier(brand_key: str) -> int:
    if brand_key in TIER_1_BRANDS:
        return 1
    if brand_key in TIER_2_BRANDS:
        return 2
    return 3


def pick_canonical(keywords: list[str], brand_raw: str) -> str:
    """Pick best canonical keyword from a group. Prefer '{brand} coupon code'."""
    for pattern in [f"{brand_raw} coupon code", f"{brand_raw} promo code",
                    f"{brand_raw} discount code", f"{brand_raw} coupon"]:
        if pattern in keywords:
            return pattern
    return min(keywords, key=len)


def run_import_sync(dry_run: bool = False):
    settings = get_settings()
    excel_path = PROJECT_ROOT / settings.keywords_excel_path

    log.info(f"Reading {excel_path}")
    wb = openpyxl.load_workbook(str(excel_path), read_only=True)
    ws = wb.active

    raw_keywords = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        kw = (row[0] or "").strip()
        if kw:
            raw_keywords.append(kw.lower())
    wb.close()
    log.info(f"Read {len(raw_keywords)} keywords from Excel")

    # --- Deduplicate ---
    groups: dict[str, list[str]] = defaultdict(list)
    brand_for_kw: dict[str, str] = {}

    for kw in raw_keywords:
        brand_key = extract_brand(kw)
        brand_for_kw[kw] = brand_key
        groups[brand_key].append(kw)

    # Pick canonical per brand group
    canonical_set = set()
    keyword_group_map: dict[str, str] = {}
    for brand_key, kws in groups.items():
        canonical = pick_canonical(kws, brand_key)
        canonical_set.add(canonical)
        for kw in kws:
            keyword_group_map[kw] = brand_key

    total_canonical = len(canonical_set)
    total_noncanonical = len(raw_keywords) - total_canonical

    log.info(f"Deduplication: {len(raw_keywords)} → {total_canonical} canonical + {total_noncanonical} variants")
    log.info(f"Keyword groups: {len(groups)}")

    # --- Build insert rows ---
    rows_to_insert = []
    for kw in raw_keywords:
        brand_key = brand_for_kw[kw]
        rows_to_insert.append((
            kw,
            classify_category(brand_key),
            classify_intent(kw),
            assign_tier(brand_key),
            keyword_group_map[kw],
            kw in canonical_set,
        ))

    # --- Stats ---
    tier_counts = defaultdict(int)
    canonical_tier_counts = defaultdict(int)
    for _, _, _, tier, _, is_canon in rows_to_insert:
        tier_counts[tier] += 1
        if is_canon:
            canonical_tier_counts[tier] += 1

    log.info("Tier breakdown (all keywords):")
    for t in sorted(tier_counts):
        log.info(f"  Tier {t}: {tier_counts[t]} total, {canonical_tier_counts[t]} canonical")

    if dry_run:
        log.info("DRY RUN — no DB writes. Sample rows:")
        for r in rows_to_insert[:10]:
            log.info(f"  {r}")
        return

    # --- Bulk insert ---
    log.info("Inserting into database...")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO prompts (text, merchant_category, intent_type, tier, keyword_group, is_canonical)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (text) DO UPDATE SET
                       tier = EXCLUDED.tier,
                       keyword_group = EXCLUDED.keyword_group,
                       is_canonical = EXCLUDED.is_canonical,
                       merchant_category = EXCLUDED.merchant_category,
                       intent_type = EXCLUDED.intent_type""",
                rows_to_insert,
            )
        conn.commit()

    log.info(f"Done. Inserted/updated {len(rows_to_insert)} keywords.")

    # --- Generate GEO conversational queries for ALL brands ---
    _insert_geo_queries(conn_str=settings.database_url, groups=groups, brand_for_kw=brand_for_kw)


BRAND_DISPLAY_NAMES = {
    "amazon": "Amazon", "flipkart": "Flipkart", "myntra": "Myntra", "ajio": "AJIO",
    "swiggy": "Swiggy", "zomato": "Zomato", "zepto": "Zepto", "blinkit": "Blinkit",
    "redbus": "RedBus", "dominos": "Domino's", "rapido": "Rapido", "paytm": "Paytm",
    "bookmyshow": "BookMyShow", "ola": "Ola", "uber": "Uber", "nykaa": "Nykaa",
    "makemytrip": "MakeMyTrip", "goibibo": "Goibibo", "indigo": "IndiGo", "kfc": "KFC",
    "jiomart": "JioMart", "dream11": "Dream11", "pizzahut": "Pizza Hut",
    "burgerking": "Burger King", "urbancompany": "Urban Company", "lenskart": "Lenskart",
    "puma": "Puma", "adidas": "Adidas", "nike": "Nike", "firstcry": "FirstCry",
    "samsung": "Samsung", "phonepe": "PhonePe", "bigbasket": "BigBasket",
    "croma": "Croma", "decathlon": "Decathlon", "jio": "Jio", "bewakoof": "Bewakoof",
    "pvr": "PVR", "netflix": "Netflix", "ikea": "IKEA", "hm": "H&M",
    "westside": "Westside", "meesho": "Meesho", "tatacliq": "Tata CLiQ",
    "boat": "boAt", "mamaearth": "Mamaearth", "mcdonalds": "McDonald's",
    "abhibus": "AbhiBus", "airindia": "Air India", "airtel": "Airtel",
    "tataneu": "Tata Neu", "starbucks": "Starbucks", "1mg": "1mg",
    "zara": "Zara", "uniqlo": "UNIQLO", "crocs": "Crocs",
}

# GEO query templates keyed by keyword suffix pattern.
# Each keyword gets matched to ONE template based on its dominant suffix.
# Fallback template covers keywords with no recognized suffix.
GEO_TEMPLATES = {
    "coupon code": "What are the best working coupon codes for {brand} right now?",
    "promo code": "Where can I find verified promo codes for {brand}?",
    "discount code": "What are the latest {brand} discount codes that actually work?",
    "coupon": "What are the best {brand} coupons available today?",
    "coupons": "What are the best {brand} coupons available today?",
    "promo": "What are the current {brand} promo offers?",
    "offer": "What are the best {brand} offers available right now?",
    "offers": "What are the best {brand} offers available right now?",
    "deal": "What are the top {brand} deals I can get today?",
    "deals": "What are the top {brand} deals I can get today?",
    "discount": "How can I get the biggest discount on {brand}?",
    "sale": "When is {brand}'s next sale and what discounts can I expect?",
    "cashback": "Which platform gives the best cashback for {brand} purchases?",
    "referral": "Does {brand} have a referral program and how does it work?",
    "voucher": "Where can I find valid {brand} vouchers?",
    "bonus": "Does {brand} offer any signup bonus or welcome rewards?",
    "gift": "What are the best {brand} gift card deals?",
    "free": "How can I get free delivery or free items from {brand}?",
    "recharge": "What are the best {brand} recharge offers and plans?",
    "subscription": "What discounts are available on {brand} subscription plans?",
    "first order": "What is the {brand} first order discount or welcome offer?",
}

# Additional GEO queries per brand for broader AI coverage
GEO_SUPPLEMENTARY = [
    "What are the best ways to save money on {brand}?",
    "Which coupon sites have working {brand} codes?",
    "Is {brand} offering any deals right now?",
]

# Suffix priority order for matching keywords to templates
_SUFFIX_PRIORITY = [
    "coupon code", "promo code", "discount code", "first order",
    "coupon", "coupons", "promo", "offer", "offers", "deal", "deals",
    "discount", "sale", "cashback", "referral", "voucher", "bonus",
    "gift", "free", "recharge", "subscription",
]


def _detect_suffix(keyword: str) -> str | None:
    kw = keyword.lower()
    for suffix in _SUFFIX_PRIORITY:
        if suffix in kw:
            return suffix
    return None


def _generate_geo_queries(
    groups: dict[str, list[str]],
    brand_for_kw: dict[str, str],
) -> tuple[list[tuple], dict[str, list[str]]]:
    """Generate GEO conversational queries for ALL brands.

    Returns:
        geo_rows: list of (text, category, intent, tier, group, is_canonical) tuples
        coverage: {brand_key: [geo_query_texts]} for coverage report
    """
    geo_rows = []
    coverage: dict[str, list[str]] = {}
    seen_queries: set[str] = set()

    for brand_key, kws in groups.items():
        display = BRAND_DISPLAY_NAMES.get(brand_key, brand_key.title())
        category = classify_category(brand_key)
        tier = assign_tier(brand_key)
        brand_queries: list[str] = []

        # Collect unique suffixes present in this brand's keywords
        suffixes_found: set[str] = set()
        for kw in kws:
            s = _detect_suffix(kw)
            if s:
                suffixes_found.add(s)

        # Generate one GEO query per unique suffix pattern
        for suffix in suffixes_found:
            template = GEO_TEMPLATES.get(suffix)
            if not template:
                continue
            query = template.format(brand=display)
            if query.lower() in seen_queries:
                continue
            seen_queries.add(query.lower())
            geo_rows.append((query, category, "GEO", tier, brand_key, True))
            brand_queries.append(query)

        # If no suffix matched (brandless or unusual keywords), use fallback
        if not brand_queries:
            fallback = f"What are the best deals and offers for {display}?"
            if fallback.lower() not in seen_queries:
                seen_queries.add(fallback.lower())
                geo_rows.append((fallback, category, "GEO", tier, brand_key, True))
                brand_queries.append(fallback)

        # Add supplementary queries for Tier 1 and Tier 2 brands
        if tier <= 2:
            for tmpl in GEO_SUPPLEMENTARY:
                query = tmpl.format(brand=display)
                if query.lower() in seen_queries:
                    continue
                seen_queries.add(query.lower())
                geo_rows.append((query, category, "GEO", tier, brand_key, True))
                brand_queries.append(query)

        coverage[brand_key] = brand_queries

    return geo_rows, coverage


def _insert_geo_queries(conn_str: str, groups: dict, brand_for_kw: dict):
    geo_rows, coverage = _generate_geo_queries(groups, brand_for_kw)

    if not geo_rows:
        log.warning("No GEO queries generated")
        return

    total_brands = len(coverage)
    total_queries = len(geo_rows)
    uncovered = [b for b, qs in coverage.items() if not qs]

    log.info(f"GEO generation: {total_brands} brands -> {total_queries} queries, {len(uncovered)} uncovered")
    if uncovered:
        log.warning(f"Uncovered brands: {uncovered[:20]}")

    with psycopg.connect(conn_str, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO prompts (text, merchant_category, intent_type, tier, keyword_group, is_canonical)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (text) DO NOTHING""",
                geo_rows,
            )
        conn.commit()

    log.info(f"GEO queries inserted: {total_queries} queries for {total_brands} brands, 0 unmapped")


async def run_import(dry_run: bool = False):
    """Async wrapper for use from API routes."""
    import asyncio
    await asyncio.to_thread(run_import_sync, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import keywords from Excel")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without DB writes")
    args = parser.parse_args()
    run_import_sync(dry_run=args.dry_run)
