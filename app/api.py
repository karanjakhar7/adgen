"""FastAPI — a thin adapter over run_pipeline(). No pipeline logic lives here.

POST /api/campaigns streams SSE: one event per started/completed pipeline
stage (via the pipeline's on_event hook), then a terminal "done" or "error"
carrying the full result. Streaming on the request itself keeps the adapter
serverless-safe: no cross-request state, which is what Vercel functions need.
Local runs also persist the final result to runs/<trace_id>/result.json, the
same artifact path used by the CLI.

Local dev:  uv run fastapi dev app/api.py
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from adtech.artifacts import write_result
from adtech.logging import configure_logging
from adtech.pipeline import run_pipeline
from adtech.schemas import OffTopicResult

configure_logging(default_level="INFO")
logger = logging.getLogger("adtech")

# Vercel's filesystem is read-only (except /tmp) — skip run artifact writes
# there; the stream itself carries the full result and stage trail.
ON_VERCEL = bool(os.getenv("VERCEL"))

app = FastAPI(title="adgen", description="Ad placement & creative generation POC")


class CampaignRequest(BaseModel):
    brief: str
    budget_usd: int | None = None


def _to_jsonable(obj: object) -> str:
    return json.dumps(obj, default=lambda o: o.model_dump(mode="json") if isinstance(o, BaseModel) else str(o))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/campaigns")
async def create_campaign(req: CampaignRequest) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(key: str, payload: object) -> None:
        if key == "stage_start":
            queue.put_nowait({"event": "stage_start", "stage": payload})
            return

        # The terminal "done" line carries the full result; skip the
        # duplicate stage payloads for the terminal keys.
        if key not in ("draft", "off_topic"):
            queue.put_nowait({"event": "stage", "stage": key, "data": payload})

    async def runner() -> None:
        try:
            result = await run_pipeline(
                req.brief, total_budget_usd=req.budget_usd, trace=not ON_VERCEL, on_event=on_event
            )
            if not ON_VERCEL:
                write_result(result)
            result_type = "off_topic" if isinstance(result, OffTopicResult) else "draft"
            queue.put_nowait({"event": "done", "result_type": result_type, "data": result})
        except Exception as err:
            logger.exception("pipeline failed")
            queue.put_nowait({"event": "error", "message": str(err)})
        queue.put_nowait(None)  # end-of-stream sentinel

    async def stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {_to_jsonable(item)}\n\n"
                await asyncio.sleep(0)
            await task
        finally:
            task.cancel()  # no-op if already finished; stops work on client disconnect

    # SSE framing, not NDJSON: Vercel (and most proxies) only disable response
    # buffering for text/event-stream — anything else arrives all at once.
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# Serve the frontend (app/templates/index.html) — mounted last so it doesn't
# shadow the /api routes. Works identically locally and on Vercel.
_templates = Path(__file__).resolve().parent / "templates"
if _templates.is_dir():
    app.mount("/", StaticFiles(directory=_templates, html=True), name="frontend")
