"""Load every knowledge source into ``SourceDoc`` objects.

Sources (all deduplicated by app/hub.py where it applies):
  zendesk        help-center articles          data_sources/articles.json + Article Jsons/
  trainn         interactive tutorials         data_sources/tutorials.json + Tutorial Jsons/
  release_note   release notes                 data_sources/release-notes.json
  api_reference  Route API endpoints           data_sources/*postman_collection*.json
  beans_content  products, maps, pricing, …   data_sources/beans_content.json
  youtube        training-video transcripts    BOT_VIDEO_SOURCES_DIR (data_sources/Video Jsons)

document_id = '{source_type}:{external_id}', the IDs the eval set uses.
"""

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.hub import load_api_reference, find_postman_collection, load_articles, load_release_notes, load_tutorials

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")


@dataclass
class SourceDoc:
    source_type: str
    external_id: str
    title: str
    url: str | None
    text: str = ""
    # For videos: [(start_seconds, text)] so chunks can carry a timestamp.
    segments: list[tuple[float, str]] | None = None
    updated_at: str | None = None
    tags: list[str] = field(default_factory=list)
    audience_tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def document_id(self) -> str:
        return f"{self.source_type}:{self.external_id}"

    def content_hash(self) -> str:
        body = self.text + "".join(f"{t}:{s}" for t, s in self.segments or [])
        return hashlib.sha256(f"{self.title}\n{self.url}\n{body}".encode()).hexdigest()


def html_to_text(value: str) -> str:
    value = re.sub(r"<(br|/p|/li|/h\d|/figure)\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<li[^>]*>", "\n- ", value, flags=re.I)
    text = html.unescape(_TAG.sub(" ", value))
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _articles(data_dir: Path) -> list[SourceDoc]:
    return [
        SourceDoc("zendesk", a["id"], a["title"], a["url"], a["text"], updated_at=a["updated"], tags=[a["group"]])
        for a in load_articles(data_dir)["items"]
    ]


def _tutorials(data_dir: Path, include_account_specific: bool) -> list[SourceDoc]:
    docs = []
    for t in load_tutorials(data_dir, include_account_specific)["items"]:
        lines = [f"Interactive tutorial for the {t['platform']} ({t['category']})."]
        if t["audiences"]:
            lines.append(f"For: {', '.join(t['audiences'])}.")
        if t["summary"]:
            lines.append(t["summary"])
        docs.append(SourceDoc("trainn", t["id"], t["title"], t["url"], "\n".join(lines),
                              updated_at=t["date"], tags=[t["platform"], t["category"]], audience_tags=t["audiences"]))
    return docs


def _release_notes(data_dir: Path) -> list[SourceDoc]:
    path = data_dir / "release-notes.json"
    if not path.is_file():
        return []
    docs = []
    for n in load_release_notes(path):
        steps = "\n\n".join(html_to_text(h) for h in n["steps_html"])
        text = f"Release note ({n['date'] or 'undated'}, {n['platform'] or 'Beans Route'}).\n{n['summary']}\n\n{steps}".strip()
        docs.append(SourceDoc("release_note", n["id"], n["title"], n["tutorial_url"], text,
                              updated_at=n["date"], tags=[n["platform"] or ""], audience_tags=n["audiences"]))
    return docs


def _api_reference(data_dir: Path) -> list[SourceDoc]:
    path = find_postman_collection(data_dir)
    if path is None:
        return []
    api = load_api_reference(path)
    docs = [SourceDoc("api_reference", "overview", f"{api['name']}: getting started", None,
                      f"Base URL: {api['base_url']}\n\n{api['description']}")]
    for g in api["groups"]:
        if g["description"]:
            docs.append(SourceDoc("api_reference", g["id"], f"Route API: {g['name']}", None, g["description"], tags=[g["name"]]))
        for e in g["endpoints"]:
            parts = [f"{e['method']} {e['path']}", f"Full URL: {e['url']}", e["description"]]
            if e["query"]:
                parts.append("Query parameters:\n" + "\n".join(f"- {q['key']} (example {q['value']}): {q['description']}" for q in e["query"]))
            if e["headers"]:
                parts.append("Headers: " + ", ".join(f"{h['key']}: {h['value']}" for h in e["headers"]))
            if e["body"]:
                parts.append(f"Example request body:\n{e['body']}")
            docs.append(SourceDoc("api_reference", e["id"], f"Route API: {e['name']}", None, "\n\n".join(p for p in parts if p),
                                  tags=[g["name"], e["method"]]))
    return docs


