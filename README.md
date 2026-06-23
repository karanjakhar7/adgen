# adgen

Generate ranked publisher recommendations, persona-tuned ad creative, and a campaign config from a one-sentence business description. Bring your own publisher inventory and shopper personas, or start from the bundled sample catalog.

**Live demo:** https://adgen-puce.vercel.app

---

## How it works

1. Describe your business in a sentence or two
2. Add your publishers (ad networks, newsletters, apps) and shopper personas — or use the sample catalog
3. Get back: ranked publisher placements with fit reasoning, 3–5 ad creative variants tuned to each persona, and a structured campaign config with budget allocation

Publishers and personas are fully user-supplied. Only a name + category (publisher) or name + description (persona) are required — every other field is optional.

## Quickstart

```bash
git clone <repo>
cd adgen
uv sync
cp .env.example .env  # add GEMINI_API_KEY (or any LiteLLM-supported provider)
```

**Web UI:**
```bash
uv run fastapi dev app/api.py  # → http://localhost:8000
```

Open the Publishers and Shopper personas sections to customize your catalog, then describe your business and click Generate.

**CLI:**
```bash
uv run python -m app.cli "We sell premium running shoes for competitive distance runners. $250 a pair."
uv run python -m app.cli "..." --budget 5000            # include budget allocation
uv run python -m app.cli "..." --publishers pubs.json --personas personas.json  # custom catalog
```

## Deployment

```bash
vercel deploy
```

Set `GEMINI_API_KEY` (or your preferred model's key) in the Vercel project environment variables. The app is stateless — no database, no persistent storage — so it deploys as a single serverless function.

## Configuration

Model routing lives in `.env` and `adtech/config.py`. Both fast and strong model classes default to `gemini/gemini-3.1-flash-lite` for low-cost testing; swap in a stronger model to improve ranking and creative quality without changing any pipeline code.

```bash
LLM_MODEL_FAST=claude-haiku-4-5-20251001
LLM_MODEL_STRONG=claude-sonnet-4-6
```

LiteLLM infers the API key from the model name prefix (`claude-` → `ANTHROPIC_API_KEY`, `gemini/` → `GEMINI_API_KEY`, etc.).

## Pipeline

Six stages in `adtech/pipeline.py`:

| Stage | Type | What it does |
|-------|------|-------------|
| Interpret | LLM, temp 0 | Parses the brief into a typed profile; gates on `off_topic` input |
| Precompute signals | Code | AOV fit, category overlap, gender alignment — no LLM arithmetic |
| Rank publishers | LLM, temp 0 | Scores each publisher 0–1; a threshold (not top-K) decides recommendations |
| Select personas | LLM, temp 0.3 | Picks personas conditioned on winning publishers; assigns distinct message angles |
| Generate creative | LLM, temp 0.8 | Parallel fan-out — one call per persona, steered by angle + placement context |
| Assemble config | Code | Fit-proportional budget allocation, frequency cap, bid strategy |

Pydantic v2 schemas are the typed contracts between every stage. A validate-and-repair loop feeds schema errors back to the model and retries before failing.

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/campaigns` | Run the pipeline; streams SSE stage events then the final result |
| `GET` | `/api/sample-catalog` | Returns the bundled publishers and personas for UI prefill |
| `GET` | `/api/health` | Health check |

`POST /api/campaigns` request shape:
```json
{
  "brief": "string",
  "budget_usd": 5000,
  "publishers": [...],
  "personas": [...]
}
```

`publishers` and `personas` are optional — the sample catalog is used when omitted.

## Project structure

```
adtech/
  pipeline.py     six-stage pipeline
  schemas.py      Pydantic models (the contracts between stages)
  signals.py      mechanical fit signals (pure code)
  budget.py       budget allocation (pure code)
  retrieval.py    catalog loading + normalization
  llm.py          LiteLLM wrapper + validate-and-repair loop
  prompts/        prompt templates (one .txt per LLM stage)
app/
  api.py          FastAPI app (SSE streaming adapter)
  cli.py          CLI adapter
  templates/      web UI (single-file vanilla JS)
data/
  publishers.json       sample publisher catalog (~20 entries)
  shopper_personas.json sample persona catalog (10 entries)
```

## Development

```bash
uv run ruff check .
uv run --with mypy mypy adtech/
uv run pytest
```

Per-run artifacts (stage dumps + final result JSON) are written to `runs/<trace_id>/` locally. On Vercel, artifact writes are skipped — the SSE stream carries the full result.
