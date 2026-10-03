import asyncio
import json

import httpx

from evals.schema import EvalQuestion
from evals.targets.legacy_node import LegacyNodeTarget, parse_legacy_citations
from evals.targets.new_bot import NewBotTarget, citations_from_final

LEGACY_ANSWER = (
    "1. Click the Hub.\n"
    "2. Open Calendar [Drivers: Work Schedule](https://beansai.zendesk.com/hc/en-us/articles/111-x).\n"
    "Watch [Gate codes](https://www.youtube.com/watch?v=vid1&t=61s) or see [Creating and Managing Preset Filters].\n"
    "Also [Old article](https://beansai.zendesk.com/hc/en-us/articles/999-gone) and "
    "[Beans Route](https://beansroute.ai). Repeat [Drivers: Work Schedule](https://beansai.zendesk.com/hc/en-us/articles/111-x)."
)


def question(**kw):
    base = dict(id="q1", question="How do drivers request time off?", question_type="simple",
                expected_source_ids=["zendesk:111"], reference_answer="Calendar tab.")
    return EvalQuestion.model_validate({**base, **kw})


def test_parse_legacy_citations(catalog):
    cits = parse_legacy_citations(LEGACY_ANSWER, catalog)
    assert [(c.document_ids, c.valid) for c in cits] == [
        (["zendesk:111"], True),
        (["youtube:vid1"], True),
        ([], False),  # zendesk link not in the knowledge base
        (["release_note:creating-preset-filters"], True),  # bare [Title]
    ]


def test_legacy_target_text_reply(catalog):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, text=LEGACY_ANSWER, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    target = LegacyNodeTarget("http://bot", catalog, 10, 50, client=client)
    q = question(question_type="follow_up", prior_turns=["hi"])
    r = asyncio.run(target.answer(q, "eval-run-q1"))

    assert [c["query"] for c in calls] == ["hi", "How do drivers request time off?"]
    assert {c["sessionID"] for c in calls} == {"eval-run-q1"}
    assert r.retrieved is None and r.ttft_ms == r.total_ms
    assert [e.document_id for e in r.evidence] == [
        "zendesk:111", "youtube:vid1", "release_note:creating-preset-filters"
    ]
    assert all(len(e.text) <= 50 for e in r.evidence)


def test_legacy_target_json_debug_reply(catalog):
    body = {"answer": "See [x](https://www.youtube.com/watch?v=vid1)", "retrieved": ["vid1 Part 2", "111", "nope"]}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    r = asyncio.run(LegacyNodeTarget("http://bot", catalog, 10, 100, client=client).answer(question(), "s"))
    assert [(i.document_id, i.rank) for i in r.retrieved] == [("youtube:vid1", 1), ("zendesk:111", 2)]


def test_legacy_target_http_error_is_captured(catalog):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(500)))
    r = asyncio.run(LegacyNodeTarget("http://bot", catalog, 10, 100, client=client).answer(question(), "s"))
    assert r.error and "HTTPStatusError" in r.error


def _sse(events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def test_new_bot_target_parses_stream():
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
    r = asyncio.run(NewBotTarget("http://new", "/v1/chat/stream", "tok", 10, client=client).answer(question(), "s"))
    assert r.error is None
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"message": "How do drivers request time off?", "conversation_id": "s"}
    assert r.ttft_ms is not None and r.ttft_ms <= r.total_ms
    assert r.tool_calls == ["search_knowledge_base"] and r.usage.cost_usd == 0.002
    # [3] is not a provided source -> invalid citation.
    assert [(c.n, c.valid) for c in r.citations] == [(1, True), (3, False)]


def test_new_bot_error_event():
    stream = _sse([("error", {"message": "boom"})])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=stream)))
    r = asyncio.run(NewBotTarget("http://new", "/x", None, 10, client=client).answer(question(), "s"))
    assert r.error == "RuntimeError: boom"


def test_citations_from_final_dedupes_markers():
    cits = citations_from_final("a [2] b [2] c [1]", [{"n": 1, "document_id": "zendesk:1"}])
    assert [(c.n, c.valid) for c in cits] == [(1, True), (2, False)]
