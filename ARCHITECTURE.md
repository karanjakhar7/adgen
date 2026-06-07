# Ad Placement & Creative Generation — Architecture Spec (POC)

**Status:** draft v1 · **Scope:** working prototype with a clean path to production

An advertiser types one or two sentences about their business. The system returns a ranked list of recommended publishers (with reasoning and justified exclusions), 3–5 persona-tuned creative variants, and a structured campaign config ready for human review or a downstream system to launch.

This document is the build contract: what each stage does, what is code vs. model, the data shapes, how edge cases are handled, how we measure quality, and where the production seams are.

---

## 1. Design principles

These are the load-bearing decisions. Everything downstream follows from them.

**The catalog fits in context.** ~20 publishers and 10 personas is the entire dataset. It goes into the prompt whole on every call. No vector database, no embeddings, no retrieval layer for the POC. This single fact removes most of the infrastructure a real ad-matching system would need. Retrieval is treated as a *seam* (see §10), not a component we build now.

**Split work by job and by temperature.** The pipeline is multiple focused LLM calls, not one mega-prompt. The clinching technical reason is temperature: ranking needs near-deterministic output (temp ≈ 0) so scores are stable and reproducible; creative needs variety (temp ≈ 0.8) so variants don't collapse into the same copy. You cannot serve both from one call. Once you accept two calls, the other splits (independent retry, per-stage model routing, independent evaluation) pay for themselves.

**Code does math and structure; the model does language and judgment.** Budget arithmetic, allocation ceilings, demographic-overlap calculations, the excluded-publisher list, and config serialization are deterministic code — testable, reproducible, and free of token cost. The model is reserved for interpreting messy text, qualitative fit judgment, and writing copy.

**Every output carries its reasoning.** "Show your work" is not a feature bolted on at the end; it is a property of the schema. Each publisher score, persona choice, and creative variant ships with a rationale field. The final report just renders what the JSON already contains.

**Be honest about bad fit and bad input.** The system must be allowed to recommend *few or zero* publishers, to flag weak price-fit, to lower its confidence, and to refuse nonsense input. Force-filling five publishers for a business this catalog can't serve is the failure mode we explicitly design against.

---

## 2. Pipeline overview

Six stages, two of which are pure code. Each stage is a pure typed function of its input; state is passed forward explicitly.

```
advertiser text
  → [1] interpret            (LLM, fast,   temp 0)   → advertiser_profile + gate
  → [2] precompute signals   (CODE)                  → mechanical fit signals
  → [3] rank publishers      (LLM, strong, temp 0)   → scored publishers + exclusions
  → [4] select personas      (LLM, fast,   temp 0.3) → 3–5 personas + reasoning
  → [5] generate creative    (LLM, strong, temp 0.8) → 1 variant per persona  [PARALLEL]
  → [5.5] critique (optional)(LLM, strong, temp 0)   → flags on the variants  [BATCHED]
  → [6] assemble config       (CODE)                  → campaign_config
  → campaign draft
```

| # | Stage | Who | Model role | Temp | Why it's split out |
|---|-------|-----|-----------|------|--------------------|
| 1 | Interpret | LLM | fast | 0 | Input gate + normalization; cheap reasoning over a short string |
| 2 | Precompute signals | code | — | — | Arithmetic the model shouldn't do; deterministic & testable |
| 3 | Rank publishers | LLM | strong | 0 | Qualitative judgment; needs stable, reproducible scores |
| 4 | Select personas | LLM | fast | 0.3 | Conditioned on winning publishers for coherence |
| 5 | Generate creative | LLM | strong | 0.8 | Needs variety; independent per persona → parallel fan-out |
| 5.5 | Critique | LLM | strong | 0 | Optional QA: repetition, persona-fit, unsupported claims |
| 6 | Assemble config | code | — | — | Budget math, allocation ceilings, serialization |

---

## 3. Stage detail

### Stage 1 — Interpret the brief (LLM, fast, temp 0)

Turns the free-text one-liner into a normalized `advertiser_profile` **and acts as the input gate**. Output is strict JSON.

Extracts: `category`, `subcategory`, `value_props`, `target_customer`, `price_tier` (budget / mid / premium / luxury), `purchase_objective`, `negative_constraints`, three **creative-ready** fields that give Stage 5 better raw material, plus three control fields that drive the rest of the pipeline.

