"""Temporary chat backend: forwards questions to the current Node.js bot and reshapes its
reply into the Section 10 response (numbered ``[n]`` markers plus a sources list).

This lets the chat UI be built against the Phase 2 contract now. The Phase 2 pipeline
replaces this module; the API and UI stay the same.

The current bot cites sources as markdown links (``[Title](url)``), or as a bare
``[Title]`` for release notes. Links that resolve to a catalog document become numbered
markers; other links (e.g. beansroute.ai) are left as they are.
"""

import html
import re
from dataclasses import dataclass, field

import httpx

from evals.catalog import Catalog, normalize_title, normalize_url

_CITATION = re.compile(r"\[([^\]\n]+)\]\((\S+?)\)|\[([^\]\n]{3,200})\](?!\()")
_REPEATED_MARKER = re.compile(r"(\[\d+\])(?:\s*\1)+")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

SOURCE_TYPES = ("zendesk", "youtube", "release_note", "trainn")


class BackendError(Exception):
    """A failure the user should see as a readable message."""


@dataclass
class ChatResult:
    answer: str
    sources: list[dict] = field(default_factory=list)
    evidence_status: str | None = None


def _excerpt(text: str, title: str, limit: int) -> str:
    body = text.removeprefix(title)
    body = _WS.sub(" ", html.unescape(_TAG.sub(" ", body))).strip()
    body = body[:1].upper() + body[1:]  # video transcripts are all lower case
    if len(body) <= limit:
        return body
    return body[:limit].rsplit(" ", 1)[0] + "…"


def number_citations(answer: str, catalog: Catalog, excerpt_chars: int) -> tuple[str, list[dict]]:
    """Replace source links with ``[n]`` markers. Returns the new answer and its sources,
    numbered in order of first citation and de-duplicated by document."""
    sources: list[dict] = []
    n_by_doc: dict[str, int] = {}

    def cite(ids: list[str], link_url: str | None) -> int:
        doc_id = ids[0]
        if doc_id not in n_by_doc:
            doc = catalog.documents[doc_id]
            n_by_doc[doc_id] = len(sources) + 1
            sources.append({
                "n": n_by_doc[doc_id],
                "document_id": doc_id,
                "chunk_id": None,
                "title": doc.title,
                "section": None,
                "url": doc.url or link_url,
                "source_type": doc.source_type,
                "excerpt": _excerpt(doc.text, doc.title, excerpt_chars),
            })
        return n_by_doc[doc_id]

    def replace(m: re.Match) -> str:
        title, url, bare_title = m.groups()
        if bare_title is not None:
            ids = catalog.resolve_title(bare_title)
            return f"[{cite(ids, None)}]" if ids else m.group(0)
        if normalize_url(url) is None:
            return m.group(0)
        ids = catalog.resolve_url(url) or catalog.resolve_title(title)
        if not ids:
            return m.group(0)
        n = cite(ids, url)
        # A link whose text is just the source title becomes a bare marker; otherwise the
        # text is part of the sentence ("see [this article](...)") and is kept.
        if normalize_title(title) == normalize_title(catalog.documents[ids[0]].title):
            return f"[{n}]"
        return f"{title} [{n}]"

    numbered = _REPEATED_MARKER.sub(r"\1", _CITATION.sub(replace, answer))
    return numbered, sources


class LegacyBackend:
    name = "legacy_node"

    def __init__(
        self,
        base_url: str,
        catalog: Catalog,
        timeout_s: float,
        not_found_phrases: list[str],
        excerpt_chars: int,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.catalog = catalog
        self.not_found_phrases = [p.lower() for p in not_found_phrases]
        self.excerpt_chars = excerpt_chars
        self.timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    def describe(self) -> dict:
        counts = {t: 0 for t in SOURCE_TYPES}
        for doc in self.catalog.documents.values():
            counts[doc.source_type] = counts.get(doc.source_type, 0) + 1
        return {"backend": self.name, "base_url": self.base_url, "sources": counts}

    async def aclose(self) -> None:
        await self._client.aclose()

    def sources(self, source_type: str | None = None) -> list[dict]:
        """Knowledge-base documents for the UI's browse pages, sorted by title."""
        docs = [d for d in self.catalog.documents.values() if source_type in (None, d.source_type)]
        return [
            {
                "document_id": d.document_id,
                "title": d.title,
                "url": d.url,
                "source_type": d.source_type,
                "excerpt": _excerpt(d.text, d.title, self.excerpt_chars),
            }
            for d in sorted(docs, key=lambda d: d.title.lower())
        ]

    async def ask(self, message: str, conversation_id: str) -> ChatResult:
        try:
            resp = await self._client.post(
                f"{self.base_url}/", json={"query": message, "sessionID": conversation_id}
            )
        except httpx.TimeoutException as exc:
            raise BackendError(
                f"The current bot took longer than {self.timeout_s:.0f}s to answer."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(
                f"Can't reach the current bot at {self.base_url}. "
                "Start it with `npm start` in beans-support-bot."
            ) from exc
        if resp.status_code != 200:
            raise BackendError(f"The current bot returned HTTP {resp.status_code}.")

        text = resp.text
        if resp.headers.get("content-type", "").startswith("application/json"):
            text = resp.json().get("answer", "")
        answer, sources = number_citations(text, self.catalog, self.excerpt_chars)

        lowered = answer.lower()
        if any(p in lowered for p in self.not_found_phrases):
            status = "not_found"
        elif sources:
            status = "found"
        else:
            status = None  # e.g. greetings: no knowledge was needed
        return ChatResult(answer=answer, sources=sources, evidence_status=status)
