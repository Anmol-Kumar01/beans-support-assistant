"""Redact personal data from eval-set text before it is stored or sent to hosted LLM APIs.

Redacted: emails, phone numbers, account IDs (hex BUIDs, UUIDs), long numeric IDs
(tracking or account numbers), every name in the optional names file, and names introduced
by phrases like "my name is". URLs are kept: they identify knowledge sources.

Free-standing names cannot be found reliably with patterns. ``review_candidates`` flags
capitalized words that are not in the knowledge-base vocabulary, and the question gets a
``pii:review`` tag so a person checks it before labelling. Candidates are printed, never
written to the dataset.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path

_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(
    r"(?<![\w/])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]?\d{4}(?!\w)"
    r"|(?<![\w/])\+\d[\d\s().-]{7,}\d(?!\w)"
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Account BUIDs are 8 or 32 hex characters; require a digit and a letter so words are safe.
_HEX_ID = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b", re.I)
_LONG_NUMBER = re.compile(r"(?<![\w.])\d{7,}(?![\w.])")
_INTRO = re.compile(
    r"(\b(?i:my name is|i am|i'm|this is|name:|regards,|thanks,|thank you,|signed,)\s+)"
    r"([A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+)?)"
)
_CAPITALIZED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z'’-]{2,}\b")
_WORD = re.compile(r"[a-z][a-z'’-]+")

# Words that follow "I am" / "this is" and are not names, on top of the KB vocabulary.
_COMMON = {
    "a", "an", "the", "not", "new", "having", "trying", "looking", "getting", "unable", "able",
    "still", "just", "also", "very", "so", "here", "there", "it", "that", "this", "what", "how",
    "why", "when", "where", "who", "manager", "driver", "dispatcher", "admin", "owner", "user",
    "hello", "hi", "hey", "thanks", "thank", "please", "sorry", "okay", "ok", "good", "great",
    "beans", "route", "fedex", "amazon", "ups", "usps", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december", "can", "could",
}


class Scrubber:
    def __init__(self, names: Iterable[str] = (), vocabulary: set[str] | None = None):
        names = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)
        self._names = [re.compile(rf"\b{re.escape(n)}\b", re.I) for n in names]
        self._vocab = {w.lower() for w in (vocabulary or set())} | _COMMON

    @classmethod
    def from_files(cls, names_file: Path | None = None, vocabulary: set[str] | None = None) -> "Scrubber":
        names: list[str] = []
        if names_file:
            names = [line for line in names_file.read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.startswith("#")]
        return cls(names, vocabulary)

    def _known_word(self, word: str) -> bool:
        return all(part.lower() in self._vocab for part in word.split())

    def scrub(self, text: str | None) -> str | None:
        if not text:
            return text
        urls: list[str] = []
        out = _URL.sub(lambda m: f"\x00{urls.append(m.group(0)) or len(urls) - 1}\x00", text)
        out = _EMAIL.sub("[EMAIL]", out)
        out = _UUID.sub("[ACCOUNT_ID]", out)
        out = _HEX_ID.sub("[ACCOUNT_ID]", out)
        out = _PHONE.sub("[PHONE]", out)
        out = _LONG_NUMBER.sub("[ID]", out)
        for pattern in self._names:
            out = pattern.sub("[NAME]", out)
        out = _INTRO.sub(lambda m: m.group(0) if self._known_word(m.group(2)) else f"{m.group(1)}[NAME]", out)
        return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], out)

    def contains_pii(self, text: str | None) -> bool:
        return bool(text) and self.scrub(text) != text

    def review_candidates(self, text: str | None) -> list[str]:
        """Capitalized words outside the KB vocabulary that may be names."""
        if not text:
            return []
        text = _URL.sub(" ", text)
        return sorted({w for w in _CAPITALIZED.findall(text) if not self._known_word(w)})


def vocabulary_from_texts(texts: Iterable[str]) -> set[str]:
    vocab: set[str] = set()
    for t in texts:
        vocab.update(_WORD.findall(t.lower()))
    return vocab


# Fields sent to hosted APIs. ``notes`` is for labellers only (never sent) and keeps the
# log session ID so a question can be traced back; drafts scrub its user text on creation.
_FIELDS = ("question", "reference_answer")


def question_texts(q: dict) -> list[str]:
    return [q[f] for f in _FIELDS if q.get(f)] + list(q.get("prior_turns") or [])


def scrub_question(q: dict, scrubber: Scrubber) -> tuple[dict, bool, list[str]]:
    """Return (scrubbed copy, whether anything was redacted, review candidates)."""
    out = dict(q)
    for f in _FIELDS:
        if out.get(f):
            out[f] = scrubber.scrub(out[f])
    if out.get("prior_turns"):
        out["prior_turns"] = [scrubber.scrub(t) for t in out["prior_turns"]]
    changed = any(out.get(f) != q.get(f) for f in (*_FIELDS, "prior_turns"))
    review = sorted({c for t in [out.get("question"), *(out.get("prior_turns") or [])] for c in scrubber.review_candidates(t)})
    tags = [t for t in out.get("tags", []) if not t.startswith("pii:")]
    if changed:
        tags.append("pii:scrubbed")
    if review:
        tags.append("pii:review")
    out["tags"] = tags
    return out, changed, review


def scrub_file(src: Path, dst: Path, scrubber: Scrubber) -> list[tuple[str, bool, list[str]]]:
    """Scrub a JSONL dataset. Returns (id, changed, review candidates) per question."""
    lines, report = [], []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            lines.append(line)
            continue
        q, changed, review = scrub_question(json.loads(line), scrubber)
        report.append((q.get("id", "?"), changed, review))
        lines.append(json.dumps(q, ensure_ascii=False))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
