"""Helpers for writing run-scoped artifacts."""

from pathlib import Path

from adtech.config import RUNS_DIR
from adtech.schemas import CampaignDraft, OffTopicResult

PipelineResult = CampaignDraft | OffTopicResult


def result_path(trace_id: str) -> Path:
    """Return the canonical final-result path for a pipeline run."""
    return RUNS_DIR / trace_id / "result.json"


def write_result(result: PipelineResult) -> Path:
    """Write the final pipeline result to runs/<trace_id>/result.json."""
    out_path = result_path(str(result.trace_id))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2))
    return out_path
