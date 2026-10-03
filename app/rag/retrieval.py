"""Hybrid search over the chunk index (Architecture doc, Section 8).

  1. dense:   top RAG_DENSE_K chunks by cosine similarity (pgvector)
  2. lexical: top RAG_LEXICAL_K chunks by full-text rank (PostgreSQL, any query word)
     1 and 2 run in parallel
  3. fusion:  Reciprocal Rank Fusion (k = RAG_RRF_K)
  4. rerank:  top RERANKER_CANDIDATES fused chunks scored by the reranker
              (on reranker failure: keep the fused order and log it)
  5. evidence threshold: drop chunks scoring below RAG_MIN_RERANK_SCORE
  6. select:  best RAG_TOP_K chunks
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool

from app.core.config import AppSettings, ModelSettings
from app.llm.retry import ProviderError
from app.retrieval.embeddings import Embedder
from app.retrieval.reranker import Reranker

log = logging.getLogger("app.rag")

_COLUMNS = """c.chunk_id, c.document_id, c.ordinal, c.source_type, c.title, c.section_path,
              c.content, c.context_header, c.source_url, c.updated_at"""
_ACTIVE = "NOT c.is_deprecated AND d.status = 'active'"


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    ordinal: int
    source_type: str
    title: str
    section: str | None
    content: str
    header: str
    url: str | None
    updated_at: str | None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused: float = 0.0
    rerank: float | None = None


@dataclass
class SearchResult:
    query: str
    hits: list[Hit]                      # evidence passed to the LLM, best first
    candidates: list[Hit] = field(default_factory=list)  # everything fused (for debug/evals)
    stage_ms: dict[str, float] = field(default_factory=dict)
    reranked: bool = True


def _hit(row) -> Hit:
    return Hit(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
               row[9].date().isoformat() if row[9] else None)


class Retriever:
    def __init__(self, pool: AsyncConnectionPool, settings: AppSettings, models: ModelSettings):
        self.pool = pool
        self.s = settings
        self.config = models.embedding.config_name
        self.dim = int(models.embedding.dim)
        self.embedder = Embedder(models.embedding)
        self.reranker = Reranker(models.reranker)

    async def aclose(self) -> None:
        await self.embedder.aclose()
        await self.reranker.aclose()

    async def _dense(self, vector: list[float]) -> list[Hit]:
        vec = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
        sql = f"""
            SELECT {_COLUMNS}
            FROM chunk_embeddings e JOIN chunks c USING (chunk_id) JOIN source_documents d USING (document_id)
            WHERE e.embedding_config = %s AND {_ACTIVE}
            ORDER BY e.embedding::vector({self.dim}) <=> %s::vector({self.dim})
            LIMIT %s"""
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (self.config, vec, self.s.rag_dense_k))).fetchall()
        return [_hit(r) for r in rows]

    async def _lexical(self, query: str) -> list[Hit]:
        # Any query word may match (OR), ranked by ts_rank_cd; plainto_tsquery alone requires all words.
        sql = f"""
            WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', %s)::text, ' & ', ' | ')) AS q)
            SELECT {_COLUMNS}
            FROM chunks c JOIN source_documents d USING (document_id), q
            WHERE c.search_tsv @@ q.q AND {_ACTIVE}
            ORDER BY ts_rank_cd(c.search_tsv, q.q) DESC
            LIMIT %s"""
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (query, self.s.rag_lexical_k))).fetchall()
        return [_hit(r) for r in rows]

    async def search(self, query: str) -> SearchResult:
        stage: dict[str, float] = {}
        t = time.perf_counter()
        [vector] = await self.embedder.embed([query])
        stage["query_embedding"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        dense, lexical = await asyncio.gather(self._dense(vector), self._lexical(query))
        fused: dict[str, Hit] = {}
        for rank, h in enumerate(dense, 1):
            h.dense_rank = rank
            fused[h.chunk_id] = h
        for rank, h in enumerate(lexical, 1):
            fused.setdefault(h.chunk_id, h).lexical_rank = rank
        k = self.s.rag_rrf_k
        for h in fused.values():
            h.fused = sum(1 / (k + r) for r in (h.dense_rank, h.lexical_rank) if r)
        candidates = sorted(fused.values(), key=lambda h: h.fused, reverse=True)
        stage["search_and_fusion"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        reranked = True
        top = candidates[: self.reranker.settings.candidates]
        try:
            scored = await self.reranker.rerank(query, top, text=lambda h: f"{h.header}\n{h.content}")
            for h, score in scored:
                h.rerank = score
            ordered = [h for h, score in scored if score >= self.s.rag_min_rerank_score]
        except ProviderError as exc:  # Section 18: degrade to fused ranking, don't fail the answer
            log.warning("reranker failed, using fused order: %s", exc)
            reranked, ordered = False, top
        stage["rerank"] = (time.perf_counter() - t) * 1000
        return SearchResult(query, ordered[: self.s.rag_top_k], candidates, stage, reranked)
