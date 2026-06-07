# adgen — Ad Placement & Creative Generation POC

An advertiser describes their business in a sentence; the system returns ranked publisher
recommendations (with reasoning **and** justified exclusions), 3–5 persona-tuned creative
variants, and a structured campaign config. Design rationale lives in `ARCHITECTURE.md`;
all prompts live in `adtech/prompts/`.

## Run it

Live deployment: https://adgen-puce.vercel.app

```bash
uv sync
cp .env.example .env   # add your GEMINI_API_KEY (any LiteLLM provider works — see .env.example)
uv run python -m app.cli "We sell premium dog food for senior dogs, targeting owners who care about joint health"
uv run python -m app.cli "..." --budget 5000 -v   # optional budget + stage logs
```

Output is a readable terminal report plus run-scoped artifacts in `runs/<trace_id>/` (`result.json` + per-stage dumps). Try the fixtures in `data/example_advertisers.txt` — #15 ("idk just try it") exercises the off-topic gate, #7 (B2B SaaS) the honest zero-fit case.

**Web UI** (same pipeline, thin adapter):

```bash
uv run fastapi dev app/api.py   # → http://localhost:8000
```

`POST /api/campaigns` streams SSE stage events; the UI shows each stage live. No root `main.py` — the ASGI app is `app/api.py`. Deploys to Vercel as-is (`vercel deploy`) — set `GEMINI_API_KEY` in project env vars.

## Model quality note

For testing cost and latency, both configured model classes currently point to the small `gemini/gemini-3.1-flash-lite` model. This keeps the POC easy to run, but some weak ranking judgments, persona choices, or ad copy quality may be model-quality limitations rather than pipeline-design limitations. The model routing lives in `.env` / `adtech/config.py`, so using a stronger model for the `strong` class should improve output quality without changing pipeline code.

## What it is

Six-stage pipeline in `adtech/pipeline.py`:

1. **Interpret** — LLM gate; classifies brief as `clear` / `low_signal` / `off_topic` (off_topic short-circuits immediately)
2. **Precompute signals** — pure code; AOV fit, category overlap, gender alignment (the model never does arithmetic)
3. **Rank** — LLM scores every publisher in [0,1]; a threshold (not top-K) decides recommendations; exclusions fall out of the same output
4. **Select personas** — conditioned on the winning publishers; also assigns each a distinct `message_angle` + placement so the variants don't collapse into one voice
5. **Generate creative** — parallel fan-out, one LLM call per persona, written for its placement and steered by the assigned angle, `messaging_preferences`, and `disinterested_in`; emits headline + body + `cta`
6. **Assemble config** — pure code; fit-proportional budget capped by inventory ceilings, exploration floor, exact-100 rounding

Pydantic schemas + a validate-and-repair loop sit between every stage. Full design in `ARCHITECTURE.md`.

## With another week

- **Multi-turn clarification** — for `low_signal` briefs, ask one follow-up instead of guessing
- **Critique pass** — Stage 5.5 stub is wired; fill it in for repetition, persona drift, unsupported health claims
- **Eval harness first** — deterministic checks + LLM-vs-category-match-baseline comparison; generative systems look successful long before they're reliable
- **Caching** — key on `(brief_hash, prompt_version)`; the pipeline is deterministic at temp=0 stages

## Intentionally cut

- **Vector retrieval / embeddings** — 20 publishers fit in context; `retrieve_candidates()` is the designated swap point when the catalog grows
- **Database** — static data in memory, runs are ephemeral, artifacts are flat files; triggers for each future store are in `ARCHITECTURE.md §11`
- **Hand-tuned scoring weights** — with 20 rows and zero outcome data, a weighted formula is opinion dressed as arithmetic; code computes raw signals, the LLM weighs them and must explain itself
- **Auction/bid modeling** — config suggests a manual-CPC start with a stated upgrade path; simulating auctions without outcome data is theater

## Hard vs. easy

**Easy:** the happy path — any LLM ranks pet publishers for a dog-food brief and writes decent copy.

**Genuinely hard, and where the engineering lives:**

- **Trustworthy edges** — saying *zero publishers fit* for B2B SaaS instead of force-filling five; flagging that a $1,200 handbag exceeds the catalog's $198 AOV ceiling; surfacing assumptions on vague briefs instead of silently inventing a business
- **Evaluation** — knowing the ranker beats a dumb baseline instead of trusting fluent rationales
- **The feedback loop** — once campaigns emit outcomes, allocation/rotation become bandit problems and the ranker becomes calibratable; that's where the interesting work is
