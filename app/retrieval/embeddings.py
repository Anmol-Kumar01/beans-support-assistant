"""Dense embeddings from any OpenAI-compatible ``/embeddings`` endpoint (default: Gemini
gemini-embedding-001 at 1536 dims).

Texts are sent in batches of ``EMBEDDING_BATCH_SIZE`` under the shared retry/pacing policy,
since the Google free tier is shared with the judge. Vectors are L2-normalized (Gemini only
normalizes its full-size output). Every vector is stored with ``identity()``.
Keyword search stays in PostgreSQL full-text search; there are no sparse vectors.
"""

import asyncio
import math
from collections.abc import Sequence

from app.core.config import EmbeddingSettings
from app.llm.client import openai_compatible_client
from app.llm.retry import Pacer, ProviderError, call_with_retries, pacer_for

ENV_PREFIX = "EMBEDDING_"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class Embedder:
    def __init__(self, settings: EmbeddingSettings, http_client=None, sleep=asyncio.sleep, pacer: Pacer | None = None):
        self.settings = settings
        self._client = openai_compatible_client(settings, ENV_PREFIX, http_client)
        self._sleep = sleep
        self._pacer = pacer or pacer_for(settings)
        self.reported_model: str | None = None  # model name the API returned last

    def identity(self) -> dict:
        """Columns stored with every vector in chunk_embeddings."""
        s = self.settings
        return {
            "embedding_config": s.config_name,
            "embedding_model": s.model,
            # Hosted models are versioned by name; keep what the API reported, if anything.
            "embedding_version": self.reported_model or s.model,
            "dimensions": s.dim,
        }

    async def aclose(self) -> None:
        await self._client.close()

    async def _batch(self, texts: list[str]) -> list[list[float]]:
        s = self.settings
        kwargs = {"model": s.model, "input": texts, "encoding_format": "float"}
        if s.send_dimensions:
            kwargs["dimensions"] = s.dim
        resp, _ = await call_with_retries(
            lambda: self._client.embeddings.create(**kwargs),
            s,
            where=f"embedding model {s.model} at {s.base_url}",
            key_env=f"{ENV_PREFIX}API_KEY",
            pacer=self._pacer,
            sleep=self._sleep,
        )
        self.reported_model = getattr(resp, "model", None) or self.reported_model
        rows = list(resp.data)
        # Gemini's OpenAI-compatible endpoint returns index=None in batches (in input order).
        if all(isinstance(r.index, int) for r in rows):
            rows.sort(key=lambda r: r.index)
        if len(rows) != len(texts):
            raise ProviderError(f"{s.model} returned {len(rows)} vectors for {len(texts)} texts")
        vectors = []
        for row in rows:
            vec = [float(x) for x in row.embedding]
            if len(vec) != s.dim:
                raise ProviderError(f"{s.model} returned {len(vec)} dims but EMBEDDING_DIM={s.dim}")
            vectors.append(_normalize(vec) if s.normalize else vec)
        return vectors

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        size = max(1, self.settings.batch_size)
        out: list[list[float]] = []
        for i in range(0, len(texts), size):
            out += await self._batch(list(texts[i : i + size]))
        return out
