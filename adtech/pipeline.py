"""The six-stage pipeline, wired as one async function.

Stages 1→2→3→4 are sequential (each depends on the previous); Stage 5 fans
out concurrently over personas; Stage 6 is deterministic assembly. The
off_topic gate after Stage 1 short-circuits everything else.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

from pydantic import BaseModel

from adtech import budget, critique
from adtech.config import MAX_RECOMMENDED, RUNS_DIR, SCORE_THRESHOLD
from adtech.llm import call_llm, render_prompt
from adtech.retrieval import load_personas, retrieve_candidates
from adtech.schemas import (
    AdvertiserProfile,
    CampaignDraft,
    CreativeVariant,
    ExcludedPublisher,
    OffTopicResult,
    PersonaCreative,
    PersonaSelection,
    Publisher,
    PublisherRecommendation,
    RankOutput,
    Signal,
)
from adtech.signals import compute_signals

logger = logging.getLogger("adtech")

# event key → trace dump filename (also fixes the stage ordering shown in runs/)
STAGE_FILES = {
    "interpret": "01_interpret.json",
    "signals": "02_signals.json",
    "rank": "03_rank.json",
    "rank_split": "03b_rank_split.json",
    "personas": "04_personas.json",
    "creative": "05_creative.json",
    "config": "06_config.json",
    "draft": "07_draft.json",
    "off_topic": "99_off_topic.json",
}


def _format_placement(pub: "Publisher") -> str:
    """One publisher rendered as creative-facing placement context."""
    a = pub.audience
    dominant = max(a.gender_split, key=lambda g: a.gender_split[g])
    return (
        f"- {pub.name} ({pub.category}): AOV ${pub.avg_order_value_usd}, "
        f"audience {a.age_skew}, {dominant}-leaning, {a.income_tier} income. "
        f"Notes: {pub.notes}"
    )


def _placement_context(
    publisher_id: str | None,
    recommended: list[PublisherRecommendation],
    pubs_by_id: dict[str, "Publisher"],
) -> str:
    """Build the placement block for a creative call.

    Prefer the single publisher the persona was mapped to in Stage 4; fall back
    to all recommended publishers; degrade gracefully when nothing was matched.
    """
    if publisher_id and publisher_id in pubs_by_id:
        return _format_placement(pubs_by_id[publisher_id])
    if recommended:
        return "This persona runs across the recommended inventory:\n" + "\n".join(
            _format_placement(pubs_by_id[r.publisher_id]) for r in recommended if r.publisher_id in pubs_by_id
        )
    return "No specific publisher placement — write for the persona's general shopping context."


def _dump_stage(trace_id: str, stage_file: str, payload: Any) -> None:
    """Write a stage's output to runs/<trace_id>/ — the show-your-work trail."""
    run_dir = RUNS_DIR / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, indent=2, default=lambda o: o.model_dump() if isinstance(o, BaseModel) else str(o))
    (run_dir / stage_file).write_text(text)


