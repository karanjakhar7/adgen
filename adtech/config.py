"""Stage → (model, temperature) routing and pipeline thresholds.

Model routing is config, not code. LiteLLM infers the provider API key from
the model name string (e.g. "gemini/..." reads GEMINI_API_KEY).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RUNS_DIR = REPO_ROOT / "runs"

FAST = os.getenv("LLM_MODEL_FAST", "gemini/gemini-3.1-flash-lite")
STRONG = os.getenv("LLM_MODEL_STRONG", "gemini/gemini-3.1-flash-lite")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

# stage name → (model string, temperature)
STAGE_CONFIG: dict[str, tuple[str, float]] = {
    "interpret": (FAST, 0.0),
    "rank": (STRONG, 0.0),
    "personas": (FAST, 0.3),
    "creative": (STRONG, 0.8),
    "critique": (STRONG, 0.0),
}

# Ranking → recommendation split (threshold, not top-K; zero recs is allowed)
SCORE_THRESHOLD = 0.5
MAX_RECOMMENDED = 6  # soft cap

# Budget allocation
EXPLORATION_FLOOR = 0.05  # min share for any recommended publisher
MAX_SINGLE_PUBLISHER_SHARE = 0.60  # one giant publisher can't take everything
SUGGESTED_DAILY_RANGE_USD = (50, 150)

# LLM repair loop
MAX_REPAIR_ATTEMPTS = 2
