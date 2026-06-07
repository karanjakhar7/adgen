# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **working prototype** (POC) for an ad placement & creative generation system. An advertiser submits a one-to-two sentence business description; the system returns ranked publisher recommendations, 3–5 persona-tuned ad creative variants, and a structured campaign config.

The pipeline (`adtech/`), CLI (`app/cli.py`), and streaming web UI (`app/api.py` + `app/templates/`) are built per the spec in `ARCHITECTURE.md`. The ASGI app lives in `app/api.py`; there is no root `main.py`. Remaining stubs: `adtech/critique.py` (Stage 5.5) and `evals/`.

## Commands

This project uses `uv` for package management (Python 3.12+, see `.python-version`).

```bash
# Install dependencies
uv sync

# Run the CLI
uv run python -m app.cli "We sell premium dog food for senior dogs targeting owners who care about joint health"

# Run the API + web UI (app/api.py exposes the ASGI app; UI at app/templates/index.html)
uv run fastapi dev app/api.py

# Run tests
uv run pytest

# Run a single test file
uv run pytest evals/test_deterministic.py -v

# Lint / type check
uv run ruff check .
uv run mypy adtech/
```

## LLM Configuration

LiteLLM is used as the LLM client — see `.env.example` for required env vars. LiteLLM infers the API key from the environment based on the model name string (e.g., `claude-3-5-haiku-20241022` reads `ANTHROPIC_API_KEY`). Model routing is config, not code — the stage→(model, temperature) map lives in `adtech/config.py`.

For testing, both configured model classes currently use the small `gemini/gemini-3.1-flash-lite` model. This keeps the POC cheaper and faster to run, but weak ranking or creative output may improve by assigning a stronger model to `LLM_MODEL_STRONG` without changing pipeline code.

## Architecture

The pipeline is defined in `ARCHITECTURE.md` (the authoritative build contract). Key structural decisions:

**Six-stage pipeline in `adtech/pipeline.py`:**

1. **Interpret** (`adtech/prompts/interpret.txt`) — LLM, temp=0. Parses the advertiser brief into a typed `AdvertiserProfile` with a `signal` field (`clear`/`low_signal`/`off_topic`) that gates the rest of the pipeline.
2. **Precompute signals** (`adtech/signals.py`) — Pure code. Computes mechanical fit signals (AOV ratio, gender-skew alignment, category overlap) for every publisher candidate. The model never does arithmetic.
3. **Rank publishers** (`adtech/prompts/rank.txt`) — LLM, temp=0. Uses the precomputed signals + qualitative publisher `notes` to produce a [0,1] score per publisher. A score threshold (not top-K) decides recommendations; below-threshold publishers populate `excluded_publishers`.
4. **Select personas** (`adtech/prompts/personas.txt`) — LLM, temp=0.3. Conditioned on the *winning publishers* from Stage 3 to keep persona/publisher pairing coherent. Also acts as message strategist: emits a distinct single-minded `message_angle` and a `publisher_id` placement mapping per pick.
5. **Generate creative** (`adtech/prompts/creative.txt`) — LLM, temp=0.8. Fans out concurrently via `asyncio.gather`, one call per persona. Each call is conditioned on the persona record (`messaging_preferences` do / `disinterested_in` don't), the assigned `message_angle`, and the **placement context** (the mapped publisher's AOV, audience, and `notes`). Produces headline + body + `cta` (matched to `purchase_objective`); enforces compliant phrasing when `health_claim` is flagged.
6. **Assemble config** (`adtech/budget.py`) — Pure code. Budget allocation uses fit-proportional shares capped by `monthly_impressions` inventory ceilings, with an exploration floor for lower-ranked publishers.

**Key module responsibilities:**

- `adtech/llm.py` — wraps `litellm.acompletion`, owns JSON parsing, and implements the validate-and-repair loop (feeds Pydantic validation errors back to the model, retries 1–2x before failing).
- `adtech/schemas.py` — Pydantic v2 models are the typed contracts between every stage. Stages never pass raw dicts.
- `adtech/retrieval.py` — `retrieve_candidates()` returns all ~20 publishers today; this is the designated seam for a future vector-store swap when the catalog grows.
- `adtech/critique.py` — Stage 5.5 stub. Interface defined; returns empty `critique_flags` for now.
- `app/cli.py` — thin adapter only: parses args, calls `asyncio.run(run_pipeline(brief))`, writes output. No pipeline logic here.

**The `off_topic` gate:** If Stage 1 returns `signal: "off_topic"`, the pipeline short-circuits immediately and returns a graceful explanation — no publisher ranking, no creative generation.

**Budget is optional:** When no spend amount is given, Stage 6 returns allocation percentages only with a suggested daily range, never fabricated dollar figures.

## Data

Static reference data lives in `data/` and is loaded into typed in-memory objects at startup:

- `publishers.json` — ~20 publishers with demographics, AOV, `monthly_impressions`, and qualitative `notes`
- `shopper_personas.json` — 10 personas with `messaging_preferences` and `disinterested_in`
- `example_advertisers.txt` — the test fixture set (see `ARCHITECTURE.md §7` for expected behavior per example)

## Per-run Debugging

Each local run writes artifacts under `runs/<trace_id>/`: optional per-stage dumps such as `01_interpret.json`, plus the final result as `result.json`. The `runs/` directory is gitignored.

## Eval Harness (`evals/`)

Two check types (per `ARCHITECTURE.md §8`):

- **Deterministic:** valid JSON against schema, allocations sum to 100%, `off_topic` inputs produce no campaign, inventory ceilings respected.
- **Baseline comparison:** the LLM hybrid ranker must beat a trivial category-match ranker — this is the correctness bar, not prose quality.
