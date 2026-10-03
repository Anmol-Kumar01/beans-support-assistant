"""Explore-Beans content: everything in data_sources/, normalized for the UI.

  beans_content.json                         products, APIs, maps, traffic, ServiceNow, contacts
  Beans Route API Collection*.json           Postman collection -> API reference
  tutorials.json + Tutorial Jsons/*.json      interactive (Trainn) tutorials
  articles.json + Article Jsons/*.json        help-center articles
  release-notes.json                         release notes with step-by-step HTML

The same tutorial or article often appears in several files (and several times in one
file, once per customer account), so items are merged:
  tutorials: same access link (normalized), else same title on the same platform
  articles:  same help-center link, else same article id, else same title
Earlier sources win for the displayed fields; audiences are combined.

Tutorials tied only to specific customer accounts (``account_buids``) are left out unless
BOT_HUB_INCLUDE_ACCOUNT_TUTORIALS=true, so one customer's training is not shown to all.
HTML in release notes is sanitized in the browser before it is rendered.
"""

import json
import re
from datetime import date
from pathlib import Path

_LOGO_LINE = re.compile(r"^\s*(?:<img[^>]*beans-128x128[^>]*>|!\[Beans Logo\]\([^)]*\))\s*$", re.M)
_VAR = re.compile(r"\{\{([\w-]+)\}\}")
AUDIENCES = {"fedex": "FedEx", "regionals": "Regionals", "sway": "Sway"}


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)


def _img_to_md(m: re.Match) -> str:
    """<img src=... alt=...> -> ![alt](src), so the UI never has to render raw HTML."""
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
    return f"![{attrs.get('alt', '')}]({attrs['src']})" if attrs.get("src") else ""


def _clean_md(text: str | None) -> str:
    """Drop the repeated logo image and the leading H1 (it repeats the item's name)."""
    text = _IMG_TAG.sub(_img_to_md, _LOGO_LINE.sub("", text or ""))
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _path_params(text: str) -> str:
    return _VAR.sub(lambda m: "{" + m.group(1) + "}", text)


def load_api_reference(path: Path) -> dict:
    c = json.loads(path.read_text(encoding="utf-8"))
    variables = {v["key"]: v.get("value") for v in c.get("variable", [])}
    base = variables.get("baseURL", "")
    groups = []
    for folder in c.get("item", []):
        endpoints = []
        for it in folder.get("item", []):
            req = it["request"]
            url = req["url"] if isinstance(req["url"], dict) else {"raw": req["url"]}
            raw = url["raw"].replace("{{baseURL}}", "")
            body = (req.get("body") or {}).get("raw") or ""
            endpoints.append({
                "id": _slug(f"{folder['name']} {it['name']}"),
                "name": it["name"],
                "method": req["method"],
                "path": _path_params(raw.split("?")[0]),
                "url": base + _path_params(raw),
                "description": _clean_md(req.get("description")),
                "query": [
                    {"key": q.get("key"), "value": q.get("value"), "description": q.get("description") or ""}
                    for q in url.get("query") or [] if not q.get("disabled")
                ],
                "headers": [
                    {"key": h["key"], "value": _path_params(h.get("value", ""))}
                    for h in req.get("header", []) if not h.get("disabled")
                ],
                "body": _path_params(body),
            })
        groups.append({
            "id": _slug(folder["name"]),
            "name": folder["name"],
            "description": _clean_md(folder.get("description")),
            "endpoints": endpoints,
        })
    return {
        "name": c["info"]["name"],
        "description": _clean_md(c["info"].get("description")),
        "base_url": base,
        "groups": groups,
        "endpoint_count": sum(len(g["endpoints"]) for g in groups),
    }


def _date(value: str | None, ts: int | None) -> str | None:
    if value:
        try:
            y, m, d = (int(p) for p in value.split("-"))
            return date(y, m, d).isoformat()  # also fixes "2024-1-1"
        except ValueError:
            pass
    return date.fromtimestamp(ts).isoformat() if ts else None


def _audiences(tags: str | None) -> list[str]:
    return [AUDIENCES.get(t.strip().lower(), t.strip()) for t in (tags or "").split(";") if t.strip()]


_DASHES = re.compile(r"[‐‑‒–—−]")
_NON_WORD = re.compile(r"[\W_]+")


def _url(value: str | None) -> str | None:
    # Some URLs carry a leading space or a look-alike hyphen (e.g. U+2011) from copy-paste.
    return _DASHES.sub("-", (value or "").strip()) or None


def _link_key(value: str | None) -> str | None:
    """Comparable form of an access link: no query, fragment, /embed or trailing slash."""
    url = _url(value)
    if not url:
        return None
    url = re.sub(r"[?#].*$", "", url).rstrip("/")
    return re.sub(r"/embed$", "", url).lower()


def _title_key(value: str | None) -> str:
    return _NON_WORD.sub(" ", (value or "").replace("\xa0", " ").lower()).strip()


