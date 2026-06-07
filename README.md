# adgen — Ad Placement & Creative Generation POC

An advertiser describes their business in a sentence; the system returns ranked publisher
recommendations (with reasoning **and** justified exclusions), 3–5 persona-tuned creative
variants, and a structured campaign config. Design rationale lives in `ARCHITECTURE.md`;
all prompts live in `adtech/prompts/`.

## Run it

```bash
uv sync
cp .env.example .env   # add your GEMINI_API_KEY (any LiteLLM provider works — see .env.example)
uv run python -m app.cli "We sell premium dog food for senior dogs, targeting owners who care about joint health"
uv run python -m app.cli "..." --budget 5000 -v   # optional budget + stage logs
```

Output: a readable terminal report plus run-scoped artifacts in `runs/<trace_id>/`: `result.json`
for the full JSON result and per-stage dumps for the show-your-work trail. Try the fixtures in `data/example_advertisers.txt` —
#15 ("idk just try it") exercises the off-topic gate, #7 (B2B SaaS) the honest zero-fit case.

**Web UI** (same pipeline, another thin adapter): `uv run fastapi dev app/api.py` → http://localhost:8000.
`POST /api/campaigns` streams SSE stage events, so the UI (`app/templates/index.html`) shows each
stage's progress live and local API runs write the same `runs/<trace_id>/result.json` artifact.
There is no root `main.py`; the ASGI app lives in `app/api.py`. Deploys to Vercel as-is
(`vercel deploy`) — set `GEMINI_API_KEY` in the project's env vars.

## What it is

A six-stage pipeline (`adtech/pipeline.py`): **interpret** (LLM gate: `clear`/`low_signal`/`off_topic`) →
**precompute signals** (code: AOV-band fit, category overlap, gender alignment — the model never does
arithmetic) → **rank** (LLM scores *every* publisher in [0,1]; a threshold, not top-K, decides
recommendations — zero is allowed; exclusions fall out of the same scored output) → **select personas**
(conditioned on the *winning* publishers) → **generate creative** (parallel fan-out, one call per
persona, steered by `messaging_preferences` *and* `disinterested_in`) → **assemble config** (code:
fit-proportional budget capped by inventory ceilings, exploration floor, exact-100 rounding).
Pydantic schemas + a validate-and-repair loop sit between every stage. Model/temp routing is config
(`adtech/config.py`), not code.

## With another week

Build the eval harness first (`evals/README.md` has the plan): deterministic checks + the
LLM-vs-category-match-baseline comparison — generative systems look successful long before they're
reliable. Then the critique pass (Stage 5.5 stub: repetition, persona drift, unsupported health
claims), a Streamlit/FastAPI adapter over the same `run_pipeline()`, multi-turn clarification for
`low_signal` briefs, and caching keyed on `(brief_hash, prompt_version)`.

## Intentionally cut

- **Vector retrieval / embeddings** — 20 publishers fit in context; `retrieve_candidates()` is the
  designated swap point when the catalog grows.
- **Database** — static data in memory, runs are ephemeral, run artifacts are flat files. The triggers for
  each future store are documented in ARCHITECTURE.md §11.
- **Hand-tuned scoring weights** — with 20 rows and zero outcome data, a weighted formula is opinion
  dressed as arithmetic. Code computes raw signals; the LLM weighs them and must explain itself.
- **Auction/bid modeling** — config suggests a manual-CPC start with a stated upgrade path; simulating
  auctions without outcome data is theater.

## Hard vs. easy

Easy: the happy path — any LLM ranks pet publishers for a dog-food brief and writes decent copy.
Genuinely hard, and where the engineering lives: **being trustworthy at the edges** — saying *zero
publishers fit* for B2B SaaS instead of force-filling five; flagging that a $1,200 handbag exceeds the
catalog's $198 AOV ceiling rather than hiding it; surfacing assumptions on vague briefs instead of
silently inventing a business; and **evaluation** — knowing the ranker beats a dumb baseline instead of
trusting fluent rationales. The interesting future work is the feedback loop: once campaigns emit
outcomes, allocation/rotation become bandit problems and the ranker becomes calibratable.