def _bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items)


def _beans_content(data_dir: Path) -> list[SourceDoc]:
    """One document per section of beans_content.json, written as plain prose for search."""
    path = data_dir / "beans_content.json"
    if not path.is_file():
        return []
    c = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    add = lambda key, title, url, text: docs.append(SourceDoc("beans_content", key, title, url, text.strip()))  # noqa: E731

    for p in c.get("products", []):
        text = [f"{p['name']}: {p.get('tagline', '')}.", p.get("text", "")]
        if p.get("for"):
            text.append("Built for: " + ", ".join(p["for"]) + ".")
        if p.get("features"):
            text.append("Key features:\n" + _bullets(f"{f['name']}: {f['text']}" for f in p["features"]))
        if p.get("stats"):
            text.append("Stats: " + "; ".join(f"{s['value']} {s['label']}" for s in p["stats"]) + ".")
        if p.get("results"):
            text.append("Customer results: " + "; ".join(p["results"]) + ".")
        if p.get("awards"):
            text.append("Awards: " + "; ".join(p["awards"]) + ".")
        add(f"product-{p['id']}", f"About {p['name']}", p.get("url"), "\n\n".join(text))

    if c.get("demo_url") or c.get("contacts"):
        k = c.get("contacts", {})
        add("contact", "Contact Beans and book a demo", c.get("demo_url"),
            f"Book a demo: {c.get('demo_url')}\nSales: {k.get('sales')}\nSupport: {k.get('support')}\n"
            f"Direct: {k.get('direct')}\nPhone: {k.get('phone')}")

    if w := c.get("why_beans"):
        rows = _bullets(f"{r['feature']}: Beans.ai — {r['beans']}; others — {r['others']}" for r in w.get("rows", []))
        points = "\n\n".join(f"{p['title']}: {p['text']}" for p in w.get("points", []))
        add("why-beans", "Why Beans.ai vs other APIs", None, f"How Beans compares with other mapping and routing APIs:\n{rows}\n\n{points}")

    if c.get("usage") or c.get("trusted_by"):
        usage = _bullets(f"{u['name']}: {u['text']}" for u in c.get("usage", []))
        add("customers", "Beans customers and real-world usage", None,
            f"Real-world usage:\n{usage}\n\nTrusted by: {', '.join(c.get('trusted_by', []))}.")

    if c.get("apis"):
        apis = _bullets(f"{a['name']} {a['version']}: {a.get('text') or ', '.join(a.get('includes', []))} Docs: {a['url']}" for a in c["apis"])
        dev = c.get("developer_tutorials", {})
        tuts = _bullets(f"{t['name']}: {t['url']}" for t in dev.get("items", []))
        add("apis", "Beans APIs and developer tutorials", dev.get("url"), f"Beans APIs:\n{apis}\n\nDeveloper tutorials ({dev.get('url')}):\n{tuts}")

    if c.get("maps_url"):
        add("maps", "Beans Maps", c["maps_url"], f"Beans 3D Maps: {c['maps_url']}. Maps Enterprise API and Maps Web Widget are listed under Beans APIs.")

    if t := c.get("traffic"):
        opts = "\n\n".join(
            f"{o['name']} ({o['tagline']}): {o['text']} Best for: {', '.join(o['best_for'])}. Example: {o['example']} {o['ready']}"
            for o in t.get("options", []))
        phases = _bullets(f"{p['phase']}: {p['traffic']} — {p['benefit']}" for p in t.get("phases", []))
        plans = _bullets(f"{p['name']} ({p['includes']}): {p['price']}" for p in t.get("plans", []))
        add("traffic", "Distance matrix, predictive and real-time traffic", None,
            f"{t.get('intro', '')}\n\n{opts}\n\nWhich traffic when:\n{phases}\n\nMix and match by: {', '.join(t.get('mix', []))}.\n\n"
            f"Traffic plans and pricing:\n{plans}\n{t.get('plans_note', '')}")

    if m := c.get("maps_plans"):
        core = m["core"]
        addons = "\n\n".join(f"Add-on {a['n']} {a['name']} ({a['price']}{', ' + a['price_note'] if a.get('price_note') else ''}): "
                             f"{a['text']} Includes: {'; '.join(a['features'])}." for a in m.get("addons", []))
        u = m.get("ultimate", {})
        add("maps-pricing", "Beans Maps pricing (multifamily 3D maps)", c.get("maps_url"),
            f"{m.get('title', '')}\n\nCore — {core['name']} ({core['price']}): {core['text']} Includes: {'; '.join(core['features'])}.\n\n"
            f"{addons}\n\nUltimate ({u.get('price_was', '')} now {u.get('price', '')}): {u.get('text', '')} Includes: {', '.join(u.get('includes', []))}.")

    if a := c.get("api_plans"):
        plans = _bullets(f"{p['name']}: {p['text']}. {p['price']}, {p['qps']}, {p['calls']}" for p in a.get("plans", []))
        u = a.get("ultimate", {})
        add("api-pricing", "Address API pricing", a.get("url"),
            f"{a.get('title', '')}\n{plans}\n- {u.get('name')}: {u.get('text')}. {u.get('price')}, {u.get('calls')}")

    if s := c.get("servicenow"):
        steps = "\n".join(f"{i}. {x['title']}: {x['text']}" for i, x in enumerate(s.get("steps", []), 1))
        benefits = _bullets(f"{b['title']}: {b['text']}" for b in s.get("benefits", []))
        pricing = _bullets(f"{p['name']}: {p['example']}" for p in s.get("pricing", []))
        switching = _bullets(f"{x['title']} {x['text']}" for x in s.get("switching", []))
        add("servicenow", "Beans.ai + ServiceNow FSM integration", None,
            f"{s.get('tagline', '')} {s.get('intro', '')}\n{s.get('availability', '')}\n\nRequirements: {'; '.join(s.get('requirements', []))}.\n\n"
            f"Getting started:\n{steps}\n\nWhy Beans.ai + ServiceNow: {', '.join(s.get('why', []))}.\n\nBenefits:\n{benefits}\n\n"
            f"Pricing:\n{pricing}\n{s.get('pricing_note', '')}\n\nSwitching:\n{switching}")
    return docs


