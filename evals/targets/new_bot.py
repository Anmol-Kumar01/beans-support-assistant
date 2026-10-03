"""Target for the new FastAPI bot. This module defines the streaming contract the Phase 2
API must implement (response body per Section 10, plus an eval-only ``debug`` block).

Request: ``POST {base_url}{chat_path}`` with ``Authorization: Bearer <token>`` and
``{"message": str, "conversation_id": str}``. Tenant and user come from the token only.

Response: ``text/event-stream`` with events
  event: token  data: {"text": "..."}                     (repeated; first one = TTFT)
  event: final  data: {"answer", "sources": [{"n", "document_id", "chunk_id", "title",
                       "section", "url"}], "evidence_status", "conversation_id",
                       "debug": {"retrieved": [{"document_id", "chunk_id", "rank", "score"}],
                                 "evidence": [{"n", "document_id", "text"}],
                                 "tool_calls": [str], "stage_ms": {stage: ms},
                                 "usage": {"input_tokens", "output_tokens", "cost_usd"},
                                 "models": {role: model}}}   (answer, small, embedding, reranker)
  event: error  data: {"message": "..."}
``debug`` is returned only to eval/internal callers; it is optional here.
"""

import json
import re
import time
from collections.abc import AsyncIterator

import httpx

from evals.schema import (
    BotResponse,
    Citation,
    EvalQuestion,
    EvidenceItem,
    RetrievedItem,
    Usage,
)
from evals.targets.base import Target

_MARKER = re.compile(r"\[(\d{1,3})\]")


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Minimal SSE parser: yields (event, data) pairs."""
    event, data = "message", []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield event, "\n".join(data)


def citations_from_final(answer: str, sources: list[dict]) -> list[Citation]:
    """Independently re-check the bot's citations: every inline [n] must match a source."""
    by_n = {s.get("n"): s for s in sources}
    citations = [
        Citation(
            n=s.get("n"),
            document_ids=[s["document_id"]] if s.get("document_id") else [],
            title=s.get("title"),
            url=s.get("url"),
            valid=bool(s.get("document_id")),
        )
        for s in sources
    ]
    for n in dict.fromkeys(int(m) for m in _MARKER.findall(answer)):
        if n not in by_n:
            citations.append(Citation(n=n, valid=False))
    return citations


class NewBotTarget(Target):
    name = "new_bot"

    def __init__(
        self,
        base_url: str,
        chat_path: str,
        token: str | None,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.chat_path = chat_path
        headers = {"Accept": "text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._headers = headers

    def describe(self) -> dict:
        return {"target": self.name, "base_url": self.base_url, "chat_path": self.chat_path}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _turn(self, message: str, conversation_id: str) -> tuple[dict, float | None, float]:
        start = time.perf_counter()
        ttft = None
        final: dict | None = None
        async with self._client.stream(
            "POST",
            f"{self.base_url}{self.chat_path}",
            json={"message": message, "conversation_id": conversation_id},
            headers=self._headers,
        ) as resp:
            resp.raise_for_status()
            async for event, data in iter_sse(resp.aiter_lines()):
                if event == "token" and ttft is None:
                    ttft = (time.perf_counter() - start) * 1000
                elif event == "final":
                    final = json.loads(data)
                elif event == "error":
                    raise RuntimeError(json.loads(data).get("message", data))
        if final is None:
            raise RuntimeError("stream ended without a final event")
        return final, ttft, (time.perf_counter() - start) * 1000

    async def answer(self, question: EvalQuestion, session_id: str) -> BotResponse:
        try:
            for turn in question.prior_turns:
                await self._turn(turn, session_id)
            final, ttft, total = await self._turn(question.question, session_id)
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
            return BotResponse(error=f"{type(exc).__name__}: {exc}")

        answer = final.get("answer", "")
        debug = final.get("debug") or {}
        retrieved = None
        if "retrieved" in debug:
            retrieved = [RetrievedItem.model_validate(r) for r in debug["retrieved"]]
        usage = Usage.model_validate(debug["usage"]) if debug.get("usage") else None
        return BotResponse(
            answer=answer,
            citations=citations_from_final(answer, final.get("sources", [])),
            retrieved=retrieved,
            evidence=[
                EvidenceItem(label=f"[{e['n']}]", document_id=e.get("document_id"), text=e["text"])
                for e in debug.get("evidence", [])
            ],
            evidence_status=final.get("evidence_status"),
            tool_calls=debug.get("tool_calls"),
            ttft_ms=ttft,
            total_ms=total,
            stage_ms=debug.get("stage_ms", {}),
            usage=usage,
            models=debug.get("models"),
            raw=final,
        )