Creative-ready fields:
- `emotional_benefit` — the human payoff under the functional `value_props` ("joint support" → "more pain-free years with a dog who can still climb the stairs"). Copy sells this, not the feature.
- `proof_points` — the *positive* inventory of credibility claims the brief actually supports ("vet-formulated", "recycled ocean plastic"). Pairs with the "don't invent facts" rule: creative may cite from this list and nothing else.
- `brand_voice` — the brand's tonal register ("clinical & reassuring", "playful & irreverent"). The on-brand voice that must hold across every persona, so the brand stays recognizable even as the message flexes per audience.

Control fields:

- `signal` — `clear` | `low_signal` | `off_topic`. This is the gate. `off_topic` (e.g. "idk just try it") short-circuits the pipeline with a graceful explanation. `low_signal` (e.g. "we help people feel better") proceeds *with assumptions surfaced* and lowered confidence.
- `confidence` — `high` | `medium` | `low`. Flows through to `targeting_mode` and budget caution downstream.
- `sensitive_category_flags` — health, finance, children, employment, housing, etc. When set, downstream targeting is narrowed and the draft is marked for human review. (The "joint health" dog-food example is a regulated health claim — this is not hypothetical.)
- `assumptions` — explicit list of anything the model inferred rather than read. Surfacing assumptions instead of silently inventing them *is* part of showing the work.

### Stage 2 — Precompute mechanical signals (code)

Before ranking, code computes the signals that are arithmetic, not judgment, for every candidate publisher and attaches them to the candidate record:

