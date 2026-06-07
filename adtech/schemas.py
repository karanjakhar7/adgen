"""Pydantic v2 models — the typed contracts between every pipeline stage.

Stages never pass raw dicts. Closed vocabularies are StrEnums; catalog
vocabularies (categories, subcategories, geos) stay free strings because the
source data uses inconsistent slugs.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums (closed sets)
# ---------------------------------------------------------------------------


class Signal(StrEnum):
    CLEAR = "clear"
    LOW_SIGNAL = "low_signal"
    OFF_TOPIC = "off_topic"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PriceTier(StrEnum):
    BUDGET = "budget"
    MID = "mid"
    PREMIUM = "premium"
    LUXURY = "luxury"


class Gender(StrEnum):
    FEMALE = "female"
    MALE = "male"
    BALANCED = "balanced"
    ANY = "any"


class TargetingMode(StrEnum):
    RESTRICT = "restrict"
    OBSERVE = "observe"


# ---------------------------------------------------------------------------
# Catalog records (loaded from data/, validated once at startup)
# ---------------------------------------------------------------------------


class Audience(BaseModel):
    age_skew: str
    gender_split: dict[str, float]
    top_geos: list[str]
    income_tier: str


class Publisher(BaseModel):
    id: str
    name: str
    category: str
    subcategories: list[str]
    monthly_impressions: int
    avg_order_value_usd: int
    audience: Audience
    notes: str


class Persona(BaseModel):
    id: str
    name: str
    age_range: str
    gender_skew: str
    description: str
    category_affinities: list[str]
    price_sensitivity: str
    messaging_preferences: list[str]
    disinterested_in: list[str]
    typical_aov_usd: int


# ---------------------------------------------------------------------------
# Stage 1 — interpret
# ---------------------------------------------------------------------------


class AdvertiserProfile(BaseModel):
    category: str
    subcategory: str | None = None
    value_props: list[str] = []
    target_customer: str = ""
    price_tier: PriceTier = PriceTier.MID
    purchase_objective: str = "purchase"
    negative_constraints: list[str] = []
    implied_audience_gender: Gender = Gender.ANY
    sensitive_category_flags: list[str] = []
    assumptions: list[str] = []
    confidence: Confidence
    signal: Signal


# ---------------------------------------------------------------------------
# Stage 2 — precomputed mechanical signals (code, not LLM)
# ---------------------------------------------------------------------------


class PublisherSignals(BaseModel):
    publisher_id: str
    aov_ratio: float
    aov_fit: str  # "below" | "in_band" | "above"
    category_overlap: float
    gender_alignment: float
    income_note: str


# ---------------------------------------------------------------------------
# Stage 3 — rank
# ---------------------------------------------------------------------------


class PublisherScore(BaseModel):
    publisher_id: str
    score: float = Field(ge=0.0, le=1.0)
    fit_reasons: list[str]


class RankOutput(BaseModel):
    scores: list[PublisherScore]


# ---------------------------------------------------------------------------
# Stage 4 — personas
# ---------------------------------------------------------------------------


class PersonaChoice(BaseModel):
    persona_id: str
    why_this_persona: str


class PersonaSelection(BaseModel):
    personas: list[PersonaChoice] = Field(min_length=1, max_length=5)


# ---------------------------------------------------------------------------
# Stage 5 — creative (one variant per fan-out call)
# ---------------------------------------------------------------------------


class CreativeVariant(BaseModel):
    persona_id: str
    headline: str
    body: str
    rationale: str


# ---------------------------------------------------------------------------
# Final assembly — the campaign draft (ARCHITECTURE.md §6)
# ---------------------------------------------------------------------------


class PublisherRecommendation(BaseModel):
    publisher_id: str
    name: str
    score: float
    fit_reasons: list[str]
    budget_pct: int


class ExcludedPublisher(BaseModel):
    publisher_id: str
    name: str
    reason: str


class PersonaCreative(BaseModel):
    persona_id: str
    name: str
    why_this_persona: str
    headline: str
    body: str
    rationale: str
    critique_flags: list[str] = []


class PublisherAllocation(BaseModel):
    publisher_id: str
    budget_pct: int
    budget_usd: int | None = None


class Budget(BaseModel):
    mode: str  # "percentage_only_until_budget_provided" | "total_provided"
    total_usd: int | None = None
    suggested_daily_range_usd: tuple[int, int] | None = None


class BidStrategy(BaseModel):
    type: str = "manual_cpc_start"
    upgrade_path: str = "target_cpa_when_conversion_data_exists"


class CampaignConfig(BaseModel):
    objective: str
    targeting_mode: TargetingMode
    audience_attributes: list[str]
    demographic_filters: dict[str, list[str]] = {}
    geo: list[str] = ["US"]
    devices: list[str] = ["mobile", "desktop"]
    publisher_allocations: list[PublisherAllocation]
    budget: Budget
    bid_strategy: BidStrategy = BidStrategy()
    frequency_cap_per_user_per_day: int = 3
    creative_rotation: str = "even_until_data_exists"
    exclusions: list[str] = []
    assumptions: list[str] = []
    confidence: Confidence


class CampaignDraft(BaseModel):
    trace_id: str
    advertiser_profile: AdvertiserProfile
    publisher_recommendations: list[PublisherRecommendation]
    excluded_publishers: list[ExcludedPublisher]
    persona_creatives: list[PersonaCreative]
    campaign_config: CampaignConfig


class OffTopicResult(BaseModel):
    """Returned when the Stage-1 gate stops the pipeline."""

    trace_id: str
    signal: Signal = Signal.OFF_TOPIC
    message: str
    advertiser_profile: AdvertiserProfile