async def run_pipeline(
    brief: str,
    total_budget_usd: int | None = None,
    trace: bool = True,
    on_event: Callable[[str, Any], None] | None = None,
) -> CampaignDraft | OffTopicResult:
    """Run the six-stage pipeline.

    `on_event(key, payload)` fires when visible stages start and when they
    complete (completion keys per STAGE_FILES) — the hook interfaces (API
    streaming, progress UIs) attach here without touching stage logic.
    """
    trace_id = uuid.uuid4().hex[:12]
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    log.info("run initiated: trace_id=%s artifacts=%s", trace_id, RUNS_DIR / trace_id)

    def emit(key: str, payload: Any) -> None:
        if trace:
            _dump_stage(trace_id, STAGE_FILES[key], payload)
        if on_event is not None:
            on_event(key, payload)

    def emit_start(key: str) -> None:
        if on_event is not None:
            on_event("stage_start", key)

    # ---- Stage 1: interpret the brief (LLM, fast, temp 0) -----------------
    log.info("stage 1: interpret")
    emit_start("interpret")
    profile = await call_llm("interpret", render_prompt("interpret", brief=brief), AdvertiserProfile)
    emit("interpret", profile)

    # The gate: nonsense input stops here — no ranking, no creative.
    if profile.signal == Signal.OFF_TOPIC:
        log.info("off_topic — short-circuiting")
        result = OffTopicResult(
            trace_id=trace_id,
            message=(
                "We couldn't identify an advertisable business in that description. "
                "Tell us what you sell and who it's for — e.g. \"We sell premium dog "
                "food for senior dogs, targeting owners who care about joint health.\""
            ),
            advertiser_profile=profile,
        )
        emit("off_topic", result)
        return result

    # ---- Stage 2: precompute mechanical signals (code) --------------------
    log.info("stage 2: signals")
    emit_start("signals")
    candidates = retrieve_candidates(profile)
    signals_by_id = {p.id: compute_signals(profile, p) for p in candidates}
    emit("signals", {pid: s for pid, s in signals_by_id.items()})

    # ---- Stage 3: rank publishers (LLM, strong, temp 0) --------------------
    log.info("stage 3: rank %d candidates", len(candidates))
    emit_start("rank")
    candidates_json = json.dumps(
        [{**p.model_dump(), "signals": signals_by_id[p.id].model_dump(exclude={"publisher_id"})} for p in candidates],
        indent=2,
    )
    rank = await call_llm(
        "rank",
        render_prompt("rank", advertiser_profile=profile.model_dump_json(indent=2), candidates_json=candidates_json),
        RankOutput,
    )
    emit("rank", rank)

    # Threshold (not top-K) decides recommendations; the split is code.
    pubs_by_id = {p.id: p for p in candidates}
    scored = sorted(
        (s for s in rank.scores if s.publisher_id in pubs_by_id), key=lambda s: s.score, reverse=True
    )
    winners = [s for s in scored if s.score >= SCORE_THRESHOLD][:MAX_RECOMMENDED]
    losers = [s for s in scored if s not in winners]

    allocation = budget.allocate(
        [(s.publisher_id, s.score, pubs_by_id[s.publisher_id].monthly_impressions) for s in winners]
    )
    recommended = [
        PublisherRecommendation(
            publisher_id=s.publisher_id,
            name=pubs_by_id[s.publisher_id].name,
            score=s.score,
            fit_reasons=s.fit_reasons,
            budget_pct=allocation[s.publisher_id],
        )
        for s in winners
    ]
    excluded = [
        ExcludedPublisher(
            publisher_id=s.publisher_id,
            name=pubs_by_id[s.publisher_id].name,
            reason="; ".join(s.fit_reasons) or f"score {s.score:.2f} below threshold {SCORE_THRESHOLD}",
        )
        for s in losers
    ]
    log.info("recommended %d / excluded %d", len(recommended), len(excluded))
    emit("rank_split", {"recommended": recommended, "excluded_count": len(excluded)})

    # ---- Stage 4: select personas, conditioned on the winners --------------
    log.info("stage 4: personas")
    emit_start("personas")
    all_personas = load_personas()
    personas_by_id = {p.id: p for p in all_personas}
    selection = await call_llm(
        "personas",
        render_prompt(
            "personas",
            advertiser_profile=profile.model_dump_json(indent=2),
            recommended_publishers=json.dumps([r.model_dump() for r in recommended], indent=2),
            personas_json=json.dumps([p.model_dump() for p in all_personas], indent=2),
        ),
        PersonaSelection,
    )
    choices = [c for c in selection.personas if c.persona_id in personas_by_id]
    emit("personas", selection)

    # ---- Stage 5: generate creative (LLM, strong, temp 0.8) — parallel -----
    log.info("stage 5: creative fan-out over %d personas", len(choices))
    emit_start("creative")
    variants: list[CreativeVariant] = await asyncio.gather(
        *(
            call_llm(
                "creative",
                render_prompt(
                    "creative",
                    advertiser_profile=profile.model_dump_json(indent=2),
                    persona_json=personas_by_id[c.persona_id].model_dump_json(indent=2),
                    why_this_persona=c.why_this_persona,
                    message_angle=c.message_angle,
                    placement_context=_placement_context(c.publisher_id, recommended, pubs_by_id),
                ),
                CreativeVariant,
            )
            for c in choices
        )
    )
    emit("creative", {"variants": variants})

    # ---- Stage 5.5: critique (stub — returns no flags) ----------------------
    flags = await critique.critique_variants(profile, variants)

    persona_creatives = [
        PersonaCreative(
            persona_id=c.persona_id,
            name=personas_by_id[c.persona_id].name,
            why_this_persona=c.why_this_persona,
            message_angle=c.message_angle,
            headline=v.headline,
            body=v.body,
            cta=v.cta,
            rationale=v.rationale,
            critique_flags=flags.get(c.persona_id, []),
        )
        for c, v in zip(choices, variants)
    ]

    # ---- Stage 6: assemble campaign config (code) ---------------------------
    log.info("stage 6: assemble config")
    emit_start("config")
    config = budget.assemble_config(profile, recommended, pubs_by_id, total_budget_usd)
    emit("config", config)

    draft = CampaignDraft(
        trace_id=trace_id,
        advertiser_profile=profile,
        publisher_recommendations=recommended,
        excluded_publishers=excluded,
        persona_creatives=persona_creatives,
        campaign_config=config,
    )
    emit("draft", draft)
    return draft
