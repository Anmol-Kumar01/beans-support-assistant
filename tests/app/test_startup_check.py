import asyncio
import json

import httpx
import httpx2
import pytest

from app.core.config import ModelSettings
from app.core.startup_check import StartupCheckError, format_results, require_models, run_startup_checks


def models(monkeypatch, **env) -> ModelSettings:
    base = {
        "LLM_ANSWER_API_KEY": "a", "LLM_SMALL_API_KEY": "a", "LLM_JUDGE_API_KEY": "g",
        "EMBEDDING_API_KEY": "g", "RERANKER_API_KEY": "r",
        "LLM_ANSWER_BASE_URL": "https://groq.test/v1", "LLM_SMALL_BASE_URL": "https://groq.test/v1",
        "LLM_JUDGE_BASE_URL": "https://gemini.test/v1", "EMBEDDING_BASE_URL": "https://gemini.test/v1",
        "EMBEDDING_DIM": "4",
    }
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    return ModelSettings.from_env(env_file=None)


def llm_http(listed: dict[str, list[str]], chat_status: int = 200, dims: int = 4, fail_host: str | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        if host == fail_host:
            raise httpx2.ConnectError("refused")
        if request.url.path.endswith("/models"):
            data = [{"id": i, "object": "model", "created": 0, "owned_by": "x"} for i in listed.get(host, [])]
            return httpx2.Response(200, json={"object": "list", "data": data})
        if request.url.path.endswith("/embeddings"):
            n = len(json.loads(request.content)["input"])
            data = [{"object": "embedding", "index": i, "embedding": [1.0] * dims} for i in range(n)]
            return httpx2.Response(200, json={"object": "list", "data": data, "model": "e"})
        if chat_status != 200:
            return httpx2.Response(chat_status, json={"error": {"message": "model retired"}})
        return httpx2.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
        })
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def rerank_http(scores=(0.9, 0.1)):
    def handler(request):
        return httpx.Response(200, json={"results": [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


LISTED = {"groq.test": ["openai/gpt-oss-20b"], "gemini.test": ["models/gemini-3.5-flash", "models/gemini-3.5-flash-lite"]}


def check(m, http, rerank=None, **kw):
    return asyncio.run(run_startup_checks(m, http_client=http, rerank_http_client=rerank or rerank_http(), **kw))


def test_all_checks_pass(monkeypatch):
    results = check(models(monkeypatch), llm_http(LISTED))
    assert [r.name for r in results] == ["llm:answer", "llm:small", "llm:judge", "embedding", "reranker"]
    assert all(r.ok and not r.warning for r in results), format_results(results)
    assert "All model checks passed" in format_results(results)


def test_missing_key_and_unknown_model_are_named(monkeypatch):
    m = models(monkeypatch, LLM_SMALL_API_KEY="", LLM_JUDGE_MODEL="gemini-3.5-flsh")
    m.small.api_key = None
    by = {r.name: r for r in check(m, llm_http(LISTED))}
    assert not by["llm:small"].ok and "LLM_SMALL_API_KEY is not set" in by["llm:small"].detail
    judge = by["llm:judge"]
    assert not judge.ok and "LLM_JUDGE_MODEL" in judge.detail and "gemini-3.5-flash" in judge.detail  # close match
    assert by["llm:answer"].ok


def test_listed_but_refused_model_fails_the_probe(monkeypatch):
    by = {r.name: r for r in check(models(monkeypatch), llm_http(LISTED, chat_status=404), retrieval=False)}
    assert not by["llm:judge"].ok and "model retired" in by["llm:judge"].detail
    no_probe = check(models(monkeypatch), llm_http(LISTED, chat_status=404), retrieval=False, probe=False)
    assert all(r.ok for r in no_probe)


def test_unreachable_endpoint(monkeypatch):
    by = {r.name: r for r in check(models(monkeypatch), llm_http(LISTED, fail_host="groq.test"), retrieval=False)}
    assert not by["llm:answer"].ok and "can't reach https://groq.test/v1" in by["llm:answer"].detail


def test_embedding_dimension_mismatch_and_bad_reranker(monkeypatch):
    by = {r.name: r for r in check(models(monkeypatch), llm_http(LISTED, dims=8), rerank_http((0.1, 0.9)), roles=())}
    assert not by["embedding"].ok and "EMBEDDING_DIM=4" in by["embedding"].detail
    assert not by["reranker"].ok and "irrelevant passage higher" in by["reranker"].detail


def test_self_grading_warning(monkeypatch):
    m = models(monkeypatch, LLM_JUDGE_BASE_URL="https://groq.test/v1", LLM_JUDGE_MODEL="openai/gpt-oss-20b")
    warning = [r for r in check(m, llm_http(LISTED), retrieval=False) if r.warning]
    assert len(warning) == 1 and "self-grading" in warning[0].detail and warning[0].ok


def test_require_models_raises_with_every_failure(monkeypatch):
    m = models(monkeypatch)
    m.answer.api_key = None
    with pytest.raises(StartupCheckError) as err:
        asyncio.run(require_models(m, http_client=llm_http(LISTED), rerank_http_client=rerank_http()))
    assert "LLM_ANSWER_API_KEY is not set" in str(err.value) and "1 check(s) failed" in str(err.value)


def test_cli_exit_code(monkeypatch, capsys):
    from app.core import startup_check

    monkeypatch.setattr(startup_check, "get_model_settings", lambda: ModelSettings.from_env(env_file=None))
    for var in ("LLM_ANSWER_API_KEY", "LLM_SMALL_API_KEY", "LLM_JUDGE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert startup_check.main(["--skip-retrieval"]) == 1
    assert "LLM_JUDGE_API_KEY is not set" in capsys.readouterr().out
