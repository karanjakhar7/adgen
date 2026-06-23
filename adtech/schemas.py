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
    """Publisher audience. All fields optional with neutral defaults so a
    user-supplied publisher needn't describe its audience to be usable."""

    age_skew: str = "all"
    gender_split: dict[str, float] = Field(default_factory=lambda: {"female": 0.5, "male": 0.5})
    top_geos: list[str] = Field(default_factory=list)
    income_tier: str = "mid"


class Publisher(BaseModel):
    """A user-supplied publisher. Only `name` + `category` are required;
    everything else defaults so the entry barrier stays low. `id` is assigned
    server-side during normalization (see retrieval.normalize_publishers)."""

    id: str = ""
    name: str
    category: str
    subcategories: list[str] = Field(default_factory=list)
    monthly_impressions: int | None = None
    avg_order_value_usd: int | None = None
    audience: Audience = Field(default_factory=Audience)
    notes: str = ""


class Persona(BaseModel):
    """A user-supplied shopper persona. Only `name` + `description` are
    required. `id` is assigned server-side during normalization."""

    id: str = ""
    name: str
    description: str
    age_range: str = "any"
    gender_skew: str = "any"
    category_affinities: list[str] = Field(default_factory=list)
    price_sensitivity: str = "medium"
    messaging_preferences: list[str] = Field(default_factory=list)
    disinterested_in: list[str] = Field(default_factory=list)
    typical_aov_usd: int | None = None


# ---------------------------------------------------------------------------
# Stage 1 — interpret
# ---------------------------------------------------------------------------


class AdvertiserProfile(BaseModel):
    category: str = Field(description="Short product/service category slug", examples=["pet_food", "apparel", "wellness", "home_decor"])
    subcategory: str | None = Field(None, description="More specific slug if clearly apparent, else null", examples=["senior_dog_food", "activewear", "supplements"])
    value_props: list[str] = Field(default_factory=list, description="Key selling points stated or strongly implied by the brief")
    emotional_benefit: str = Field("", description="The core human desire/payoff under the functional value props — what the customer actually feels or gains (e.g. 'more pain-free years with a dog who can still climb the stairs')")
    proof_points: list[str] = Field(default_factory=list, description="Substantiable credibility claims the brief actually supports (e.g. 'vet-formulated', 'recycled ocean plastic', 'clinically studied') — the positive inventory copy may cite; do NOT invent")
    brand_voice: str = Field("", description="The brand's tonal register inferred from positioning (e.g. 'clinical & reassuring', 'playful & irreverent', 'understated premium') — the on-brand voice creative must hold across every persona")
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
    message_angle: str = Field(description="The ONE single-minded idea this persona's ad should lead with — must be distinct from the other picks' angles so the variants don't collapse into one voice (e.g. 'clinical proof of joint mobility' vs 'more good years together' vs 'vet-trusted, zero guesswork')")
    publisher_id: str | None = Field(None, description="The recommended publisher whose audience this persona most maps to — used to ground the creative in its placement environment; null if no clear single fit")


class PersonaSelection(BaseModel):
    personas: list[PersonaChoice] = Field(min_length=1, max_length=5, description="3–5 personas ordered by fit; fewer is acceptable when publisher inventory is narrow")


# ---------------------------------------------------------------------------
# Stage 5 — creative (one variant per fan-out call)
# ---------------------------------------------------------------------------


class CreativeVariant(BaseModel):
    persona_id: str = Field(description="The persona ID this creative is written for")
    headline: str = Field(description="Ad headline, max ~12 words, hooks the persona's core motivation on the single assigned message angle")
    body: str = Field(description="1–2 sentence body copy built on messaging_preferences and one concrete proof point, avoiding disinterested_in topics")
    cta: str = Field(description="A short, specific call to action matched to the advertiser's purchase_objective (e.g. 'Start your free trial', 'Subscribe & save', 'Shop senior formulas') — low-friction, never generic 'Shop now'")
    rationale: str = Field(description="1–2 sentences explaining copy choices relative to this persona's specific preferences, the assigned angle, and the placement")


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
    message_angle: str
    headline: str
    body: str
    cta: str
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
