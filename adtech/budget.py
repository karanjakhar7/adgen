"""Stage 6 — campaign config assembly (pure code, no LLM).

Budget allocation = fit-proportional shares, capped by an inventory ceiling
derived from monthly_impressions, with an exploration floor so lower-ranked
publishers still get room to learn. Integer percentages always sum to exactly
100 via largest-remainder rounding.
"""

from adtech.config import (
    EXPLORATION_FLOOR,
    MAX_SINGLE_PUBLISHER_SHARE,
    SUGGESTED_DAILY_RANGE_USD,
)
from adtech.schemas import (
    AdvertiserProfile,
    Budget,
    CampaignConfig,
    Confidence,
    Publisher,
    PublisherAllocation,
    PublisherRecommendation,
    Signal,
    TargetingMode,
)


def _largest_remainder_round(shares: list[float]) -> list[int]:
    """Round fractional shares (summing to ~1.0) to integer pcts summing to 100."""
    scaled = [s * 100 for s in shares]
    floors = [int(s) for s in scaled]
    remainder = 100 - sum(floors)
    # Hand the leftover points to the largest fractional remainders.
    order = sorted(range(len(scaled)), key=lambda i: scaled[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def allocate(scored: list[tuple[str, float, int]]) -> dict[str, int]:
    """Allocate budget percentages across recommended publishers.

    `scored` is [(publisher_id, fit_score, monthly_impressions), ...].
    Returns {publisher_id: budget_pct} summing to exactly 100 (or {} for zero
    publishers — the honest zero-recommendation case).
    """
    if not scored:
        return {}
    if len(scored) == 1:
        return {scored[0][0]: 100}

    total_score = sum(s for _, s, _ in scored) or 1.0
    total_impressions = sum(i for _, _, i in scored) or 1

    raw: list[float] = []
    for _, score, impressions in scored:
        fit_share = score / total_score
        inventory_ceiling = min(impressions / total_impressions, MAX_SINGLE_PUBLISHER_SHARE)
        share = min(fit_share, inventory_ceiling)
        raw.append(max(share, EXPLORATION_FLOOR))

    normalized = [r / sum(raw) for r in raw]

    # Renormalizing can push a share back above the cap; clamp and hand the
    # excess to the uncapped publishers (a couple of passes converge for ≤6).
    for _ in range(3):
        excess = sum(s - MAX_SINGLE_PUBLISHER_SHARE for s in normalized if s > MAX_SINGLE_PUBLISHER_SHARE)
        if excess <= 0:
            break
        uncapped_total = sum(s for s in normalized if s <= MAX_SINGLE_PUBLISHER_SHARE)
        normalized = [
            min(s, MAX_SINGLE_PUBLISHER_SHARE) + (excess * s / uncapped_total if s <= MAX_SINGLE_PUBLISHER_SHARE else 0)
            for s in normalized
        ]

    pcts = _largest_remainder_round(normalized)
    return {pub_id: pct for (pub_id, _, _), pct in zip(scored, pcts)}


def assemble_config(
    profile: AdvertiserProfile,
    recommended: list[PublisherRecommendation],
    publishers_by_id: dict[str, Publisher],
    total_budget_usd: int | None = None,
) -> CampaignConfig:
    """Deterministic assembly of the final campaign config."""
    allocations = []
    for rec in recommended:
        usd = round(total_budget_usd * rec.budget_pct / 100) if total_budget_usd else None
        allocations.append(
            PublisherAllocation(publisher_id=rec.publisher_id, budget_pct=rec.budget_pct, budget_usd=usd)
        )

    if total_budget_usd:
        budget = Budget(mode="total_provided", total_usd=total_budget_usd)
    else:
        # No spend given → percentages only; never fabricate dollar figures.
        budget = Budget(
            mode="percentage_only_until_budget_provided",
            suggested_daily_range_usd=SUGGESTED_DAILY_RANGE_USD,
        )

    # Low confidence → observe (watch and learn), not fake precision.
    targeting_mode = (
        TargetingMode.OBSERVE
        if profile.confidence == Confidence.LOW or profile.signal == Signal.LOW_SIGNAL
        else TargetingMode.RESTRICT
    )

    age_bands = sorted(
        {publishers_by_id[r.publisher_id].audience.age_skew for r in recommended if r.publisher_id in publishers_by_id}
    )

    exclusions = ["low-fit / off-category inventory"]
    if profile.sensitive_category_flags:
        exclusions.append(f"sensitive category — human review required: {', '.join(profile.sensitive_category_flags)}")

    return CampaignConfig(
        objective=profile.purchase_objective,
        targeting_mode=targeting_mode,
        audience_attributes=[profile.target_customer] + profile.value_props[:2],
        demographic_filters={"age_bands": age_bands},
        publisher_allocations=allocations,
        budget=budget,
        exclusions=exclusions,
        assumptions=profile.assumptions,
        confidence=profile.confidence,
    )
