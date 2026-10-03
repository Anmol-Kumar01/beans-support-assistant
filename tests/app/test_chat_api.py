import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import AppSettings
from app.legacy_proxy import LegacyBackend, number_citations
from evals.schema import EvalQuestion
from evals.targets.new_bot import NewBotTarget

ARTICLE = "https://beansai.zendesk.com/hc/en-us/articles/111-Drivers-Work-Schedule"
VIDEO = "https://www.youtube.com/watch?v=vid1"


def test_source_links_become_numbered_markers(catalog):
    answer = (
        f"Open the Calendar tab. [Drivers: Work Schedule and Time Off Requests]({ARTICLE})\n"
        f"Gate codes are covered in [this video]({VIDEO}). "
        f"See [Drivers: Work Schedule and Time Off Requests]({ARTICLE}) "
        f"[Drivers: Work Schedule and Time Off Requests]({ARTICLE})\n"
        "Presets: [Creating and Managing Preset Filters]\n"
        "Sign in at [Beans Route](https://beansroute.ai/login)."
    )
    numbered, sources = number_citations(answer, catalog, excerpt_chars=40)

    assert numbered == (
        "Open the Calendar tab. [1]\n"
        "Gate codes are covered in this video [2]. See [1]\n"
        "Presets: [3]\n"
        "Sign in at [Beans Route](https://beansroute.ai/login)."
    )
    assert [(s["n"], s["document_id"]) for s in sources] == [
        (1, "zendesk:111"),
        (2, "youtube:vid1"),
        (3, "release_note:creating-preset-filters"),
    ]
    assert sources[0]["source_type"] == "zendesk"
    assert sources[0]["excerpt"].startswith("Drivers request time off")
    assert len(sources[0]["excerpt"]) <= 41


def test_unknown_source_link_is_left_alone(catalog):
    answer = "See [Other](https://beansai.zendesk.com/hc/en-us/articles/999-Other)."
    assert number_citations(answer, catalog, 40) == (answer, [])


def _app(tmp_path: Path, catalog, handler) -> tuple:
    settings = AppSettings(_env_file=None, feedback_path=tmp_path / "var" / "feedback.jsonl")
    backend = LegacyBackend(
        base_url="http://legacy",
        catalog=catalog,
        timeout_s=5,
        not_found_phrases=settings.not_found_phrases,
        excerpt_chars=settings.source_excerpt_chars,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return create_app(settings, backend), settings


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_streams_token_then_final_and_forwards_session(tmp_path, catalog):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, text=f"Use the Calendar tab. [Drivers: Work Schedule and Time Off Requests]({ARTICLE})")

    app, _ = _app(tmp_path, catalog, handler)
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
    assert seen == {"query": "time off?", "sessionID": "c1"}


def test_not_found_and_backend_errors(tmp_path, catalog):
    replies = iter([
        httpx.Response(200, text="I don't know how to answer the query. Could you please try to rephrase it?"),
        httpx.Response(500, text="Internal Server Error"),
    ])
    app, _ = _app(tmp_path, catalog, lambda r: next(replies))
    with TestClient(app) as client:
        first = _events(client.post("/v1/chat/stream", json={"message": "x"}).text)
        second = _events(client.post("/v1/chat/stream", json={"message": "x"}).text)
        too_long = client.post("/v1/chat/stream", json={"message": "x" * 2001})
    assert first[-1][1]["evidence_status"] == "not_found"
    assert second == [("error", {"message": "The current bot returned HTTP 500."})]
    assert too_long.status_code == 422


def test_unreachable_backend_gives_readable_error(tmp_path, catalog):
    def handler(request):
        raise httpx.ConnectError("refused")

    app, _ = _app(tmp_path, catalog, handler)
    with TestClient(app) as client:
        events = _events(client.post("/v1/chat/stream", json={"message": "x"}).text)
    assert events[0][0] == "error"
    assert "Can't reach the current bot" in events[0][1]["message"]


def test_feedback_is_stored_with_the_answer(tmp_path, catalog):
    app, settings = _app(tmp_path, catalog, lambda r: httpx.Response(200, text="Hello!"))
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
    assert record["message"]["question"] == "hi"
    assert record["message"]["answer"] == "Hello!"


def test_health_and_sources(tmp_path, catalog):
    app, _ = _app(tmp_path, catalog, lambda r: httpx.Response(200, text=""))
    with TestClient(app) as client:
        health = client.get("/v1/health").json()
        videos = client.get("/v1/sources", params={"type": "youtube"}).json()
    assert health["backend"] == "legacy_node" and health["user_name"] == "Guest"
    assert health["sources"]["trainn"] == 2
    assert [v["document_id"] for v in videos] == ["youtube:vid1"]


def test_eval_new_bot_target_can_score_this_server(tmp_path, catalog):
    """The chat endpoint follows the SSE contract the eval runner expects."""
    reply = f"Use the Calendar tab. [Drivers: Work Schedule and Time Off Requests]({ARTICLE})"
    app, _ = _app(tmp_path, catalog, lambda r: httpx.Response(200, text=reply))

    async def run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        target = NewBotTarget("http://test", "/v1/chat/stream", None, 5, client=client)
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
