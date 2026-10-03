"""Hosted reranking: Jina (default, jina-reranker-v2-base-multilingual) or Cohere
(rerank-v3.5). Both take ``POST {base_url}/rerank`` with model, query, documents, and
top_n, and return ``results: [{index, relevance_score}]``.

Only the first ``RERANKER_CANDIDATES`` fused candidates are sent (default 20). Scores are on
the provider's scale, so the not-found threshold (Section 13) is calibrated per model.
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import TypeVar

import httpx

from app.core.config import RerankerSettings
from app.llm.retry import Pacer, ProviderConfigError, ProviderError, call_with_retries, pacer_for

T = TypeVar("T")
ENV_PREFIX = "RERANKER_"


class Reranker:
    def __init__(
        self, settings: RerankerSettings, http_client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep, pacer: Pacer | None = None,
    ):
        if settings.api_key is None and not settings.is_local:
            raise ProviderConfigError(
                f"{ENV_PREFIX}API_KEY is not set (needed for {settings.base_url}). "
                "See .env.example for free-tier presets."
            )
        self.settings = settings
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"
        self._client = http_client or httpx.AsyncClient(timeout=settings.timeout_s)
        self._headers = headers
        self._sleep = sleep
        self._pacer = pacer or pacer_for(settings)

    def identity(self) -> dict:
        return {"reranker_provider": self.settings.provider, "reranker_model": self.settings.model}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Relevance score per document, in input order."""
        if not documents:
            return []
        s = self.settings
        body = {
            "model": s.model,
            "query": query,
            "documents": [d[: s.max_chars_per_document] for d in documents],
            "top_n": len(documents),
        }
        if s.provider == "jina":
            body["return_documents"] = False

        url = s.base_url.rstrip("/")
        url = url if url.endswith("/rerank") else f"{url}/rerank"  # accept either form

        async def post() -> dict:
            resp = await self._client.post(url, json=body, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

        data, _ = await call_with_retries(
            post, s,
            where=f"{s.provider} reranker {s.model} at {s.base_url}",
            key_env=f"{ENV_PREFIX}API_KEY",
            pacer=self._pacer,
            sleep=self._sleep,
        )
        scores: list[float | None] = [None] * len(documents)
        for r in data.get("results", []):
            scores[r["index"]] = float(r["relevance_score"])
        if any(v is None for v in scores):
            raise ProviderError(f"{s.model} returned scores for {sum(v is not None for v in scores)} of {len(documents)} documents")
        return scores  # type: ignore[return-value]

    async def rerank(self, query: str, candidates: Sequence[T], text: Callable[[T], str]) -> list[tuple[T, float]]:
        """Score the top ``candidates`` items (in fused order) and return them best first."""
        top = list(candidates[: self.settings.candidates])
        scored = zip(top, await self.score(query, [text(c) for c in top]), strict=True)
        return sorted(scored, key=lambda pair: pair[1], reverse=True)
