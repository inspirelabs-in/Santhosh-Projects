"""Signal collector ABC.

A collector is an async iterator producing SignalEvent objects. The
runner (`run_collector`) handles brand resolution, dedup, idempotent
insert, and run-state bookkeeping. Sources only worry about pulling data.
"""
from __future__ import annotations

import abc
import datetime as dt
import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session as session_ctx
from ..db.models import CollectorRun, Signal
from ..logging import get_logger
from ..resolver import resolve_or_create

log = get_logger(__name__)

# ICP pre-filter: reject brand names that are clearly NOT consumer/D2C brands
_ICP_REJECT_NAME = re.compile(
    r"^("
    r"people[\s,]+jobs|case\s+study|newsletter|inside\s+(the|india|indian)|"
    r"layoffs?|funding\s+wrap|breaking\s*views|how\s+to|why\s+|what\s+is|"
    r"top\s+\d+|best\s+\d+|isbr\s+|bits\s+design|"
    r"linkedin\s+|cracking\s+|stitching\s+|fortune\s+|"
    r"e-?commerce\s+business|new\s+report|daily\s+digest|"
    r"india\s+today|economic\s+times|business\s+standard|"
    r"mint\s+|livemint|moneycontrol|reuters|bloomberg|"
    r"techcrunch|yourstory\s+|inc42\s+|entrackr|"
    r"update|india|youtube|key\s+differences"
    r")$",
    re.I,
)

_ICP_REJECT_SUFFIXES = re.compile(
    r"\s+(case\s+study|review|tutorial|guide|news|report|analysis|wrap|digest)$",
    re.I,
)

_ICP_REJECT_DOMAINS = {
    "ndtv.com", "indianexpress.com", "aajtak.in", "news18.com",
    "thehindu.com", "hindustantimes.com", "zeenews.india.com",
    "livemint.com", "moneycontrol.com", "economictimes.com",
    "businessinsider.com", "forbes.com", "reuters.com", "bloomberg.com",
    "techcrunch.com", "yourstory.com", "inc42.com", "entrackr.com",
    "britannica.com", "wikipedia.org", "quora.com", "reddit.com",
    "medium.com", "wordpress.com", "blogspot.com",
    "salesforce.com", "hubspot.com", "zoho.com", "freshworks.com",
    "youtube.com", "instagram.com", "facebook.com", "twitter.com",
    "linkedin.com", "x.com", "tiktok.com", "pinterest.com",
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com",
    "nykaa.com", "ajio.com", "snapdeal.com", "meesho.com",
    "jiomart.com", "indiamart.com", "justdial.com",
    "grabon.in", "coupondunia.in", "cashkaro.com", "magicpin.in",
    "mycouponsnexus.com", "couponzguru.com",
    "d2cstory.com", "discoveringbrands.com", "d2csale.com",
    "towardsbusiness.com", "businessoutreach.in",
    # Platform subdomains — real brands use custom domains
    "myshopify.com", "shopify.com", "shopify.in", "shopifypreview.com",
    "bigcartel.com",
    "wixsite.com", "wix.com", "squarespace.com",
    "webflow.io", "carrd.co", "godaddysites.com",
    "netlify.app", "vercel.app", "herokuapp.com",
    "blogspot.in", "tumblr.com", "substack.com",
}

# Reject platform subdomains (*.myshopify.com, *.wixsite.com, etc.)
_PLATFORM_SUBDOMAIN_HOSTS = {
    "myshopify.com", "shopify.com", "shopify.in", "shopifypreview.com",
    "bigcartel.com",
    "wixsite.com", "squarespace.com", "webflow.io",
    "godaddysites.com", "carrd.co",
    "netlify.app", "vercel.app", "herokuapp.com",
}

# Reject domains that look auto-generated (hex slugs, numeric prefixes, UUIDs)
_JUNK_DOMAIN_RE = re.compile(
    r"^("
    r"\d{2,}-\d{2,}[a-z]*-"       # "03-0274aa-mol" pattern
    r"|[0-9a-f]{8,}-"              # UUID-prefix pattern
    r"|store-?\d{4,}"              # store-12345
    r"|shop-?\d{4,}"              # shop-12345
    r"|test-?site"                 # test sites
    r"|demo-?store"                # demo stores
    r"|my-?store-?\d+"             # generic store names
    r")",
    re.I,
)

_ICP_REJECT_DOMAIN_PATTERNS = re.compile(
    r"\.(gov\.\w+|gov|mil|edu|ac\.in|nic\.in|bank\.in|org\.in)$"
)