- gender-skew alignment vs. the brief's implied audience (0.82 vs. 0.94 is a number, not an opinion)
- AOV ratio (publisher AOV vs. brief's `price_tier` band) — surfaces the price-ceiling problem
- income-tier and geo overlap
- category / subcategory token overlap (the cheap, exact-match signal)
- `monthly_impressions` (inventory size — needed later for budget ceilings)

These become *inputs* to the ranking prompt. This keeps the model out of arithmetic, keeps the numbers stable across runs, and makes them unit-testable on their own.

### Stage 3 — Rank publishers (LLM, strong, temp 0)

Consumes the candidates (with precomputed signals attached) plus the full publisher records — crucially including the qualitative `notes`. Outputs a score in [0,1] for **every** candidate with a per-dimension rationale.

This is where the model earns its place. The `notes` encode directional, qualitative fit that field-matching and embedding similarity both miss. Example: Tailcrate's note "fun, playful brand voice converts best" is semantically *close* to any pet brief, but for a premium, clinical senior-dog-food brand it's a tone *clash* — a negative signal. Embedding cosine similarity is undirected and would score it high; an LLM reasons about the direction.

A score **threshold** (not a fixed top-K) decides how many publishers are recommended — possibly zero. Everything below threshold becomes the `excluded_publishers` list with reasons, which falls out of the same scored output for free.

### Stage 4 — Select personas (LLM, fast, temp 0.3)

Picks 3–5 plausible shopper personas, **conditioned on the publishers that actually won** in Stage 3. Ordering matters: personas and publishers share enough category/AOV vocabulary that conditioning persona choice on the winning inventory keeps the pairing coherent — you never generate a "Convenience-First Millennial" ad for a $1,200 handbag that only placed on affluent-classic publishers.

This stage also acts as **message strategist**. Per pick it outputs: persona id, a `why_this_persona` rationale, a single-minded `message_angle` (the ONE idea that variant leads with), and a `publisher_id` mapping the persona to the placement it best fits. The angles are forced to differ across picks — because Stage 5 fans out independently, naming a distinct angle here is what stops the variants from collapsing into one voice downstream.

### Stage 5 — Generate creative (LLM, strong, temp 0.8, parallel)

One headline + body + `cta` + rationale per selected persona. The personas are independent, so this **fans out concurrently** (`asyncio.gather`), which is where parallelism actually buys latency.

Each call is conditioned on three things:
- the persona record — `messaging_preferences` (the *do*: voice and claims to use) and `disinterested_in` (the *don't*: the negative constraint)
- the `message_angle` assigned in Stage 4 — a hard "lead with exactly this one idea, don't cram value props" instruction
- the **placement context** — the mapped publisher's AOV, audience, and qualitative `notes`. A top copywriter never writes blind to where the ad runs: the same offer is pitched differently on impulse, late-night Swiftcart (AOV $28) than on considered, affluent Linden Park (AOV $128), and a publisher note like Tailcrate's "playful voice converts best" or Daily Form's "skeptical of unsubstantiated claims" steers tone directly.

Two things stop variants collapsing into generic copy: the negative constraint (telling the model the Affluent Classic rejects "influencer positioning, loud aesthetics") and the distinct per-persona angle. The `cta` is matched to `purchase_objective` (trial → "Start your free trial"; subscription → "Subscribe & save"), and when `sensitive_category_flags` contains `health_claim` the prompt enforces supportive, non-curative phrasing at generation time — the compliance backstop the Stage 5.5 critique stub does not yet provide.

### Stage 5.5 — Critique pass (LLM, strong, temp 0, optional, batched)

A single batched call over all generated variants that flags: cross-variant repetition, persona-fit drift, and — most importantly — **unsupported or non-compliant claims** (the regulated-claims concern, operationalized at the creative layer). Batched over all variants so it adds one call, not N. Optional in v1 (see open decisions).

### Stage 6 — Assemble campaign config (code)

Deterministic assembly of the final config (§5 for the algorithm, §6 for the shape). No LLM. Budget allocation, bid strategy, `targeting_mode`, exclusions, and the propagated `assumptions`/`confidence` are all computed here.

---

## 4. The matching approach (why it's a hybrid, not either extreme)

Two tempting extremes, both wrong for this data:

- **Pure deterministic scoring** (a weighted formula of overlaps and penalties) requires inventing and tuning weights with nothing to calibrate against — 20 rows and zero outcome data. Hand-picked weights look rigorous but are just opinion expressed as arithmetic.
- **Pure LLM ranking** (model both scores and explains) can't be trusted blindly: you can't tell whether the rationale is genuine or nice prose wrapped around a bad call.

The hybrid answers both concerns: **code precomputes the mechanical signals** (Stage 2), the **LLM does the qualitative judgment and emits the final score + rationale** (Stage 3), and the **eval harness** (§8) checks whether the LLM ranker actually beats a dumb category-match baseline rather than trusting that it does.

---

## 5. Budget allocation algorithm (code)

Allocation is a function of fit score **and** an inventory ceiling — not fit alone.

1. Take recommended publishers (above threshold) with their fit scores.
2. Compute the fit-proportional share: `share_i = score_i / Σ score`.
3. Cap each share by an inventory ceiling derived from `monthly_impressions`. A tiny-inventory publisher (Hearthstone, 2.8M) physically cannot absorb a large budget no matter how well it fits, unlike Swiftcart (84M). Allocation = `min(fit_share, inventory_ceiling)`.
4. Apply an **exploration floor** so a lower-ranked-but-plausible publisher still gets some spend (room to learn once feedback exists).
5. Redistribute any capped/floored remainder, then normalize to 100%.

**Budget input is optional.** If the advertiser gives no spend amount, return **allocation percentages only** plus an optional suggested daily range. If they give a budget, allocate it proportionally using the above. This avoids fabricating absolute dollar figures the input doesn't support.

**Bid strategy** stays a suggested starting point with reasoning (the glossary explicitly says not to model auction dynamics): a simple CPC-style start, with a stated upgrade path to a conversion/value-based automated strategy *once real outcome data exists*. We do not simulate auctions.

---

## 6. Data contracts

Pydantic v2 models are the interface between every stage. Each LLM call is asked for JSON matching its schema; output is validated; on failure a **repair loop** feeds the validation error back and retries (1–2 attempts) before the stage fails. Downstream stages therefore never see malformed data.

Final output shape (ids are the real catalog ids — `pub_xxx`, `persona_xxx`):

```json
{
  "advertiser_profile": {
    "category": "pet_food",
    "subcategory": "senior_dog_food",
    "price_tier": "premium",
    "value_props": ["joint support", "longevity", "vet-formulated"],
    "emotional_benefit": "more pain-free years with a dog who can still climb the stairs",
    "proof_points": ["vet-formulated", "grain-free"],
    "brand_voice": "clinical & reassuring",
    "target_customer": "owners of senior dogs who prioritize health",
    "purchase_objective": "subscription_signup",
    "sensitive_category_flags": ["health_claim"],
    "assumptions": [],
    "confidence": "high",
    "signal": "clear"
  },
  "publisher_recommendations": [
    {
      "publisher_id": "pub_007",
      "name": "Pawline",
      "score": 0.91,
      "fit_reasons": [
        "pet subscription audience, premium-positioned",
        "owners described as health-conscious about pets"
      ],
      "budget_pct": 38
    }
  ],
  "excluded_publishers": [
    { "publisher_id": "pub_018", "name": "Tailcrate",
      "reason": "playful brand voice clashes with premium clinical positioning; low AOV" }
  ],
  "persona_creatives": [
    {
      "persona_id": "persona_004",
      "name": "The Pet Parent",
      "why_this_persona": "reads ingredient labels, pays a premium for pet health",
      "message_angle": "more good years together",
      "headline": "Built for the dog who built your family",
      "body": "Vet-formulated nutrition for senior dogs — joint support and the kind of ingredient list you actually want to read.",
      "cta": "Subscribe & save",
      "critique_flags": []
    }
  ],
  "campaign_config": {
    "objective": "purchase",
    "targeting_mode": "restrict",
    "audience_attributes": ["senior dog owners", "premium pet shoppers"],
    "demographic_filters": { "age_bands": ["25-34","35-44","45-54","55-64"] },
    "geo": ["US"],
    "devices": ["mobile", "desktop"],
    "publisher_allocations": [ { "publisher_id": "pub_007", "budget_pct": 38 } ],
    "budget": { "mode": "percentage_only_until_budget_provided",
                "suggested_daily_range_usd": [50, 150] },
    "bid_strategy": { "type": "manual_cpc_start",
                      "upgrade_path": "target_cpa_when_conversion_data_exists" },
    "frequency_cap_per_user_per_day": 3,
    "creative_rotation": "even_until_data_exists",
    "exclusions": ["low-fit / off-category inventory"],
    "assumptions": [],
    "confidence": "high"
  }
}
```

Notes on the config shape:
- `targeting_mode` is `restrict` for high-confidence briefs and **`observe`** for low-confidence ones — an honest way to say "watch this audience and learn" rather than fake precision.
- `frequency_cap` and `creative_rotation` are static defaults in v1, but they're deliberately in the schema now: they're the anchors a future optimizer (bandits, fatigue-aware rotation) hangs off.
- `objective`, `geo`, `device` make the object read like a real campaign without adding logic.

---

## 7. Edge-case handling

The provided `example_advertisers.txt` is not just sample input — it's the test fixture set, already written. Expected behavior:

| Example | Input gist | Expected behavior |
|---------|-----------|-------------------|
| #1 | Premium senior-dog food, joint health | Clear; pet publishers rank; `sensitive_category_flags: [health_claim]` set |
| #7 | B2B SaaS for dental practices | **Near-zero recommendations**; honest "this catalog is consumer-retail, no suitable placement" |
| #10 | $1,200 Italian handbags | Best-available but **weak price-fit flag**; AOV far above catalog ceiling ($198 max) |
| #6 | $650+ backcountry ski shells | Same price-ceiling story; `confidence` reflects the gap |
| #5 | "We help people feel better" | `low_signal`; proceed with surfaced assumptions, lowered confidence, `observe` mode |
| #8 | "A new kind of thing for moms" | `low_signal`; audience hint but no product — assumptions surfaced |
| #15 | "idk just try it" | `off_topic`; gate stops the pipeline, asks for a real description |

These map directly to the eval rubric.

---

## 8. Evaluation harness (built with the POC, not after)

Generative systems look successful long before they're reliable, so a tiny offline benchmark ships with v1. The fixtures are the example advertisers above.

Two kinds of check:

**Deterministic** — valid JSON against the schema; allocations sum to 100%; no recommended publisher below the score threshold; `off_topic` inputs produce no campaign; allocation respects inventory ceilings; sensitive flags trigger the expected targeting narrowing.

**Human-scored rubric** (5 axes): are the top publishers plausible? are the exclusions justified? do the chosen personas make sense? is the creative distinct across personas and free of the personas' stated disinterests? is the config internally consistent?

**Baseline comparison.** A trivial category-match ranker is the control. The eval's job is to show the LLM hybrid ranker beats it — otherwise we're paying for nice explanations around a baseline-quality decision.

---

## 9. Guardrails & policy

Three lightweight layers, all cheap in v1:

1. **Sensitive-category flag at interpretation** — narrows targeting or routes to human review for health/finance/children/employment/housing briefs.
2. **Claims critique at the creative layer** (Stage 5.5) — flags unsupported or non-compliant claims before a human sees the draft.
3. **Confidence-driven targeting** — low-confidence briefs default to `observe` mode rather than hard `restrict`.

---

## 10. Production seams (build the boundary now, defer the heavy version)

The POC is small, but these boundaries are placed so scaling is a swap, not a rewrite.

**Retrieval seam.** Ranking is structured as `retrieve_candidates(profile) → rank(candidates)`. Today `retrieve_candidates` returns all ~20 publishers. When the catalog grows to thousands, its body becomes metadata filters (category, geo, price band) + vector search returning the top ~30 — and the ranking prompt downstream never changes. This is the single most important seam.

**Schema-contract seam.** Pydantic models + the validate/repair loop (§6) mean stages are decoupled by typed contracts. This is the reliability backbone and is in from day one.

**Orchestration seam.** Stages are pure functions `(input, catalog_version, prompt_version) → output` wired with plain `asyncio`. Later they lift into LangGraph / Temporal / Prefect for durable execution and human-in-the-loop review without rewriting stage bodies. Not built now; just not designed against.

**Observability.** Structured logging with a trace id per run, capturing each stage's input and output. Cheapest production investment we make; in from day one.

**Caching & versioning.** Stages keyed on `(brief_hash, catalog_version, prompt_version)`. Interpretation and ranking are cacheable; creative is the variable part. Enables replay and prompt A/B later.

---

## 11. Execution model, storage & stack

### Execution model

A pipeline run is a **synchronous batch job**: load static reference data once → run the stages → emit the campaign-draft artifact → exit. There is no long-running server, no queue, and no background workers in the POC. Each invocation is fully self-contained.

The pipeline itself is an **`async` function** because of one stage: the creative fan-out (Stage 5) runs `asyncio.gather` over the selected personas. Stages 1→2→3→4 are sequential (each depends on the previous); Stage 5 is the only concurrent point; Stage 6 is deterministic assembly. The CLI is a thin adapter that calls `asyncio.run(run_pipeline(brief))` and writes the result.

Critically, **the pipeline is a pure async function and the interface is a thin adapter over it.** The CLI is that adapter today. The future FastAPI service and Streamlit UI are *also* thin adapters that call the same `run_pipeline()` — they add no pipeline logic. This is the modularity boundary: business logic lives in `adtech/`, interfaces live in `app/`, and an interface can be added or swapped without touching a stage.

### Do we need a database? No — and here's exactly when that changes.

For the CLI POC the answer is no. There are three kinds of state, and none of them needs durable storage:

- **Static reference data** (publishers, personas, example advertisers) — read-only, ~30 rows total. Loaded into typed objects in memory at startup.
- **Per-run intermediate state** (profile, signals, scores, personas, creatives) — ephemeral; it lives only for the duration of one run as in-memory objects passed between stages, then the process exits.
- **Output** (the campaign draft) — written to `runs/<trace_id>/result.json` (and/or stdout).

For debuggability and to feed the future eval harness, each run keeps its artifacts in one run-scoped folder (`runs/<trace_id>/`). The final result is `result.json`; optional stage JSON files (`01_interpret.json`, …) are the "show your work" trail. This is **flat files, not a database**.

"Do we need a DB" is really four separate future questions, each with its own trigger and its own appropriate store. None is a POC need:

| Future need | Trigger | Appropriate store |
|-------------|---------|-------------------|
| Cache stage outputs to cut cost | API serving repeated briefs | In-memory LRU → Redis |
| Run history / audit / analytics | Many runs you need to query | Append-only JSONL → Postgres |
| Catalog at scale (the retrieval seam) | Catalog grows to thousands | Vector store (e.g. pgvector, Qdrant) |
| Feedback / online learning | Live campaigns emit impressions/clicks/conversions | Time-series / OLAP store |

The takeaway: keep the POC DB-free, but the seams above (§10) are placed so each store slots in behind a function boundary rather than forcing a rewrite.

### Stack

- **Python** with `asyncio` (the creative fan-out is `asyncio.gather`).
- **Pydantic v2** for the inter-stage contracts and the validate/repair loop.
- **LiteLLM** as the LLM client — one interface, any provider. `adtech/llm.py` wraps `litellm.completion` and owns: the per-stage model + temperature config, JSON/structured-output handling, and the validate-and-repair loop against the Pydantic schemas. Because LiteLLM normalizes provider differences, switching or mixing providers per stage is a config change, not a code change.
- **Data in memory** — JSON loaded at startup; `retrieve_candidates` is the swap point for a vector store later.
- **structlog** (or equivalent) with a per-run trace id.
- **Interface:** CLI first, as a thin adapter over `run_pipeline()`. FastAPI + Streamlit are deferred — their repo layout will be provided separately; the design already treats them as additional adapters, not new logic.

Take a look at .env.examples file to get an idea of how LLM is configured and based on model name, litellm will read the env for required key and generate the output.

Model routing is config, not code — a stage → (model string, temp) map consumed by `llm.py`:

For this testing POC, both the `fast` and `strong` model classes are configured to the small `gemini/gemini-3.1-flash-lite` model. That is intentional for cost and latency while evaluating the pipeline shape. Some lower-quality recommendations or creative variants should therefore be read as likely to improve with a stronger model, especially for the `strong` class stages (`rank`, `creative`, and `critique`), without requiring code changes.

| Stage | Model class | Temp |
|-------|-------------|------|
| Interpret | fast | 0 |
| Rank | strong | 0 |
| Select personas | fast | 0.3 |
| Creative | strong | 0.8 |
| Critique | strong | 0 |

---

## 12. Proposed repo structure

A standalone, self-contained directory. Core pipeline is built now; evals, the critique stage, and the FastAPI/Streamlit interfaces are left as clearly marked seams with stubs/TODOs.

```
adtech-poc/
├── data/                      # publishers.json, shopper_personas.json, example_advertisers.txt
├── adtech/                    # ── business logic (no interface code) ──
│   ├── config.py              # stage → (model string, temp) routing map
│   ├── schemas.py             # Pydantic models = the stage contracts
│   ├── llm.py                 # LiteLLM wrapper: acompletion + JSON handling + repair loop
│   ├── retrieval.py           # retrieve_candidates() — scalability seam (returns all, for now)
│   ├── signals.py             # Stage 2: deterministic precomputed signals
│   ├── budget.py              # Stage 6: allocation w/ inventory ceiling + floor
│   ├── critique.py            # Stage 5.5 — STUB: interface defined, returns no flags (TODO)
│   ├── pipeline.py            # async run_pipeline(): the stages wired together
│   └── prompts/               # one template per LLM stage
│       ├── interpret.txt
│       ├── rank.txt
│       ├── personas.txt
│       └── creative.txt
│       # critique.txt — TODO, added with Stage 5.5
├── app/
│   └── cli.py                 # thin adapter: parse args → asyncio.run(run_pipeline()) → write output
│       # fastapi/, streamlit/ — TODO, layout provided separately; both call run_pipeline()
├── evals/                     # ── TODO (future) ──
│   # fixtures.py  — example advertisers + expected outcomes (see §7/§8)
│   # run_evals.py — deterministic checks + baseline comparison
├── runs/                      # per-run results + stage dumps for debugging (flat files, gitignored)
├── pyproject.toml
└── README.md
```

The `critique.py` and `evals/` placeholders matter: defining the interface now (even as a no-op) means wiring them in later is filling a stub, not restructuring the pipeline.

---

## 13. Decisions locked for v1

- **Interface:** CLI first, in a standalone directory, as a thin adapter over `run_pipeline()`. FastAPI + Streamlit deferred — layout provided separately; both will be additional adapters over the same pipeline.
- **LLM client:** LiteLLM, wrapped in `adtech/llm.py`, with per-stage model + temperature config.
- **Storage:** no database — in-memory reference data, ephemeral per-run state, flat-file output and optional per-run stage dumps. DB triggers documented in §11.
- **Scope:** core pipeline only. Eval harness and the critique pass (5.5) are stubbed/TODO seams, not built in v1.
- **Budget input:** optional — percentages-only when no spend is given.
- **Score threshold:** tunable cutoff with a soft cap (~6 publishers max), allowing zero recommendations.

---

## 14. Explicitly out of scope (future work)

Embeddings / vector store (slots into the retrieval seam when the catalog grows); multi-turn clarifying dialogue when the gate flags low signal; landing-page / product-feed grounding via tool-calling; online learning (contextual bandits, Thompson sampling for allocation and creative rotation — the schema anchors are already present); performance-driven optimization (dynamic reallocation, automated bidding tied to real outcomes); image creative; a shared controlled taxonomy normalizing persona and publisher vocabularies.
