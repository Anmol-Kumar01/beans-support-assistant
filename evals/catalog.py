"""Catalog of the knowledge-base documents in the source JSON folders of data_sources/.

Used to check the golden set's expected source IDs and as vocabulary for the PII scrubber.
"""

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_DIRS = {
    "zendesk": "Article Jsons",
    "youtube": "Video Jsons",
    "release_note": "Release Notes Jsons",
    "trainn": "Tutorial Jsons",
}


@dataclass
class CatalogDocument:
    document_id: str
    source_type: str
    title: str
    url: str | None
    text: str
    audience_tags: list[str] = field(default_factory=list)
    account_buids: list[str] = field(default_factory=list)


def _split_tags(value: str | None) -> list[str]:
    return [t.strip() for t in (value or "").split(";") if t.strip()]


def _clean_caption(text: str) -> str:
    return html.unescape(html.unescape(text)).replace("[Music]", " ")


class Catalog:
    def __init__(self, documents: list[CatalogDocument]):
        self.documents = {d.document_id: d for d in documents}

    def __len__(self) -> int:
        return len(self.documents)

    @classmethod
    def from_dir(cls, root: Path) -> "Catalog":
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
