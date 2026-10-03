import asyncio
import json

import httpx

from evals.schema import EvalQuestion
from evals.targets.chat_server import ChatServerTarget, citations_from_final

def question(**kw):
    base = dict(id="q1", question="How do drivers request time off?", question_type="simple",
                expected_source_ids=["zendesk:111"], reference_answer="Calendar tab.")
    return EvalQuestion.model_validate({**base, **kw})


def _sse(events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def test_chat_server_target_parses_stream():
    final = {
        "answer": "Use the Calendar tab [1]. Managers approve it [3].",
        "sources": [{"n": 1, "document_id": "zendesk:111", "chunk_id": "c1", "title": "Schedule", "url": "u"}],
        "evidence_status": "found",
        "conversation_id": "s",
        "debug": {
            "retrieved": [{"document_id": "zendesk:111", "chunk_id": "c1", "rank": 1, "score": 0.9}],
            "evidence": [{"n": 1, "document_id": "zendesk:111", "text": "Calendar tab"}],
            "tool_calls": ["search_knowledge_base"],
            "stage_ms": {"embedding": 80.0, "rerank": 200.0},
            "usage": {"input_tokens": 900, "output_tokens": 120, "cost_usd": 0.002},
        },
    }
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        stream = _sse([("token", {"text": "Use"}), ("token", {"text": " the"}), ("final", final)])
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = asyncio.run(ChatServerTarget("http://server", "/v1/chat/stream", "tok", 10, client=client).answer(question(), "s"))
    assert r.error is None
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"message": "How do drivers request time off?", "conversation_id": "s"}
    assert r.ttft_ms is not None and r.ttft_ms <= r.total_ms
    assert r.tool_calls == ["search_knowledge_base"] and r.usage.cost_usd == 0.002
    # [3] is not a provided source -> invalid citation.
    assert [(c.n, c.valid) for c in r.citations] == [(1, True), (3, False)]


def test_chat_server_error_event():
    stream = _sse([("error", {"message": "boom"})])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=stream)))
    r = asyncio.run(ChatServerTarget("http://server", "/x", None, 10, client=client).answer(question(), "s"))
    assert r.error == "RuntimeError: boom"


def test_citations_from_final_dedupes_markers():
    cits = citations_from_final("a [2] b [2] c [1]", [{"n": 1, "document_id": "zendesk:1"}])
    assert [(c.n, c.valid) for c in cits] == [(1, True), (2, False)]
