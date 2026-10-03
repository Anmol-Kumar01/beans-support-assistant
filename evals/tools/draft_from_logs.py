"""Extract user questions from the current bot's winston logs into an unlabelled draft set.

Drafts have ``labelled: false`` and are skipped by the runner until a person sets the
question type, expected sources, and reference answer. The bot's own answers are
deliberately not copied into drafts, so labels are not anchored on the baseline.
Personal data is scrubbed (evals/tools/scrub_pii.py) before anything is written.
"""

import json
import re
from pathlib import Path

from evals.tools.scrub_pii import Scrubber

_ENTRY_SPLIT = re.compile(r"^\{", re.M)
_MESSAGE = re.compile(r"^\s*message: '((?:[^'\\]|\\.)*)'", re.M)
_TIMESTAMP = re.compile(r"^\s*timestamp: '([^']+)'", re.M)
_GREETING = re.compile(r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|how are you)\b", re.I)


def redact(text: str, scrubber: Scrubber | None = None) -> str:
    return (scrubber or Scrubber()).scrub(text)


def parse_log(text: str) -> list[tuple[str, str]]:
    """Return (timestamp, question) pairs in file order."""
    out = []
    for entry in _ENTRY_SPLIT.split(text):
        m = _MESSAGE.search(entry)
        if not m or not m.group(1).startswith("Request: "):
            continue
        question = m.group(1).removeprefix("Request: ").replace("\\'", "'").strip()
        ts = _TIMESTAMP.search(entry)
        out.append((ts.group(1) if ts else "", question))
    return out


def draft_questions(log_dirs: list[Path], scrubber: Scrubber | None = None) -> list[dict]:
    scrubber = scrubber or Scrubber()
    drafts: list[dict] = []
    seen: set[str] = set()
    for log_dir in log_dirs:
        for path in sorted(log_dir.glob("*.log")):
            session = path.name.removesuffix("chatbot.log")
            history: list[str] = []
            for ts, question in sorted(parse_log(path.read_text(encoding="utf-8", errors="replace"))):
                q = redact(question, scrubber)
                key = re.sub(r"\W+", " ", q.lower()).strip()
                if key and key not in seen:
                    seen.add(key)
                    drafts.append({
                        "id": f"log-{len(drafts) + 1:04d}",
                        "question": q,
                        "question_type": "general_conversation" if _GREETING.match(q) else "simple",
                        "origin": "bot_log",
                        "labelled": False,
                        "tags": [f"logdir:{log_dir.name}"],
                        "notes": (
                            f"session {session} at {ts}. Type is a guess."
                            + (f" Earlier in session: {history[-3:]}" if history else "")
                        ),
                    })
                history.append(q)
    return drafts


def write_drafts(drafts: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in drafts), encoding="utf-8")
