"""Stage 5.5 — creative critique pass. STUB.

Interface defined now so wiring the real pass later is filling a stub, not
restructuring the pipeline. The real version is a single batched LLM call
(temp 0) over all variants that flags cross-variant repetition, persona-fit
drift, and unsupported/non-compliant claims.
"""

from adtech.schemas import AdvertiserProfile, CreativeVariant


async def critique_variants(
    profile: AdvertiserProfile, variants: list[CreativeVariant]
) -> dict[str, list[str]]:
    """Return {persona_id: [critique flags]} for each variant.

    TODO: batched LLM call via call_llm("critique", ...). No-op for v1.
    """
    del profile
    return {v.persona_id: [] for v in variants}
