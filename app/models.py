from pydantic import BaseModel
from datetime import datetime
from enum import Enum


class Sentiment(str, Enum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"


class CouponStatus(str, Enum):
    ACTIVE = "Active-Valid"
    EXPIRED = "Expired-On-Site"
    HALLUCINATED = "Hallucinated"


class PromptItem(BaseModel):
    id: str
    text: str
    merchant_category: str
    intent_type: str
    tier: int = 2
    keyword_group: str | None = None
    last_run_at: datetime | None = None
    is_canonical: bool = True


class BrandMention(BaseModel):
    rank_position: int
    brand_name: str
    sentiment: Sentiment
    context_snippet: str
    cited_url: str | None = None


class HallucinatedCoupon(BaseModel):
    coupon_code: str = ""
    associated_merchant: str = ""
    status_flag: CouponStatus = CouponStatus.HALLUCINATED


class ExtractedData(BaseModel):
    brand_mentions: list[BrandMention]
    ai_hallucinated_coupons: list[HallucinatedCoupon]


class ScrapeResult(BaseModel):
    raw_response_text: str
    raw_html_payload: str
    cited_urls: list[str] = []
    error_log: str | None = None


class PipelineState(BaseModel):
    target_prompt: PromptItem
    engine_name: str
    country_code: str = "IN"
    raw_response_text: str = ""
    raw_html_payload: str = ""
    extracted_data: ExtractedData | None = None
    retry_count: int = 0
    error_log: str | None = None
