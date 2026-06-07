"""Catalog loading + candidate retrieval.

`retrieve_candidates()` is the scalability seam: today it returns the whole
catalog (~20 publishers fit in context). When the catalog grows to thousands,
its body becomes metadata filters + vector search returning the top ~30 — and
the ranking prompt downstream never changes.
"""

import json
from functools import lru_cache

from adtech.config import DATA_DIR
from adtech.schemas import AdvertiserProfile, Persona, Publisher


@lru_cache(maxsize=1)
def load_publishers() -> list[Publisher]:
    raw = json.loads((DATA_DIR / "publishers.json").read_text())
    return [Publisher.model_validate(p) for p in raw]


@lru_cache(maxsize=1)
def load_personas() -> list[Persona]:
    raw = json.loads((DATA_DIR / "shopper_personas.json").read_text())
    return [Persona.model_validate(p) for p in raw]


def retrieve_candidates(profile: AdvertiserProfile) -> list[Publisher]:
    """Return candidate publishers for ranking. POC: the full catalog."""
    del profile  # unused today; the future vector-store swap point
    return load_publishers()
