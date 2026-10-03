"""Split SourceDocs into retrieval chunks (Architecture doc, Section 9).

  zendesk / release_note / api_reference / beans_content / trainn
      Paragraph and step blocks are packed up to ~TARGET tokens and never split mid-block,
      so a numbered step stays whole. Short documents stay as one chunk.
  youtube
      ~WINDOW_S-second windows of the transcript; each chunk keeps its start time so the
      citation deep-links to that moment (&t=…s).

Every chunk gets a context header ("Title › part · source") stored separately and
embedded/indexed with the content, so a fragment like "Click Save" is still findable.
The header is deterministic for now; an LLM-written summary can replace it later.
"""

import hashlib
import re
from dataclasses import dataclass

from app.ingest.sources import SourceDoc

CHUNKER_VERSION = "v1"
TARGET_TOKENS = 400
MAX_TOKENS = 700
WINDOW_S = 90.0

SOURCE_LABELS = {
    "zendesk": "Help article",
    "youtube": "Training video",
    "release_note": "Release note",
    "trainn": "Interactive tutorial",
    "api_reference": "API reference",
    "beans_content": "Beans.ai product info",
}

_BLOCKS = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def tokens(text: str) -> int:
    """Rough token count (about 4 characters per token for English)."""
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    ordinal: int
    source_type: str
    title: str
    section_path: str | None
    content: str
    context_header: str
    token_count: int
    source_url: str | None
    timestamp_start: float | None = None

    @property
    def embed_text(self) -> str:
        return f"{self.context_header}\n\n{self.content}"


def _pack(blocks: list[str]) -> list[str]:
    """Greedily pack blocks into chunks of ~TARGET tokens; split only oversized blocks."""
    pieces: list[str] = []
    for block in blocks:
        if tokens(block) <= MAX_TOKENS:
            pieces.append(block)
            continue
        current = ""
        for sentence in _SENTENCE.split(block):
            if current and tokens(current) + tokens(sentence) > TARGET_TOKENS:
                pieces.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)
    chunks, current = [], ""
    for piece in pieces:
        if current and tokens(current) + tokens(piece) > TARGET_TOKENS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _video_windows(doc: SourceDoc) -> list[tuple[float, str]]:
    windows, start, words = [], None, []
    for t, text in doc.segments or []:
        if start is None:
            start = t
        words.append(text)
        if t - start >= WINDOW_S or tokens(" ".join(words)) >= TARGET_TOKENS:
            windows.append((start, " ".join(words)))
            start, words = None, []
    if words:
        windows.append((start or 0.0, " ".join(words)))
    return windows


def _timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def chunk_document(doc: SourceDoc) -> list[Chunk]:
    label = SOURCE_LABELS.get(doc.source_type, doc.source_type)
    if doc.source_type == "youtube":
        parts = [(start, text, f"from {_timestamp(start)}") for start, text in _video_windows(doc)]
    else:
        blocks = [b.strip() for b in _BLOCKS.split(doc.text) if b.strip()]
        packed = _pack(blocks) or [doc.title]
        parts = [(None, text, f"part {i + 1} of {len(packed)}" if len(packed) > 1 else None) for i, text in enumerate(packed)]

    chunks = []
    for ordinal, (start, content, section) in enumerate(parts):
        header = " › ".join(x for x in (doc.title, section) if x) + f" · {label}"
        url = doc.url
        if start is not None and url:
            url = f"{url}&t={int(start)}s"
        chunk_id = hashlib.sha256(f"{doc.document_id}|{CHUNKER_VERSION}|{ordinal}".encode()).hexdigest()[:32]
        chunks.append(Chunk(
            chunk_id=chunk_id,
            document_id=doc.document_id,
            ordinal=ordinal,
            source_type=doc.source_type,
            title=doc.title,
            section_path=section,
            content=content,
            context_header=header,
            token_count=tokens(content),
            source_url=url,
            timestamp_start=start,
        ))
    return chunks