def _passes_icp_filter(name: str | None, domain: str | None) -> bool:
    """Quick deterministic check — reject obvious non-brand names/domains."""
    if domain:
        d = domain.lower().removeprefix("www.")
        if d in _ICP_REJECT_DOMAINS:
            return False
        if _ICP_REJECT_DOMAIN_PATTERNS.search(d):
            return False
        # Reject platform subdomains (*.myshopify.com etc.)
        # Real brands use custom domains, not platform defaults.
        for host in _PLATFORM_SUBDOMAIN_HOSTS:
            if d.endswith("." + host):
                return False
        # Reject auto-generated/junk domain prefixes
        if _JUNK_DOMAIN_RE.match(d):
            return False
    if not name and not domain:
        return False
    if name:
        n = name.strip()
        if len(n) < 2 or len(n) > 60:
            return False
        if _ICP_REJECT_NAME.match(n):
            return False
        if _ICP_REJECT_SUFFIXES.search(n):
            return False
        if len(n.split()) > 5:
            return False
    return bool(domain or name)


@dataclass(slots=True)
class SignalEvent:
    """One observation about a brand."""

    type: str
    source: str
    observed_at: dt.datetime
    brand_name: str | None = None
    brand_domain: str | None = None
    value_num: float | None = None
    value_text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None  # if None, auto-derived from (source,type,domain/name,observed_at,value)

    def derive_dedupe_key(self) -> str:
        if self.dedupe_key:
            return self.dedupe_key
        anchor = self.brand_domain or self.brand_name or ""
        raw = f"{self.source}|{self.type}|{anchor}|{self.observed_at.isoformat()}|{self.value_num}|{self.value_text or ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:64]


class Collector(abc.ABC):
    """Subclass and implement `produce`."""

    name: str = "abstract"

    def __init__(self, **params: Any) -> None:
        self.params = params

    @abc.abstractmethod
    async def produce(self) -> AsyncIterator[SignalEvent]:
        """Yield SignalEvent objects. Must be an async generator."""
        raise NotImplementedError
        yield  # pragma: no cover  # makes type checker treat as async gen


async def _insert_signal(session: AsyncSession, ev: SignalEvent, brand_id: int | None) -> bool:
    """Insert one signal idempotently. Returns True if a new row was created."""
    stmt = pg_insert(Signal).values(
        brand_id=brand_id,
        brand_hint=ev.brand_name or ev.brand_domain,
        type=ev.type,
        value_num=ev.value_num,
        value_text=ev.value_text,
        payload=ev.payload or None,
        source=ev.source,
        dedupe_key=ev.derive_dedupe_key(),
        observed_at=ev.observed_at,
    ).on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(Signal.id)
    row = (await session.execute(stmt)).first()
    return row is not None


async def run_collector(collector: Collector) -> dict[str, int]:
    """Run a collector to completion. Returns counts."""
    emitted = 0
    deduped = 0
    run_id: int | None = None
    error: str | None = None
    try:
        async with session_ctx() as s:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO collector_runs (collector, params, status) "
                        "VALUES (:c, CAST(:p AS JSONB), 'running') RETURNING id"
                    ),
                    {"c": collector.name, "p": _json_dump(collector.params)},
                )
            ).first()
            run_id = int(row[0]) if row else None

        rejected = 0
        async for ev in collector.produce():
            if not _passes_icp_filter(ev.brand_name, ev.brand_domain):
                rejected += 1
                continue
            async with session_ctx() as s:
                bid = await resolve_or_create(s, name=ev.brand_name, domain=ev.brand_domain)
                inserted = await _insert_signal(s, ev, bid)
                if inserted:
                    emitted += 1
                else:
                    deduped += 1
        if rejected:
            log.info("collector.icp_rejected", collector=collector.name, rejected=rejected)
        log.info("collector.done", collector=collector.name, emitted=emitted, deduped=deduped)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        log.exception("collector.failed", collector=collector.name, error=error)
        from ..logging import classify_error, record_error
        cat = classify_error(exc)
        record_error(
            category=cat,
            source=f"collector.{collector.name}",
            message=error[:300],
            detail=str(collector.params),
        )
        raise
    finally:
        if run_id is not None:
            async with session_ctx() as s:
                await s.execute(
                    text(
                        "UPDATE collector_runs SET finished_at = NOW(), "
                        "signals_emitted = :e, signals_deduped = :d, "
                        "status = :st, error = :err WHERE id = :id"
                    ),
                    {
                        "e": emitted,
                        "d": deduped,
                        "st": "error" if error else "ok",
                        "err": error,
                        "id": run_id,
                    },
                )
    return {"emitted": emitted, "deduped": deduped}


def _json_dump(obj: Any) -> str:
    import orjson

    return orjson.dumps(obj, default=str).decode("utf-8")
