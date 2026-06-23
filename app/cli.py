"""CLI — a thin adapter over run_pipeline(). No pipeline logic lives here.

Usage:
    uv run python -m app.cli "We sell premium dog food for senior dogs..." [--budget 5000]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from adtech.artifacts import write_result
from adtech.logging import configure_logging
from adtech.pipeline import run_pipeline
from adtech.retrieval import normalize_personas, normalize_publishers
from adtech.schemas import CampaignDraft, OffTopicResult, Persona, Publisher

RULE = "─" * 72


def _render_draft(draft: CampaignDraft) -> str:
    p = draft.advertiser_profile
    lines = [
        RULE,
        "CAMPAIGN DRAFT",
        RULE,
        f"  category: {p.category}" + (f" / {p.subcategory}" if p.subcategory else ""),
        f"  price tier: {p.price_tier}   ·   signal: {p.signal}   ·   confidence: {p.confidence}",
        f"  target customer: {p.target_customer}",
    ]
    if p.sensitive_category_flags:
        lines.append(f"  ⚠ sensitive categories: {', '.join(p.sensitive_category_flags)} (human review)")
    if p.assumptions:
        lines.append("  assumptions made:")
        lines.extend(f"    • {a}" for a in p.assumptions)

    lines += ["", RULE, f"RECOMMENDED PUBLISHERS ({len(draft.publisher_recommendations)})", RULE]
    if not draft.publisher_recommendations:
        lines.append("  none — this catalog has no suitable placement for this advertiser.")
    for rec in draft.publisher_recommendations:
        lines.append(f"  {rec.name}  ·  score {rec.score:.2f}  ·  {rec.budget_pct}% of budget")
        lines.extend(f"    • {r}" for r in rec.fit_reasons)
        lines.append("")

    lines += [RULE, f"EXCLUDED PUBLISHERS ({len(draft.excluded_publishers)})", RULE]
    for exc in draft.excluded_publishers:
        lines.append(f"  {exc.name}: {exc.reason}")

    lines += ["", RULE, f"CREATIVE VARIANTS ({len(draft.persona_creatives)})", RULE]
    for c in draft.persona_creatives:
        lines += [
            f"  ▸ {c.name}",
            f"    why: {c.why_this_persona}",
            f"    angle: {c.message_angle}",
            f'    headline: "{c.headline}"',
            f"    body: {c.body}",
            f"    cta: {c.cta}",
            f"    rationale: {c.rationale}",
        ]
        if c.critique_flags:
            lines.append(f"    ⚠ flags: {', '.join(c.critique_flags)}")
        lines.append("")

    cfg = draft.campaign_config
    budget_line = (
        f"total ${cfg.budget.total_usd}"
        if cfg.budget.total_usd
        else f"percentages only (suggested daily ${cfg.budget.suggested_daily_range_usd[0]}–"
        f"${cfg.budget.suggested_daily_range_usd[1]})"
    )
    lines += [
        RULE,
        "CAMPAIGN CONFIG",
        RULE,
        f"  objective: {cfg.objective}   ·   targeting mode: {cfg.targeting_mode}",
        f"  budget: {budget_line}",
        f"  bid strategy: {cfg.bid_strategy.type} → {cfg.bid_strategy.upgrade_path}",
        f"  audience: {', '.join(a for a in cfg.audience_attributes if a)}",
        f"  age bands: {', '.join(cfg.demographic_filters.get('age_bands', []))}",
        f"  frequency cap: {cfg.frequency_cap_per_user_per_day}/user/day   ·   rotation: {cfg.creative_rotation}",
    ]
    return "\n".join(lines)


def _render_off_topic(result: OffTopicResult) -> str:
    return "\n".join([RULE, "NO CAMPAIGN GENERATED (off-topic input)", RULE, f"  {result.message}"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a draft ad campaign from a one-line business description.")
    parser.add_argument("brief", help="the advertiser's business description")
    parser.add_argument("--budget", type=int, default=None, help="total budget in USD (optional)")
    parser.add_argument("--publishers", type=Path, default=None, help="path to a publishers JSON file (default: bundled sample catalog)")
    parser.add_argument("--personas", type=Path, default=None, help="path to a shopper-personas JSON file (default: bundled sample catalog)")
    parser.add_argument("--output", type=Path, default=None, help="path for the JSON result (default runs/<trace_id>/result.json)")
    parser.add_argument("--no-trace", action="store_true", help="skip per-stage dumps to runs/")
    parser.add_argument("-v", "--verbose", action="store_true", help="show per-stage progress logs")
    args = parser.parse_args()

    configure_logging(default_level="INFO", override_level="INFO" if args.verbose else None)

    try:
        publishers = (
            normalize_publishers([Publisher.model_validate(p) for p in json.loads(args.publishers.read_text())])
            if args.publishers
            else None
        )
        personas = (
            normalize_personas([Persona.model_validate(p) for p in json.loads(args.personas.read_text())])
            if args.personas
            else None
        )
    except Exception as err:  # bad catalog file — fail before the pipeline runs
        print(f"error: could not load catalog — {err}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(
            run_pipeline(
                args.brief,
                total_budget_usd=args.budget,
                trace=not args.no_trace,
                publishers=publishers,
                personas=personas,
            )
        )
    except Exception as err:  # surface a clean failure, not a stack trace
        print(f"error: pipeline failed — {err}", file=sys.stderr)
        return 1

    if isinstance(result, OffTopicResult):
        print(_render_off_topic(result))
    else:
        print(_render_draft(result))

    if args.output:
        out_path = args.output
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.model_dump_json(indent=2))
    else:
        out_path = write_result(result)
    print(f"\nFull JSON result: {out_path}")
    if not args.no_trace:
        print(f"Run artifacts:     runs/{result.trace_id}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