def _clean_caption(text: str) -> str:
    return _WS.sub(" ", html.unescape(html.unescape(text)).replace("[Music]", " ")).strip()


def _videos(video_dir: Path | None) -> list[SourceDoc]:
    if not video_dir or not video_dir.is_dir():
        return []
    by_id: dict[str, SourceDoc] = {}
    for f in sorted(video_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        vid = str(d["id"]).split(" Part ")[0]
        segs = [(float(s.get("offset", 0)), _clean_caption(s.get("text", ""))) for s in d.get("transcript", [])]
        segs = [(t, s) for t, s in segs if s]
        if vid in by_id:  # "<id> Part 2" continues the same video's timeline
            by_id[vid].segments += segs
        else:
            title = re.sub(r"\s+Part\s+\d+$", "", d["title"].strip())
            by_id[vid] = SourceDoc("youtube", vid, title, f"https://www.youtube.com/watch?v={vid}", segments=segs)
    for doc in by_id.values():
        doc.segments.sort()
    return list(by_id.values())


def load_all(data_dir: Path, video_dir: Path | None, include_account_tutorials: bool) -> list[SourceDoc]:
    docs = (_articles(data_dir) + _tutorials(data_dir, include_account_tutorials) + _release_notes(data_dir)
            + _api_reference(data_dir) + _beans_content(data_dir) + _videos(video_dir))
    seen: set[str] = set()
    unique = []
    for d in docs:
        if d.document_id not in seen:
            seen.add(d.document_id)
            unique.append(d)
    return unique
