"""Target for the current Node.js bot (beans-support-bot/ChatbotEndpoint.js).

Contract today: ``POST /`` with ``{"query", "sessionID"}``; the reply body is the final
answer text with markdown source links added by a second LLM pass. The bot does not
stream, so TTFT equals total latency, and it does not report retrieval, usage, or cost.

If the bot later returns JSON with ``{"answer", "retrieved": [<chroma id>, ...]}`` (an
opt-in debug mode, pending approval), retrieval metrics are scored from it automatically.
"""

import re
import time

import httpx

from evals.catalog import Catalog, normalize_url
from evals.schema import BotResponse, Citation, EvalQuestion, EvidenceItem, RetrievedItem
from evals.targets.base import Target

_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((\S+?)\)")
# "[Title]" not followed by "(" — how the current bot cites release notes.
_BARE_TITLE = re.compile(r"\[([^\]\n]{3,200})\](?!\()")


def parse_legacy_citations(answer: str, catalog: Catalog) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[tuple[str, ...]] = set()

    def add(c: Citation) -> None:
        key = tuple(c.document_ids) or (c.url or c.title or "",)
        if key not in seen:
            seen.add(key)
            citations.append(c)

    for title, url in _MD_LINK.findall(answer):
        if normalize_url(url) is None:
            continue  # not a knowledge-source link (e.g. beansroute.ai)
        ids = catalog.resolve_url(url) or catalog.resolve_title(title)
        add(Citation(title=title, url=url, document_ids=ids, valid=bool(ids)))
    for title in _BARE_TITLE.findall(answer):
        if ids := catalog.resolve_title(title):
            add(Citation(title=title, document_ids=ids))
    return citations


def legacy_id_to_document_id(chroma_id: str, catalog: Catalog) -> str | None:
    """Chroma IDs are the raw source IDs; find the prefixed catalog ID."""
    base = str(chroma_id).split(" Part ")[0]
    for prefix in ("zendesk", "youtube", "release_note", "trainn"):
        if f"{prefix}:{base}" in catalog.documents:
            return f"{prefix}:{base}"
    return None


def build_legacy_response(
    answer: str,
    catalog: Catalog,
    max_evidence_chars: int,
    *,
    retrieved: list[RetrievedItem] | None,
    total_ms: float,
    raw: dict | str | None,
    models: dict[str, str] | None = None,
) -> BotResponse:
    """Score-ready response from the current bot's final text (live or from a trace).
    The bot does not return its context, so cited documents' text is the evidence."""
    citations = parse_legacy_citations(answer, catalog)
    cited_ids = dict.fromkeys(d for c in citations for d in c.document_ids)
    evidence = [
        EvidenceItem(
            label=catalog.documents[doc_id].title,
            document_id=doc_id,
            text=catalog.documents[doc_id].text[:max_evidence_chars],
        )
        for doc_id in cited_ids
    ]
    return BotResponse(
        answer=answer,
        citations=citations,
        retrieved=retrieved,
        evidence=evidence,
        ttft_ms=total_ms,  # the current bot does not stream
        total_ms=total_ms,
        models=models,
        raw=raw,
    )


class LegacyNodeTarget(Target):
    name = "legacy_node"

    def __init__(
        self,
        base_url: str,
        catalog: Catalog,
        timeout_s: float,
        max_evidence_chars: int,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.catalog = catalog
        self.max_evidence_chars = max_evidence_chars
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    def describe(self) -> dict:
        return {"target": self.name, "base_url": self.base_url, "catalog_docs": len(self.catalog)}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, query: str, session_id: str) -> httpx.Response:
        resp = await self._client.post(
            f"{self.base_url}/", json={"query": query, "sessionID": session_id}
        )
        resp.raise_for_status()
        return resp

    async def answer(self, question: EvalQuestion, session_id: str) -> BotResponse:
        try:
            for turn in question.prior_turns:
                await self._post(turn, session_id)
            start = time.perf_counter()
            resp = await self._post(question.question, session_id)
            total_ms = (time.perf_counter() - start) * 1000
        except httpx.HTTPError as exc:
            return BotResponse(error=f"{type(exc).__name__}: {exc}")

        retrieved = None
        raw: dict | str = resp.text
        answer = resp.text
        if resp.headers.get("content-type", "").startswith("application/json"):
            raw = resp.json()
            answer = raw.get("answer", "")
            if "retrieved" in raw:
                retrieved = []
                for rank, cid in enumerate(raw["retrieved"], 1):
                    if doc_id := legacy_id_to_document_id(cid, self.catalog):
                        retrieved.append(RetrievedItem(document_id=doc_id, rank=rank))

        return build_legacy_response(
            answer, self.catalog, self.max_evidence_chars,
            retrieved=retrieved, total_ms=total_ms, raw=raw,
        )
