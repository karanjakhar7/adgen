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
    category: str = Field(description="Short product/service category slug", examples=["pet_food", "apparel", "wellness", "home_decor"])
    subcategory: str | None = Field(None, description="More specific slug if clearly apparent, else null", examples=["senior_dog_food", "activewear", "supplements"])
    value_props: list[str] = Field(default_factory=list, description="Key selling points stated or strongly implied by the brief")
    target_customer: str = Field("", description="One phrase describing the ideal customer")
    price_tier: PriceTier = Field(PriceTier.MID, description="Price positioning inferred from wording or price points")
    purchase_objective: str = Field("purchase", description="What a campaign should drive", examples=["purchase", "subscription_signup", "lead_gen", "trial", "app_install"])
    negative_constraints: list[str] = Field(default_factory=list, description="Things the brand explicitly is NOT or avoids")
    implied_audience_gender: Gender = Field(Gender.ANY, description="Primary gender skew inferred from product or wording")
    sensitive_category_flags: list[str] = Field(default_factory=list, description="Regulated categories that apply", examples=[["health_claim"], ["finance"], ["children"], ["employment", "housing"]])
    assumptions: list[str] = Field(default_factory=list, description="Every value inferred rather than read from the brief — surface all guesses here")
    confidence: Confidence = Field(description="How much was guessed: high = stated outright, medium = reasonable inference, low = mostly assumptions")
    signal: Signal = Field(description="Input quality gate: clear = actionable brief, low_signal = vague but real business, off_topic = no advertisable business at all")


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
    publisher_id: str = Field(description="Publisher ID from the candidate list")
    score: float = Field(ge=0.0, le=1.0, description="Fit score 0.0–1.0; clustered-low is correct when fit is genuinely poor everywhere")
    fit_reasons: list[str] = Field(description="2–4 concise reasons citing specific signals, notes, or demographics — no generic praise")


class RankOutput(BaseModel):
    scores: list[PublisherScore] = Field(description="One entry per candidate publisher — every publisher must be scored exactly once")


# ---------------------------------------------------------------------------
# Stage 4 — personas
# ---------------------------------------------------------------------------


class PersonaChoice(BaseModel):
    persona_id: str = Field(description="Persona ID from the provided catalog")
    why_this_persona: str = Field(description="1–2 sentences citing a specific affinity, price sensitivity, or publisher audience match")


class PersonaSelection(BaseModel):
    personas: list[PersonaChoice] = Field(min_length=1, max_length=5, description="3–5 personas ordered by fit; fewer is acceptable when publisher inventory is narrow")


# ---------------------------------------------------------------------------
# Stage 5 — creative (one variant per fan-out call)
# ---------------------------------------------------------------------------


class CreativeVariant(BaseModel):
    persona_id: str = Field(description="The persona ID this creative is written for")
    headline: str = Field(description="Ad headline, max ~12 words, hooks the persona's core motivation")
    body: str = Field(description="1–2 sentence body copy built on messaging_preferences, avoiding disinterested_in topics")
    rationale: str = Field(description="1–2 sentences explaining copy choices relative to this persona's specific preferences")


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