def _read_sources(data_dir: Path, file_name: str, folder: str) -> list[dict]:
    """Items from the main JSON file first, then from the folder of single-item files."""
    items: list[dict] = []
    if (p := data_dir / file_name).is_file():
        items += json.loads(p.read_text(encoding="utf-8"))
    if (data_dir / folder).is_dir():
        for f in sorted((data_dir / folder).glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            items += data if isinstance(data, list) else [data]
    return items


class _Groups:
    """Union of items that share any key, in first-seen order."""

    def __init__(self) -> None:
        self.groups: list[list[dict]] = []
        self._by_key: dict[str, int] = {}

    def add(self, item: dict, keys: list[str | None]) -> None:
        keys = [k for k in keys if k]
        hits = sorted({self._by_key[k] for k in keys if k in self._by_key})
        if not hits:
            self.groups.append([item])
            idx = len(self.groups) - 1
        else:
            idx = hits[0]
            self.groups[idx].append(item)
            for other in hits[1:]:  # an item can join two existing groups together
                self.groups[idx] += self.groups[other]
                self.groups[other] = []
                for key, value in list(self._by_key.items()):
                    if value == other:
                        self._by_key[key] = idx
        for key in keys:
            self._by_key[key] = idx

    def merged(self) -> list[list[dict]]:
        return [g for g in self.groups if g]


def load_tutorials(data_dir: Path, include_account_specific: bool) -> dict:
    raw = _read_sources(data_dir, "tutorials.json", "Tutorial Jsons")
    groups = _Groups()
    for t in raw:
        platform = (t.get("platform") or "Other").strip()
        groups.add(t, [_link_key(t.get("trainn_url")), f"title:{platform}:{_title_key(t.get('title'))}"])

    items, hidden = [], 0
    for g in groups.merged():
        general = [t for t in g if not (t.get("account_buids") or "").strip()]
        if not general and not include_account_specific:
            hidden += 1
            continue
        main = (general or g)[0]
        dates = [d for t in g if (d := _date(t.get("date_str"), t.get("timestamp")))]
        items.append({
            "id": main["id"],
            "title": main["title"].strip(),
            "summary": next((t["summary"].strip() for t in g if (t.get("summary") or "").strip()), ""),
            "platform": main.get("platform") or "Other",
            "category": main.get("category") or "Other",
            "audiences": list(dict.fromkeys(a for t in g for a in _audiences(t.get("display_option_tags")))),
            "url": _url(main.get("trainn_url")),
            "date": max(dates) if dates else None,
        })
    items.sort(key=lambda t: (t["category"], t["title"].lower()))
    unique = len(groups.merged())
    return {
        "items": items,
        "hidden_account_specific": hidden,
        "stats": {"entries": len(raw), "unique": unique, "duplicates_merged": len(raw) - unique},
    }


_ROLE = re.compile(r"^\s*(Drivers|Managers|Timesheets)\s*:\s*", re.I)
_STEP = re.compile(r"^\s*Step\s+\d+\s*:", re.I)


def _article_group(title: str) -> str:
    if m := _ROLE.match(title):
        return "Drivers" if m.group(1).lower() == "drivers" else "Managers"
    return "Getting started" if _STEP.match(title) else "General"


def _article_text(body: str) -> str:
    text = body.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"^[ .\t]*\.[ .\t]*$", "", text, flags=re.M)  # ". ." left where images were
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_articles(data_dir: Path) -> dict:
    raw = _read_sources(data_dir, "articles.json", "Article Jsons")
    groups = _Groups()
    for a in raw:
        meta = a.get("metadata") or {}
        groups.add(a, [_link_key(meta.get("html_url")), f"id:{a.get('id')}", f"title:{_title_key(a.get('title'))}"])

    items = []
    for g in groups.merged():
        a = g[0]
        meta = a.get("metadata") or {}
        text = _article_text(a.get("body") or "")
        flat = re.sub(r"\s+", " ", text)
        title = re.sub(r"\s+", " ", a["title"].replace("\xa0", " ")).strip()
        images, seen = [], set()
        for img in (x for item in g for x in (item.get("images") or [])):
            if img.get("src") and img["src"] not in seen:
                seen.add(img["src"])
                images.append({"src": img["src"], "alt": img.get("alt") or ""})
        items.append({
            "id": str(a["id"]),
            "title": title,
            "group": _article_group(title),
            "url": meta.get("html_url"),
            "updated": (meta.get("updated_at") or "")[:10] or None,
            "excerpt": flat if len(flat) <= 220 else flat[:220].rsplit(" ", 1)[0] + "…",
            "text": text,
            "images": images,
        })
    items.sort(key=lambda x: x["title"].lower())
    return {"items": items, "stats": {"entries": len(raw), "unique": len(items), "duplicates_merged": len(raw) - len(items)}}


def load_release_notes(path: Path) -> list[dict]:
    notes = []
    for r in json.loads(path.read_text(encoding="utf-8")):
        steps = r.get("description") or []
        notes.append({
            "id": r["id"],
            "title": r["title"].strip(),
            "summary": r.get("summary", "").strip(),
            "platform": r.get("platform"),
            "date": _date(r.get("date_str"), r.get("timestamp")),
            "audiences": _audiences(r.get("display_option_tags")),
            "steps_html": steps if isinstance(steps, list) else [steps],
            "tutorial_url": _url(r.get("tutorial_url")),
            "video_url": _url(r.get("video_url")),
            "thumbnail_url": _url(r.get("thumbnail_url")),
        })
    notes.sort(key=lambda n: n["date"] or "", reverse=True)
    return notes


def find_postman_collection(data_dir: Path) -> Path | None:
    return next(iter(sorted(data_dir.glob("*postman_collection*.json"))), None)


def load_hub(data_dir: Path, include_account_tutorials: bool = False) -> dict:
    """Everything the Explore pages need. Missing files give empty sections, not errors."""
    hub: dict = {"content": {}, "api": None, "release_notes": []}
    if (p := data_dir / "beans_content.json").is_file():
        hub["content"] = json.loads(p.read_text(encoding="utf-8"))
    if p := find_postman_collection(data_dir):
        hub["api"] = load_api_reference(p)
    hub["tutorials"] = load_tutorials(data_dir, include_account_tutorials)
    hub["articles"] = load_articles(data_dir)
    if (p := data_dir / "release-notes.json").is_file():
        hub["release_notes"] = load_release_notes(p)
    return hub
