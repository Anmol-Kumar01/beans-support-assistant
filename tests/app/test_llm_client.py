import asyncio
import json

import httpx2
import pytest
from pydantic import BaseModel

from app.core.config import AnswerLLMSettings, JudgeLLMSettings
from app.llm.client import LLMClient, LLMConfigError, LLMError, LLMRateLimitError, inline_refs


def completion(content: str, model: str = "served-model") -> dict:
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


class Server:
    """Scripted OpenAI-compatible endpoint: pops one reply per request, records bodies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, httpx2.Response) else httpx2.Response(200, json=reply)


def make(settings, server, sleeps=None):
    async def sleep(s):
        sleeps.append(s) if sleeps is not None else None

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return LLMClient("answer", settings, http_client=http, sleep=sleep)


def answer_settings(**kw):
    return AnswerLLMSettings(_env_file=None, api_key="k", base_url="https://llm.test/v1", model="m-1", **kw)


def run(coro):
    return asyncio.run(coro)


def test_request_carries_model_and_thinking_off():
    server = Server(completion("<think>hmm</think>Hello"))
    resp = run(make(answer_settings(), server).chat([{"role": "user", "content": "hi"}]))
    body = server.bodies[0]
    assert body["model"] == "m-1"
    assert body["reasoning_effort"] == "low"  # thinking off -> reasoning_effort_off
    assert body["max_completion_tokens"] == 1024
    assert resp.content == "Hello"  # <think> block stripped
    assert (resp.model, resp.input_tokens, resp.output_tokens) == ("served-model", 11, 7)


@pytest.mark.parametrize("control, thinking, check", [
    ("chat_template_kwargs", False, lambda b: b["chat_template_kwargs"] == {"enable_thinking": False}),
    ("prompt_switch", False, lambda b: b["messages"][0]["content"].endswith("/no_think")),
    ("prompt_switch", True, lambda b: b["messages"][0]["content"].endswith("/think")),
    ("reasoning_effort", True, lambda b: b["reasoning_effort"] == "medium"),
    ("none", False, lambda b: "reasoning_effort" not in b and "chat_template_kwargs" not in b),
])
def test_thinking_controls(control, thinking, check):
    server = Server(completion("ok"))
    s = answer_settings(thinking=thinking, thinking_control=control, extra_body={"service_tier": "auto"})
    run(make(s, server).chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]))
    assert check(server.bodies[0])
    assert server.bodies[0]["service_tier"] == "auto"


def test_429_is_retried_with_retry_after_then_succeeds():
    sleeps = []
    limited = httpx2.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}})
    server = Server(limited, httpx2.Response(503, json={}), completion("ok"))
    resp = run(make(answer_settings(backoff_base_s=1), server, sleeps).chat([{"role": "user", "content": "hi"}]))
    assert resp.content == "ok" and resp.attempts == 3
    assert sleeps[0] == 3.0  # honoured Retry-After
    assert 0.5 <= sleeps[1] <= 2.0  # backoff with jitter for the 503


def test_long_retry_after_fails_fast_as_quota():
    sleeps = []
    server = Server(httpx2.Response(429, headers={"retry-after": "3600"}, json={}))
    with pytest.raises(LLMRateLimitError, match="rate limited for 3600s"):
        run(make(answer_settings(), server, sleeps).chat([{"role": "user", "content": "hi"}]))
    assert sleeps == []


def test_rate_limit_retries_are_bounded():
    server = Server(*[httpx2.Response(429, json={}) for _ in range(3)])
    with pytest.raises(LLMRateLimitError, match="after 2 retries"):
        run(make(answer_settings(max_retries=2), server, []).chat([{"role": "user", "content": "hi"}]))
    assert len(server.bodies) == 3


def test_bad_key_is_not_retried():
    server = Server(httpx2.Response(401, json={"error": {"message": "invalid key"}}))
    with pytest.raises(LLMConfigError, match="LLM_ANSWER_API_KEY"):
        run(make(answer_settings(), server, []).chat([{"role": "user", "content": "hi"}]))
    assert len(server.bodies) == 1


def test_missing_key_for_hosted_endpoint_fails_at_construction():
    with pytest.raises(LLMConfigError, match="LLM_ANSWER_API_KEY is not set"):
        LLMClient("answer", AnswerLLMSettings(_env_file=None))
    local = AnswerLLMSettings(_env_file=None, base_url="http://localhost:11434/v1")
    assert LLMClient("answer", local).model == local.model  # Ollama needs no key


def test_pacing_spaces_requests():
    sleeps = []
    server = Server(completion("a"), completion("b"))
    client = make(answer_settings(max_requests_per_minute=30), server, sleeps)
    run(client.chat([{"role": "user", "content": "1"}]))
    run(client.chat([{"role": "user", "content": "2"}]))
    assert len(sleeps) == 1 and 1.5 < sleeps[0] <= 2.0  # 60 / 30 = 2 s apart


class Inner(BaseModel):
    ok: bool


class Outer(BaseModel):
    items: list[Inner]


def test_complete_json_schema_and_repair():
    server = Server(completion("not json"), completion('```json\n{"items": [{"ok": true}]}\n```'))
    parsed, resp = run(make(answer_settings(), server).complete_json(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}], Outer, name="outer"
    ))
    assert parsed == Outer(items=[Inner(ok=True)])
    fmt = server.bodies[0]["response_format"]
    assert fmt["type"] == "json_schema" and "$defs" not in json.dumps(fmt)  # refs inlined
    assert "invalid" in server.bodies[1]["messages"][-1]["content"]  # repair turn
    assert resp.input_tokens == 22  # summed over both attempts


def test_complete_json_gives_up_after_repair():
    server = Server(completion("nope"), completion("still nope"))
    with pytest.raises(LLMError, match="invalid outer JSON"):
        run(make(answer_settings(), server).complete_json([{"role": "system", "content": "s"}], Outer, name="outer"))


def test_prompt_json_mode_puts_schema_in_system_prompt():
    server = Server(completion('{"items": []}'))
    run(make(answer_settings(json_mode="prompt"), server).complete_json(
        [{"role": "user", "content": "u"}], Outer, name="outer"
    ))
    body = server.bodies[0]
    assert "response_format" not in body
    assert body["messages"][0]["role"] == "system" and "JSON Schema" in body["messages"][0]["content"]


def test_inline_refs():
    spec = inline_refs(Outer.model_json_schema())
    assert spec["properties"]["items"]["items"]["properties"]["ok"]["type"] == "boolean"


def test_judge_defaults_to_low_effort_thinking():
    s = JudgeLLMSettings(_env_file=None, api_key="k")
    server = Server(completion("ok"))
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    run(LLMClient("judge", s, http_client=http).chat([{"role": "user", "content": "x"}]))
    assert server.bodies[0]["reasoning_effort"] == "low"
    assert server.bodies[0]["temperature"] == 0.0
