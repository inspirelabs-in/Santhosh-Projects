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
    AI_MENTIONED = "AI-Mentioned"


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


# --- SERP Models ---

class OrganicResult(BaseModel):
    rank_position: int
    title: str
    snippet: str | None = None
    url: str
    domain: str
    has_table: bool = False
    is_target: bool = False


class AdResult(BaseModel):
    rank_position: int
    title: str
    snippet: str | None = None
    url: str
    domain: str


class AnswerBoxEntry(BaseModel):
    url: str
    domain: str
    title: str | None = None
    position: str = "main"


class SerpFeature(BaseModel):
    feature_type: str
    rank_position: int | None = None
    title: str | None = None
    snippet: str | None = None
    url: str | None = None
    domain: str | None = None
    question_text: str | None = None


class SerpData(BaseModel):
    keyword: str
    organic_results: list[OrganicResult] = []
    ads: list[AdResult] = []
    answer_box: list[AnswerBoxEntry] = []
    people_also_ask: list[str] = []
    related_keywords: list[str] = []
    results_count: str = ""
    raw_html_hash: str = ""


# --- Citation/Content Models ---

class PageContent(BaseModel):
    url: str
    domain: str = ""
    title: str = ""
    meta_description: str = ""
    h1_tags: list[str] = []
    word_count: int = 0
    main_text_preview: str = ""
    schema_types: list[str] = []
    canonical_url: str = ""
    fetch_status: int = 0


class ComparisonResult(BaseModel):
    keyword: str
    engine_name: str
    competitor_url: str
    target_url: str = ""
    content_gaps: list[str] = []
    structural_advantages: list[str] = []
    authority_signals: list[str] = []
    recommendations: list[str] = []
    confidence: float = 0.0


class CitationOverlap(BaseModel):
    cited_url: str
    engine_name: str
    serp_rank: int | None = None
    brand_mention_id: str | None = None
    serp_entry_id: str | None = None


# --- Diagnosis Models ---

class RootCause(BaseModel):
    category: str  # content_gap, authority_gap, freshness_gap, structural_gap, relevance_gap
    description: str
    evidence: str


class ActionItem(BaseModel):
    action: str
    expected_impact: str = "medium"  # high, medium, low
    effort: str = "medium"  # high, medium, low
    target_url: str = ""


class Diagnosis(BaseModel):
    id: str = ""
    prompt_id: str
    engine_name: str
    root_causes: list[RootCause] = []
    action_items: list[ActionItem] = []
    priority: str = "medium"  # critical, high, medium, low
    confidence: float = 0.0
    summary: str = ""


# --- Verification Models ---

class MetricChange(BaseModel):
    metric: str
    before: float | int | bool | None = None
    after: float | int | bool | None = None
    delta: float | int | None = None
    direction: str = "unchanged"  # improved, degraded, unchanged
    note: str = ""


class AppliedFix(BaseModel):
    id: str = ""
    diagnosis_id: str | None = None
    prompt_id: str
    engine_name: str = "all"
    description: str = ""
    target_url: str = ""
    applied_at: datetime | None = None
    verification_status: str = "monitoring"


class VerificationResult(BaseModel):
    fix_id: str
    keyword: str = ""
    metric_changes: list[MetricChange] = []
    verdict: str = "insufficient_data"
    sufficient_data: bool = False
    post_fix_datapoints: int = 0
    days_since_fix: int = 0


class PipelineState(BaseModel):
    target_prompt: PromptItem
    engine_name: str
    country_code: str = "IN"
    raw_response_text: str = ""
    raw_html_payload: str = ""
    extracted_data: ExtractedData | None = None
    serp_data: SerpData | None = None
    retry_count: int = 0
    error_log: str | None = None
