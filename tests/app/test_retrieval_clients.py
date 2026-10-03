import asyncio
import json
import math

import httpx
import httpx2
import pytest

from app.core.config import EmbeddingSettings, RerankerSettings
from app.llm.retry import ProviderConfigError, ProviderError
from app.retrieval.embeddings import Embedder
from app.retrieval.reranker import Reranker


def embed_server(dim: int, bodies: list, replies: list | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if replies:
            return replies.pop(0)
        data = [{"object": "embedding", "index": i, "embedding": [3.0, 4.0] + [0.0] * (dim - 2)}
                for i in range(len(body["input"]))]
        return httpx2.Response(200, json={"object": "list", "data": data[::-1], "model": "emb-v1",
                                          "usage": {"prompt_tokens": 1, "total_tokens": 1}})
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def emb_settings(**kw):
    return EmbeddingSettings(_env_file=None, api_key="k", base_url="https://emb.test/v1", **kw)


async def _noop(_):
    pass


def test_embeddings_batch_normalize_and_identity():
    bodies: list = []
    s = emb_settings(dim=8, batch_size=2)
    e = Embedder(s, http_client=embed_server(8, bodies), sleep=_noop)
    vectors = asyncio.run(e.embed(["a", "b", "c"]))
    assert [len(b["input"]) for b in bodies] == [2, 1]  # batched
    assert bodies[0]["dimensions"] == 8 and bodies[0]["model"] == "gemini-embedding-001"
    assert len(vectors) == 3 and vectors[0][:2] == [0.6, 0.8]  # L2-normalized
    assert math.isclose(sum(x * x for x in vectors[0]), 1.0)
    assert e.identity() == {
        "embedding_config": "gemini-embedding-001-8",
        "embedding_model": "gemini-embedding-001",
        "embedding_version": "emb-v1",
        "dimensions": 8,
    }


def test_embeddings_reject_wrong_dimensions():
    e = Embedder(emb_settings(dim=16), http_client=embed_server(8, []), sleep=_noop)
    with pytest.raises(ProviderError, match="returned 8 dims but EMBEDDING_DIM=16"):
        asyncio.run(e.embed(["a"]))


def test_embeddings_retry_429_shared_policy():
    sleeps: list = []

    async def sleep(s):
        sleeps.append(s)

    replies = [httpx2.Response(429, headers={"retry-after": "2"}, json={})]
    e = Embedder(emb_settings(dim=4), http_client=embed_server(4, [], replies), sleep=sleep)
    assert len(asyncio.run(e.embed(["a"]))) == 1
    assert sleeps == [2.0]


def test_embeddings_need_a_key():
    with pytest.raises(ProviderConfigError, match="EMBEDDING_API_KEY is not set"):
        Embedder(EmbeddingSettings(_env_file=None))


def test_judge_and_embeddings_share_a_pacer_on_the_same_key():
    from app.core.config import JudgeLLMSettings
    from app.llm.retry import pacer_for

    judge = JudgeLLMSettings(_env_file=None, api_key="same", max_requests_per_minute=10)
    emb = EmbeddingSettings(_env_file=None, api_key="same", max_requests_per_minute=30)
    other = EmbeddingSettings(_env_file=None, api_key="different")
    assert pacer_for(judge) is pacer_for(emb)
    assert pacer_for(emb) is not pacer_for(other)
    pacer = pacer_for(emb)
    assert pacer.reserve(100.0) == 0 and pacer.reserve(100.0) == pytest.approx(6.0)  # lowest rpm wins


def rerank_client(bodies: list, reply=None):
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        if reply is not None:
            return reply
        n = len(json.loads(request.content)["documents"])
        results = [{"index": i, "relevance_score": 1.0 / (i + 1)} for i in range(n)][::-1]
        return httpx.Response(200, json={"results": results})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("provider, base_url, expected_url", [
    ("jina", "https://api.jina.ai/v1", "https://api.jina.ai/v1/rerank"),
    ("jina", "https://api.jina.ai/v1/rerank", "https://api.jina.ai/v1/rerank"),
    ("cohere", "https://api.cohere.com/v2", "https://api.cohere.com/v2/rerank"),
])
def test_rerank_request_per_provider(provider, base_url, expected_url):
    bodies: list = []
    s = RerankerSettings(_env_file=None, api_key="rk", provider=provider, base_url=base_url, model="rr")
    scores = asyncio.run(Reranker(s, http_client=rerank_client(bodies), sleep=_noop).score("q", ["a", "b", "c"]))
    url, auth, body = bodies[0]
    assert url == expected_url and auth == "Bearer rk"
    assert body["model"] == "rr" and body["top_n"] == 3
    assert ("return_documents" in body) == (provider == "jina")
    assert scores == [1.0, 0.5, pytest.approx(1 / 3)]  # back in input order


def test_rerank_limits_candidates_and_sorts():
    bodies: list = []
    s = RerankerSettings(_env_file=None, api_key="rk", candidates=2, max_chars_per_document=3)
    ranked = asyncio.run(Reranker(s, http_client=rerank_client(bodies), sleep=_noop).rerank(
        "q", ["first", "second", "third"], text=lambda c: c
    ))
    assert bodies[0][2]["documents"] == ["fir", "sec"]  # top 2, truncated
    assert [c for c, _ in ranked] == ["first", "second"]


def test_rerank_bad_key_and_missing_key():
    s = RerankerSettings(_env_file=None, api_key="bad")
    r = Reranker(s, http_client=rerank_client([], httpx.Response(401, json={})), sleep=_noop)
    with pytest.raises(ProviderConfigError, match="RERANKER_API_KEY"):
        asyncio.run(r.score("q", ["a"]))
    with pytest.raises(ProviderConfigError, match="RERANKER_API_KEY is not set"):
        Reranker(RerankerSettings(_env_file=None))
