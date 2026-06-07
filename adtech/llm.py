"""LiteLLM wrapper: per-stage routing, structured output, validate-and-repair loop.

The reliability guarantee for every LLM stage lives here: the model is
constrained to emit JSON via response_format, the output is validated against
the stage's Pydantic schema, and on failure the validation error is fed back
for up to MAX_REPAIR_ATTEMPTS retries before the stage fails. Downstream
stages never see malformed data.
"""

import logging
from string import Template
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from adtech.config import LLM_TIMEOUT_SECONDS, MAX_REPAIR_ATTEMPTS, PROMPTS_DIR, STAGE_CONFIG

logger = logging.getLogger("adtech")

T = TypeVar("T", bound=BaseModel)


def render_prompt(name: str, **slots: str) -> str:
    """Load a prompt template and fill its $placeholders.

    string.Template, not str.format — the templates embed literal JSON
    examples whose braces would break format().
    """
    template = Template((PROMPTS_DIR / f"{name}.txt").read_text())
    return template.substitute(**slots)


async def call_llm(stage: str, prompt: str, response_model: type[T]) -> T:
    """Call the stage's configured model and return a validated instance."""
    model, temperature = STAGE_CONFIG[stage]
    messages = [{"role": "user", "content": prompt}]

    last_error: Exception | None = None
    for attempt in range(1 + MAX_REPAIR_ATTEMPTS):
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=LLM_TIMEOUT_SECONDS,
            num_retries=3,  # transient provider errors (429/5xx) with backoff
            response_format=response_model,
        )
        raw = response.choices[0].message.content or ""
        try:
            return response_model.model_validate_json(raw)
        except ValidationError as err:
            last_error = err
            logger.warning("stage=%s attempt=%d validation failed: %s", stage, attempt + 1, err)
            # Repair loop: show the model its own output and the error.
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That response failed validation:\n{err}\n\n"
                        "Return ONLY the corrected JSON object. No markdown, no prose."
                    ),
                },
            ]

    raise RuntimeError(f"stage '{stage}' failed validation after {1 + MAX_REPAIR_ATTEMPTS} attempts") from last_error
