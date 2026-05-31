"""Open Food Facts brand discovery.

Searches the open-source food product database for Indian D2C
food/FMCG brands. Brands with real product listings have actual
SKUs in market — confirms legitimate D2C operations.

Free — no API key, no signup. Open data.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"

_NOISE_BRANDS = re.compile(
    r"^(unknown|generic|n/a|na|none|unbranded|various|"
    r"store brand|private label|no brand|test)$",
    re.I,
)

_BIG_FMCG = {
    "nestle", "unilever", "hindustan unilever", "p&g", "procter & gamble",
    "itc", "britannia", "parle", "amul", "dabur", "godrej", "marico",
    "colgate", "coca-cola", "pepsico", "mondelez", "mars", "kelloggs",
    "haldiram", "mtr",
}


class OpenFoodFactsCollector(Collector):
    """Discover food/FMCG brands from Open Food Facts.

    Parameters:
        search_terms: product category search (e.g. "organic snacks"). REQUIRED.
        country: country tag filter (default "india").
        max_results: max products to process (default 50).
    """

    name = "open_food_facts"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search = (self.params.get("search_terms") or "").strip()
        if not search:
            log.warning("open_food_facts.no_search_terms")
            return

        country = self.params.get("country", "india")
        max_results = self.params.get("max_results", 50)

        try:
            products = await _search_products(search, country, max_results)
        except Exception as exc:
            log.warning("open_food_facts.fetch_failed", error=str(exc))
            return

        if not products:
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        seen_brands: set[str] = set()

        for product in products:
            brand_raw = (product.get("brands") or "").strip()
            if not brand_raw:
                continue

            for brand_part in brand_raw.split(","):
                brand_name = brand_part.strip().title()
                brand_lower = brand_name.lower()

                if not brand_name or len(brand_name) < 2:
                    continue
                if _NOISE_BRANDS.match(brand_name):
                    continue
                if brand_lower in _BIG_FMCG:
                    continue
                if brand_lower in seen_brands:
                    continue

                seen_brands.add(brand_lower)
                categories = product.get("categories", "")
                product_name = product.get("product_name", "")

                yield SignalEvent(
                    type="product.food_brand",
                    source=self.name,
                    observed_at=now,
                    brand_name=brand_name,
                    value_text=f"Food brand: {product_name[:60]}",
                    payload={
                        "brand": brand_name,
                        "product_name": product_name[:120],
                        "categories": categories[:200],
                        "countries": product.get("countries", ""),
                        "stores": product.get("stores", ""),
                        "barcode": product.get("code", ""),
                        "nutriscore": product.get("nutriscore_grade", ""),
                        "search_term": search,
                    },
                    dedupe_key=f"off:{brand_lower}:{day}",
                )


async def _search_products(
    query: str, country: str, max_results: int
) -> list[dict[str, Any]]:
    params = {
        "search_terms": query,
        "search_simple": "1",
        "action": "process",
        "json": "1",
        "page_size": str(min(max_results, 100)),
        "tagtype_0": "countries",
        "tag_contains_0": "contains",
        "tag_0": country,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            _SEARCH_URL,
            params=params,
            headers={"User-Agent": "GrabOnIntel/1.0 (brand-discovery)"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("products", [])
