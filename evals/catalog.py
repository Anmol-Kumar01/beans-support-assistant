"""Catalog of the legacy knowledge base (the source JSON folders in beans-support-bot).

Maps cited URLs and titles back to document IDs, and supplies document text for the
groundedness judge when scoring the current bot (which does not return its context).
"""

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SOURCE_DIRS = {
    "zendesk": "Article Jsons",
    "youtube": "Video Jsons",
    "release_note": "Release Notes Jsons",
    "trainn": "Tutorial Jsons",
}

_ZENDESK_ARTICLE = re.compile(r"/articles/(\d+)")
_WS = re.compile(r"\s+")


@dataclass
class CatalogDocument:
    document_id: str
    source_type: str
    title: str
    url: str | None
    text: str
    audience_tags: list[str] = field(default_factory=list)
    account_buids: list[str] = field(default_factory=list)


def normalize_title(title: str) -> str:
    return _WS.sub(" ", html.unescape(title)).strip().strip(".").lower()


def normalize_url(url: str) -> str | None:
    """Canonical key for a source URL, or None if it is not a known source host."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if host.endswith("zendesk.com"):
        m = _ZENDESK_ARTICLE.search(parsed.path)
        return f"zendesk:{m.group(1)}" if m else None
    if host in ("youtube.com", "m.youtube.com"):
        vid = parse_qs(parsed.query).get("v", [None])[0]
        return f"youtube:{vid}" if vid else None
    if host == "youtu.be":
        vid = parsed.path.strip("/")
        return f"youtube:{vid}" if vid else None
    if host.endswith("trainn.co"):
        return f"trainn-url:{parsed.path.rstrip('/')}"
    return None


def _split_tags(value: str | None) -> list[str]:
    return [t.strip() for t in (value or "").split(";") if t.strip()]


def _clean_caption(text: str) -> str:
    return html.unescape(html.unescape(text)).replace("[Music]", " ")


class Catalog:
    def __init__(self, documents: list[CatalogDocument]):
        self.documents = {d.document_id: d for d in documents}
        self._by_url: dict[str, set[str]] = {}
        self._by_title: dict[str, set[str]] = {}
        for d in documents:
            if d.url and (key := normalize_url(d.url)):
                self._by_url.setdefault(key, set()).add(d.document_id)
            self._by_title.setdefault(normalize_title(d.title), set()).add(d.document_id)

    def __len__(self) -> int:
        return len(self.documents)

    def resolve_url(self, url: str) -> list[str]:
        key = normalize_url(url)
        if key is None:
            return []
        if key.startswith(("zendesk:", "youtube:")) and key in self.documents:
            return [key]
        return sorted(self._by_url.get(key, ()))

    def resolve_title(self, title: str) -> list[str]:
        return sorted(self._by_title.get(normalize_title(title), ()))

    def text(self, document_id: str) -> str | None:
        doc = self.documents.get(document_id)
        return doc.text if doc else None

    @classmethod
    def from_legacy_dir(cls, root: Path) -> "Catalog":
        docs: dict[str, CatalogDocument] = {}
        for source_type, sub in SOURCE_DIRS.items():
            folder = root / sub
            if not folder.is_dir():
                raise FileNotFoundError(f"missing source folder: {folder}")
            for path in sorted(folder.glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                doc = _parse(source_type, data)
                if doc.document_id in docs:
                    # YouTube "<id> Part 2" files are the same video: append the text.
                    docs[doc.document_id].text += "\n\n" + doc.text
                else:
                    docs[doc.document_id] = doc
        return cls(list(docs.values()))


def _parse(source_type: str, d: dict) -> CatalogDocument:
    title = d["title"].strip()
    match source_type:
        case "zendesk":
            return CatalogDocument(
                document_id=f"zendesk:{d['id']}",
                source_type=source_type,
                title=title,
                url=d["metadata"]["html_url"],
                text=f"{title}\n\n{d['body']}",
            )
        case "youtube":
            video_id = str(d["id"]).split(" Part ")[0]
            captions = " ".join(_clean_caption(t["text"]) for t in d["transcript"])
            return CatalogDocument(
                document_id=f"youtube:{video_id}",
                source_type=source_type,
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                text=f"{title}\n\n{captions}",
            )
        case "release_note":
            meta = d.get("metadata") or {}
            return CatalogDocument(
                document_id=f"release_note:{d['id']}",
                source_type=source_type,
                title=title,
                url=meta.get("tutorial_url") or None,
                text=f"{title}\n\n{d.get('body', '')}\n\n{d.get('summary', '')}",
                audience_tags=_split_tags(meta.get("display_option_tags")),
                account_buids=_split_tags(meta.get("account_buids")),
            )
        case "trainn":
            return CatalogDocument(
                document_id=f"trainn:{d['id']}",
                source_type=source_type,
                title=title,
                url=d.get("trainn_url") or None,
                text=f"{title}\n\n{d.get('summary', '')}\n\n{d.get('description') or ''}",
                audience_tags=_split_tags(d.get("display_option_tags")),
                account_buids=_split_tags(d.get("account_buids")),
            )
    raise ValueError(source_type)
