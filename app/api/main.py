"""FastAPI app: serves the React UI (frontend/dist), the streaming chat endpoint,
the knowledge-base source list, and feedback.

Run with ``uvicorn app.api.main:create_app --factory --port 8001`` after
``npm run build`` in frontend/ (or use ``npm run dev`` there, which proxies /v1 here).

``POST /v1/chat/stream`` implements the SSE contract in ``evals/targets/new_bot.py``, so
the eval runner can score this server with ``--target new`` as well. Auth and tenant
context (Section 15) are not in place yet; this server is for local and internal use only.
"""

import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.config import AppSettings, get_settings
from app.hub import find_postman_collection, load_hub
from app.legacy_proxy import BackendError, LegacyBackend
from evals.catalog import Catalog

log = logging.getLogger("app.api")
WEB_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ChatBackend(Protocol):
    """RagBackend streams (``stream_answer``); LegacyBackend answers in one piece (``ask``)."""

    def describe(self) -> dict: ...
    async def aclose(self) -> None: ...


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = Field(default=None, max_length=100)


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal[-1, 1]
    reason: Literal["wrong", "outdated", "missing_source", "not_helpful"] | None = None
    comment: str | None = Field(default=None, max_length=2000)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: AppSettings | None = None, backend: ChatBackend | None = None) -> FastAPI:
    settings = settings or get_settings()
    if backend is None and settings.chat_backend == "rag":
        from app.core.config import get_model_settings
        from app.rag.backend import RagBackend

        backend = RagBackend(settings, get_model_settings())
    if backend is None:
        backend = LegacyBackend(
            base_url=settings.legacy_base_url,
            catalog=Catalog.from_legacy_dir(settings.legacy_sources_dir),
            timeout_s=settings.legacy_timeout_s,
            not_found_phrases=settings.not_found_phrases,
            excerpt_chars=settings.source_excerpt_chars,
        )
    recent: OrderedDict[str, dict] = OrderedDict()
    hub = load_hub(settings.data_sources_dir, settings.hub_include_account_tutorials)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.startup_check:
            from app.core.config import get_model_settings
            from app.core.startup_check import format_results, require_models

            # Raises StartupCheckError, so the server refuses to start with a clear message.
            log.info("model startup check:\n%s", format_results(await require_models(get_model_settings())))
        if hasattr(backend, "open"):
            try:
                await backend.open()
            except BackendError as exc:  # keep the UI and Explore pages up; chat explains the fix
                log.error("answer pipeline not ready: %s", exc)
                backend.startup_error = str(exc)
        yield
        await backend.aclose()

    app = FastAPI(title="Beans In & Out Bot", lifespan=lifespan)
    if (WEB_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        if not (WEB_DIR / "index.html").is_file():
            return PlainTextResponse("UI not built. Run `npm install && npm run build` in frontend/.", 503)
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/favicon.png", include_in_schema=False)
    async def favicon():
        return FileResponse(WEB_DIR / "favicon.png")

    @app.get("/v1/health")
    async def health() -> dict:
        return {"status": "ok", "user_name": settings.user_name, **backend.describe()}

    @app.get("/v1/sources")
    async def sources(type: Literal["zendesk", "youtube", "release_note", "trainn", "api_reference", "beans_content"] | None = None) -> list[dict]:
        result = backend.sources(type)
        return await result if hasattr(result, "__await__") else result

    @app.get("/v1/documents/{document_id:path}")
    async def document(document_id: str) -> dict:
        doc = await backend.document(document_id) if hasattr(backend, "document") else None
        if doc is None:
            raise HTTPException(404, "Unknown document.")
        return doc

    @app.get("/v1/hub/content")
    async def hub_content() -> dict:
        return hub["content"]

    @app.get("/v1/hub/api")
    async def hub_api() -> dict:
        if hub["api"] is None:
            raise HTTPException(404, "No Postman collection in data_sources/.")
        return hub["api"]

    @app.get("/v1/hub/api/collection.json", include_in_schema=False)
    async def hub_api_download():
        path = find_postman_collection(settings.data_sources_dir)
        if path is None:
            raise HTTPException(404, "No Postman collection in data_sources/.")
        return FileResponse(path, filename="Beans Route API Collection.postman_collection.json")

    @app.get("/v1/hub/tutorials")
    async def hub_tutorials() -> dict:
        return hub["tutorials"]

    @app.get("/v1/hub/articles")
    async def hub_articles() -> dict:
        return hub["articles"]

    @app.get("/v1/hub/release-notes")
    async def hub_release_notes() -> list[dict]:
        return hub["release_notes"]

    @app.post("/v1/chat/stream")
    async def chat(req: ChatRequest) -> StreamingResponse:
        message = req.message.strip()
        if not message:
            raise HTTPException(422, "Message is empty.")
        if len(message) > settings.max_message_chars:
            raise HTTPException(
                422, f"Message is too long (max {settings.max_message_chars} characters)."
            )
        conversation_id = req.conversation_id or str(uuid.uuid4())

        async def events() -> AsyncIterator[str]:
            start = time.perf_counter()
            final = None
            try:
                if hasattr(backend, "stream_answer"):
                    async for event, data in backend.stream_answer(message, conversation_id):
                        if event == "final":
                            final = data
                        else:
                            yield _sse(event, data)
                else:  # legacy proxy: the whole answer arrives as one token event
                    result = await backend.ask(message, conversation_id)
                    final = {
                        "answer": result.answer,
                        "sources": result.sources,
                        "evidence_status": result.evidence_status,
                        "conversation_id": conversation_id,
                        "message_id": str(uuid.uuid4()),
                    }
                    yield _sse("token", {"text": result.answer})
            except BackendError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            except Exception:
                log.exception("chat failed")
                yield _sse("error", {"message": "Something went wrong while answering. Please try again."})
                return
            recent[final["message_id"]] = {
                "question": message,
                "latency_ms": round((time.perf_counter() - start) * 1000),
                **{k: v for k, v in final.items() if k != "debug"},
            }
            while len(recent) > settings.recent_messages_kept:
                recent.popitem(last=False)
            yield _sse("final", final)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/feedback", status_code=204)
    async def feedback(req: FeedbackRequest) -> None:
        message = recent.get(req.message_id)
        if message is None:
            raise HTTPException(404, "Unknown or expired message.")
        record = {
            "created_at": datetime.now(UTC).isoformat(),
            "backend": backend.describe().get("backend"),
            **req.model_dump(),
            "message": message,
        }
        settings.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.feedback_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return app
