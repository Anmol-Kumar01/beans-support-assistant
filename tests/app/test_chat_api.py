import asyncio
import json
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import AppSettings
from app.rag.backend import BackendError
from evals.schema import EvalQuestion
from evals.targets.chat_server import ChatServerTarget

ARTICLE = "https://beansai.zendesk.com/hc/en-us/articles/111-Drivers-Work-Schedule"
SOURCE = {"n": 1, "document_id": "zendesk:111", "chunk_id": "c1", "title": "Drivers: Work Schedule",
          "section": None, "url": ARTICLE, "source_type": "zendesk", "excerpt": "Drivers request time off."}


class FakeBackend:
    """Stands in for RagBackend: each reply is an answer string or a BackendError to raise."""

    def __init__(self, *replies):
        self.replies = iter(replies)
        self.seen: list[tuple[str, str]] = []

    def describe(self) -> dict:
        return {"backend": "fake", "sources": {"zendesk": 1}}

    async def aclose(self) -> None:
        pass

    def sources(self, source_type: str | None = None) -> list[dict]:
        return [s for s in [SOURCE] if source_type in (None, s["source_type"])]

    async def stream_answer(self, message: str, conversation_id: str):
        self.seen.append((message, conversation_id))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        yield "token", {"text": reply}
        cited = "[1]" in reply
        yield "final", {
            "answer": reply,
            "sources": [SOURCE] if cited else [],
            "evidence_status": "found" if cited else None,
            "conversation_id": conversation_id,
            "message_id": str(uuid.uuid4()),
        }


def _app(tmp_path: Path, backend: FakeBackend) -> tuple:
    settings = AppSettings(_env_file=None, feedback_path=tmp_path / "var" / "feedback.jsonl")
    return create_app(settings, backend), settings


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_streams_token_then_final(tmp_path):
    backend = FakeBackend("Use the Calendar tab. [1]")
    app, _ = _app(tmp_path, backend)
    with TestClient(app) as client:
        resp = client.post("/v1/chat/stream", json={"message": "time off?", "conversation_id": "c1"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _events(resp.text)
    assert [e for e, _ in events] == ["token", "final"]
    final = events[1][1]
    assert final["answer"] == "Use the Calendar tab. [1]"
    assert final["evidence_status"] == "found"
    assert final["conversation_id"] == "c1"
    assert final["sources"][0]["url"] == ARTICLE
    assert backend.seen == [("time off?", "c1")]


def test_backend_errors_and_long_messages(tmp_path):
    app, _ = _app(tmp_path, FakeBackend(BackendError("Search is unavailable: down"), RuntimeError("boom")))
    with TestClient(app) as client:
        readable = _events(client.post("/v1/chat/stream", json={"message": "x"}).text)
        unexpected = _events(client.post("/v1/chat/stream", json={"message": "x"}).text)
        too_long = client.post("/v1/chat/stream", json={"message": "x" * 2001})
    assert readable == [("error", {"message": "Search is unavailable: down"})]
    assert unexpected[0][0] == "error" and "Something went wrong" in unexpected[0][1]["message"]
    assert too_long.status_code == 422


def test_feedback_is_stored_with_the_answer(tmp_path):
    app, settings = _app(tmp_path, FakeBackend("Hello!"))
    with TestClient(app) as client:
        assert client.post("/v1/feedback", json={"message_id": "nope", "rating": 1}).status_code == 404
        final = _events(client.post("/v1/chat/stream", json={"message": "hi"}).text)[-1][1]
        assert final["evidence_status"] is None
        resp = client.post(
            "/v1/feedback",
            json={"message_id": final["message_id"], "rating": -1, "reason": "not_helpful"},
        )
        assert resp.status_code == 204
        assert client.post("/v1/feedback", json={"message_id": final["message_id"], "rating": 0}).status_code == 422
    record = json.loads(settings.feedback_path.read_text().strip())
    assert record["rating"] == -1 and record["reason"] == "not_helpful"
    assert record["backend"] == "fake"
    assert record["message"]["question"] == "hi"
    assert record["message"]["answer"] == "Hello!"


def test_health_and_sources(tmp_path):
    app, _ = _app(tmp_path, FakeBackend())
    with TestClient(app) as client:
        health = client.get("/v1/health").json()
        articles = client.get("/v1/sources", params={"type": "zendesk"}).json()
        videos = client.get("/v1/sources", params={"type": "youtube"}).json()
    assert health["backend"] == "fake" and health["user_name"] == "Guest"
    assert [a["document_id"] for a in articles] == ["zendesk:111"]
    assert videos == []


def test_eval_target_can_score_this_server(tmp_path):
    """The chat endpoint follows the SSE contract the eval runner expects."""
    app, _ = _app(tmp_path, FakeBackend("Use the Calendar tab. [1]"))

    async def run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        target = ChatServerTarget("http://test", "/v1/chat/stream", None, 5, client=client)
        question = EvalQuestion.model_validate(
            {"id": "q1", "question": "time off?", "question_type": "simple", "origin": "synthetic", "labelled": False}
        )
        try:
            return await target.answer(question, "s1")
        finally:
            await target.aclose()

    result = asyncio.run(run())
    assert result.error is None
    assert result.evidence_status == "found"
    assert [c.document_ids for c in result.citations] == [["zendesk:111"]]
    assert all(c.valid for c in result.citations)
