"""Stage 2 — deterministic mechanical fit signals (pure code, no LLM).

The model never does arithmetic. These signals become *inputs* to the ranking
prompt; the LLM weighs them qualitatively alongside publisher `notes`.
We deliberately do NOT combine them into a composite score — hand-picked
weights with zero outcome data would be opinion dressed as arithmetic.
"""

import re

from adtech.schemas import AdvertiserProfile, Gender, PriceTier, Publisher, PublisherSignals

# Expected AOV band per price tier, derived from the catalog's AOV spread
# ($28–$198). A luxury brief vs. this catalog lands "below" everywhere — that
# is the price-ceiling signal, surfaced mechanically.
PRICE_TIER_AOV_BANDS: dict[PriceTier, tuple[float, float]] = {
    PriceTier.BUDGET: (0, 50),
    PriceTier.MID: (40, 100),
    PriceTier.PREMIUM: (90, 200),
    PriceTier.LUXURY: (180, 10_000),
}


def _tokenize(*texts: str) -> set[str]:
    tokens: set[str] = set()
    for text in texts:
        tokens.update(re.split(r"[\s_\-/,]+", text.lower()))
    return tokens - {""}


def _category_overlap(profile: AdvertiserProfile, publisher: Publisher) -> float:
    brief_tokens = _tokenize(profile.category, profile.subcategory or "", *profile.value_props)
    pub_tokens = _tokenize(publisher.category, *publisher.subcategories)
    if not brief_tokens or not pub_tokens:
        return 0.0
    return round(len(brief_tokens & pub_tokens) / len(brief_tokens | pub_tokens), 3)


def _dominant_gender(publisher: Publisher) -> Gender:
    split = publisher.audience.gender_split
    top, share = max(split.items(), key=lambda kv: kv[1])
    if share < 0.55 or top == "other":
        return Gender.BALANCED
    return Gender(top)


def _gender_alignment(profile: AdvertiserProfile, publisher: Publisher) -> float:
    brief = profile.implied_audience_gender
    if brief in (Gender.ANY, Gender.BALANCED):
        return 1.0
    pub = _dominant_gender(publisher)
    if pub == brief:
        return 1.0
    if pub == Gender.BALANCED:
        return 0.5
    return 0.0


def _aov_signal(profile: AdvertiserProfile, publisher: Publisher) -> tuple[float, str]:
    lo, hi = PRICE_TIER_AOV_BANDS[profile.price_tier]
    midpoint = (lo + hi) / 2
    ratio = round(publisher.avg_order_value_usd / midpoint, 2)
    if publisher.avg_order_value_usd < lo:
        fit = "below"
    elif publisher.avg_order_value_usd > hi:
        fit = "above"
    else:
        fit = "in_band"
    return ratio, fit


def _income_note(profile: AdvertiserProfile, publisher: Publisher) -> str:
    tier = publisher.audience.income_tier
    if profile.price_tier in (PriceTier.PREMIUM, PriceTier.LUXURY) and tier == "mid":
        return f"{profile.price_tier}-priced brief vs {tier}-income audience — possible price ceiling"
    if profile.price_tier == PriceTier.BUDGET and tier == "high":
        return "budget-priced brief vs high-income audience — value pitch may underperform"
    return f"{tier}-income audience, no obvious price conflict"


def compute_signals(profile: AdvertiserProfile, publisher: Publisher) -> PublisherSignals:
    aov_ratio, aov_fit = _aov_signal(profile, publisher)
    return PublisherSignals(
        publisher_id=publisher.id,
        aov_ratio=aov_ratio,
        aov_fit=aov_fit,
        category_overlap=_category_overlap(profile, publisher),
        gender_alignment=_gender_alignment(profile, publisher),
        income_note=_income_note(profile, publisher),
    )
