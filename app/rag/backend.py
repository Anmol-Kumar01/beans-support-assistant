"""The answer pipeline behind POST /v1/chat/stream (Architecture doc, Sections 7–14).

  history (last RAG_HISTORY_TURNS turns from the messages table)
    -> answer LLM with the search_knowledge_base tool (decides whether to search, Section 11)
    -> hybrid search + rerank + evidence threshold (app/rag/retrieval.py)
    -> streamed answer with [n] markers, from the numbered evidence only
    -> citations validated in code, renumbered per document, only cited sources returned
    -> turn stored in conversations/messages

Degraded mode (Section 18): if the LLM fails after the search, the reply lists the most
relevant sources instead of failing.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator

from psycopg_pool import AsyncConnectionPool

from app.core.config import AppSettings, ModelSettings
from app.legacy_proxy import BackendError
from app.llm.client import LLMClient
from app.llm.retry import ProviderError
from app.rag import prompts
from app.rag.retrieval import Hit, Retriever, SearchResult

log = logging.getLogger("app.rag")

_MARKERS = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
_REPEAT = re.compile(r"(\[\d+\])(?:\s*\1)+")
TENANT, USER = "local", "local-user"  # until auth (Section 15) provides real identities


def _conversation_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:  # e.g. eval session ids
        return uuid.uuid5(uuid.NAMESPACE_URL, f"beans-bot:{value}")


def _strip_markers(text: str) -> str:
    return re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", text)


def cite(answer: str, evidence: list[Hit]) -> tuple[str, list[dict], list[Hit]]:
    """Validate [n] markers against the evidence, renumber them per document in order of
    first citation, and return (answer, sources, cited hits). Invalid markers are dropped."""
    doc_number: dict[str, int] = {}
    sources: list[dict] = []
    cited: list[Hit] = []

    def replace(m: re.Match) -> str:
        out = []
        for raw in re.split(r"\s*,\s*", m.group(1)):
            i = int(raw)
            if not 1 <= i <= len(evidence):
                continue
            h = evidence[i - 1]
            if h.document_id not in doc_number:
                doc_number[h.document_id] = len(sources) + 1
                sources.append({
                    "n": doc_number[h.document_id],
                    "document_id": h.document_id,
                    "chunk_id": h.chunk_id,
                    "title": h.title,
                    "section": h.section,
                    "url": h.url,
                    "source_type": h.source_type,
                    "updated_at": h.updated_at,
                    "excerpt": re.sub(r"\s+", " ", h.content)[:240],
                })
            if h not in cited:
                cited.append(h)
            out.append(f"[{doc_number[h.document_id]}]")
        return "".join(dict.fromkeys(out))

    text = _REPEAT.sub(r"\1", _MARKERS.sub(replace, answer))
    return re.sub(r"[ \t]+([.,;:])", r"\1", text), sources, cited


class RagBackend:
    name = "rag"

    def __init__(self, settings: AppSettings, models: ModelSettings):
        self.s = settings
        self.models = models
        self.pool = AsyncConnectionPool(settings.database_url, min_size=1, max_size=5, open=False)
        self.retriever: Retriever | None = None
        self.llm: LLMClient | None = None
        self.counts: dict[str, int] = {}
        self.startup_error: str | None = None

    async def open(self) -> None:
        try:
            await self.pool.open(wait=True, timeout=10)
        except Exception as exc:
            raise BackendError(
                f"Can't connect to the database ({self.s.database_url.split('@')[-1]}). "
                "Run ./scripts/setup_db.sh, then python -m app.ingest."
            ) from exc
        self.retriever = Retriever(self.pool, self.s, self.models)
        self.llm = LLMClient("answer", self.models.answer)
        await self.refresh_counts()
        if not self.counts:
            log.warning("the knowledge index is empty: run python -m app.ingest")

    async def refresh_counts(self) -> None:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT source_type, count(*) FROM source_documents WHERE status = 'active' GROUP BY 1")).fetchall()
        self.counts = dict(rows)

    async def aclose(self) -> None:
        if self.retriever:
            await self.retriever.aclose()
        if self.llm:
            await self.llm.aclose()
        await self.pool.close()

    def describe(self) -> dict:
        return {"backend": self.name, "sources": self.counts, "answer_model": self.models.answer.model}

    async def sources(self, source_type: str | None = None) -> list[dict]:
        if self.llm is None:  # database not connected
            return []
        sql = """SELECT d.document_id, d.title, d.url, d.source_type,
                        (SELECT left(c.content, 240) FROM chunks c WHERE c.document_id = d.document_id ORDER BY c.ordinal LIMIT 1)
                 FROM source_documents d WHERE d.status = 'active' AND (%s::text IS NULL OR d.source_type = %s)
                 ORDER BY lower(d.title)"""
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (source_type, source_type))).fetchall()
        return [{"document_id": r[0], "title": r[1], "url": r[2], "source_type": r[3], "excerpt": r[4] or ""} for r in rows]

    async def document(self, document_id: str) -> dict | None:
        """One source with its full text (all chunks in order), for the in-app viewer."""
        if self.llm is None:
            return None
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                """SELECT d.document_id, d.title, d.url, d.source_type,
                          string_agg(c.content, E'\n\n' ORDER BY c.ordinal)
                   FROM source_documents d JOIN chunks c USING (document_id)
                   WHERE d.document_id = %s AND d.status = 'active'
                   GROUP BY d.document_id""", (document_id,))).fetchone()
        if not row:
            return None
        return {"document_id": row[0], "title": row[1], "url": row[2], "source_type": row[3], "text": row[4]}

    # ------------------------------------------------------------------ memory

    async def _history(self, conv: uuid.UUID) -> list[dict]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT question, answer FROM messages WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s",
                (conv, self.s.rag_history_turns))).fetchall()
        messages = []
        for question, answer in reversed(rows):
            messages += [{"role": "user", "content": question}, {"role": "assistant", "content": _strip_markers(answer)}]
        return messages

    async def _store(self, conv: uuid.UUID, message_id: uuid.UUID, question: str, final: dict, cited: list[Hit],
                     tools: list[str], usage: dict, stage_ms: dict) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO conversations (conversation_id, tenant_id, user_id) VALUES (%s, %s, %s)
                   ON CONFLICT (conversation_id) DO UPDATE SET last_activity_at = now()""", (conv, TENANT, USER))
            await conn.execute(
                """INSERT INTO messages (message_id, conversation_id, tenant_id, question, answer, cited_chunk_ids,
                                         evidence_status, tool_calls, model, input_tokens, output_tokens, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (message_id, conv, TENANT, question, final["answer"], [h.chunk_id for h in cited],
                 final["evidence_status"], tools, self.models.answer.model, usage.get("input_tokens"),
                 usage.get("output_tokens"), json.dumps(stage_ms)))

    # ------------------------------------------------------------------ answer

    async def stream_answer(self, message: str, conversation_id: str) -> AsyncIterator[tuple[str, dict]]:
        """Yield ("token", {"text"}) events, then one ("final", {...}) event."""
        if self.llm is None:
            raise BackendError(self.startup_error or "The answer pipeline is not ready. Check the server log.")
        if not self.counts:
            await self.refresh_counts()
            if not self.counts:
                raise BackendError("The knowledge base is empty. Run: python -m app.ingest")
        t0 = time.perf_counter()
        stage: dict[str, float] = {}
        conv = _conversation_uuid(conversation_id)
        message_id = uuid.uuid4()

        t = time.perf_counter()
        system = prompts.SYSTEM.format(support="support@beans.ai")
        messages = [{"role": "system", "content": system}, *await self._history(conv), {"role": "user", "content": message}]
        stage["history"] = (time.perf_counter() - t) * 1000

        # 1. Let the model decide whether to search (Section 11).
        t = time.perf_counter()
        try:
            decision = await self.llm.chat(messages, tools=[prompts.SEARCH_TOOL], tool_choice="auto", max_output_tokens=400)
        except ProviderError as exc:
            raise BackendError(f"The answer model is unavailable: {exc}") from exc
        stage["tool_decision"] = (time.perf_counter() - t) * 1000
        usage = {"input_tokens": decision.input_tokens, "output_tokens": decision.output_tokens}

        evidence: list[Hit] = []
        searches: list[SearchResult] = []
        tool_calls: list[str] = []
        if decision.tool_calls:
            calls = decision.tool_calls[: self.s.rag_max_tool_calls]
            messages.append({
                "role": "assistant",
                "content": decision.content or "",
                "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
            })
            for c in calls:
                try:
                    query = json.loads(c["arguments"] or "{}").get("query") or message
                except json.JSONDecodeError:
                    query = message
                try:
                    result = await self.retriever.search(query)
                except ProviderError as exc:
                    raise BackendError(f"Search is unavailable: {exc}") from exc
                searches.append(result)
                tool_calls.append("search_knowledge_base")
                for k, v in result.stage_ms.items():
                    stage[k] = stage.get(k, 0) + v
                numbered = []
                for h in result.hits:
                    if all(e.chunk_id != h.chunk_id for e in evidence):
                        evidence.append(h)
                    numbered.append((next(i for i, e in enumerate(evidence, 1) if e.chunk_id == h.chunk_id), h))
                content = f"{prompts.format_evidence(numbered)}\n\n{prompts.CITE_RULES}" if numbered else prompts.NO_RESULTS
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})

        # 2. Stream the answer.
        text = ""
        if not decision.tool_calls:
            text = decision.content.strip()  # greeting / small talk / out of scope: no search needed
            if text:
                yield "token", {"text": text}
        else:
            t = time.perf_counter()
            first = None
            try:
                # The tool stays declared (some providers reject tool results without it) but can't be called again.
                async for delta in self.llm.stream(messages, tools=[prompts.SEARCH_TOOL], tool_choice="none",
                                                   max_output_tokens=self.models.answer.max_output_tokens):
                    if first is None:
                        first = (time.perf_counter() - t) * 1000
                    text += delta
                    yield "token", {"text": delta}
            except ProviderError as exc:
                log.warning("answer generation failed, degraded mode: %s", exc)
                text = "I couldn't write an answer right now, but these sources look relevant:\n\n" + "\n".join(
                    f"- {h.title} [{i}]" for i, h in enumerate(evidence[:4], 1)) if evidence else \
                    "I couldn't write an answer right now. Please try again in a moment."
                yield "token", {"text": text}
            stage["llm_ttft"] = first or 0.0
            stage["llm_total"] = (time.perf_counter() - t) * 1000

        # 3. Validate citations and build the response (Section 10).
        answer, sources, cited = cite(text, evidence)
        lowered = answer.lower()
        uncited = bool(evidence) and not sources and prompts.NOT_FOUND_PHRASE not in lowered
        if uncited:
            # Quality event (Section 10): the model ignored the citation rule. Still show the
            # top documents it was given so the user has links; flagged in debug and the log.
            log.warning("uncited knowledge answer for %r", message)
            seen = []
            for h in evidence:
                if h.document_id not in seen:
                    seen.append(h.document_id)
                    sources.append({"n": len(sources) + 1, "document_id": h.document_id, "chunk_id": h.chunk_id,
                                    "title": h.title, "section": h.section, "url": h.url, "source_type": h.source_type,
                                    "updated_at": h.updated_at, "excerpt": re.sub(r"\s+", " ", h.content)[:240],
                                    "cited": False})
                if len(sources) == 3:
                    break
        if decision.tool_calls and not evidence:
            status = "not_found"
        elif prompts.NOT_FOUND_PHRASE in lowered:
            status = "partial" if sources else "not_found"
        elif sources and not uncited:
            status = "found"
        else:
            status = None
        stage["total"] = (time.perf_counter() - t0) * 1000

        final = {
            "answer": answer,
            "sources": sources,
            "evidence_status": status,
            "conversation_id": conversation_id,
            "message_id": str(message_id),
        }
        if self.s.rag_return_debug:
            last = searches[-1] if searches else None
            final["debug"] = {
                "retrieved": [{"document_id": h.document_id, "chunk_id": h.chunk_id, "rank": i, "score": h.rerank}
                              for i, h in enumerate(last.candidates if last else [], 1)],
                "evidence": [{"n": next((s["n"] for s in sources if s["document_id"] == h.document_id), 0),
                              "document_id": h.document_id, "text": h.content} for h in cited],
                "tool_calls": tool_calls,
                "uncited": uncited,
                "queries": [r.query for r in searches],
                "reranked": all(r.reranked for r in searches),
                "stage_ms": {k: round(v, 1) for k, v in stage.items()},
                "usage": usage,
                "models": {"answer": self.models.answer.model, "embedding": self.models.embedding.model,
                           "reranker": self.models.reranker.model},
            }
        try:
            await self._store(conv, message_id, message, final, cited, tool_calls, usage, stage)
        except Exception as exc:  # storing history must not lose the answer
            log.warning("could not store the turn: %s", exc)
        yield "final", final
