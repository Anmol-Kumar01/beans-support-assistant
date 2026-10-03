"""Target that scores the current Node.js bot from exported LangSmith traces, without
calling it (so the baseline needs no OpenAI key and no running bot).

Each request to the bot produces two root runs (beans-support-bot/RAG.js):
  1. the RAG chain (RunnableWithMessageHistory): inputs ``{"input": query}``, outputs
     ``{"answer": draft, "context": [12 retrieved documents with their Chroma ids]}``
  2. the citation pass (RunnableSequence): inputs ``{"query", "answer": draft, "metadata"}``,
     outputs the AI message with the final text the user saw
They are joined on (query, draft). The final text is scored exactly like the live target.
The retrieved documents in the context give recall@k (k <= 12) when the export has them.

Questions are matched by a ``trace:<run id>`` tag when present, otherwise by normalized,
PII-scrubbed question text (most recent request wins). Follow-ups need the tag, since the
same words mean different things in different conversations.
Export the file with ``python -m evals export-langsmith``.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evals.catalog import Catalog
from evals.schema import BotResponse, EvalQuestion, QuestionType, RetrievedItem
from evals.targets.base import Target
from evals.targets.legacy_node import build_legacy_response, legacy_id_to_document_id
from evals.tools.scrub_pii import Scrubber

_NON_WORD = re.compile(r"\W+")


def question_key(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def message_text(value) -> str | None:
    """Text of an AI message as LangSmith stores it: a string, {"content": ...},
    {"output": ...}, or a LangChain-serialized message {"kwargs": {"content": ...}}."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in value]
        return "".join(parts) or None
    if isinstance(value, dict):
        for key in ("content", "kwargs", "output", "text"):
            if key in value and (text := message_text(value[key])) is not None:
                return text
        if gens := value.get("generations"):
            return message_text(gens[0][0] if isinstance(gens[0], list) else gens[0])
    return None


def _run_model(run: dict) -> str | None:
    extra = run.get("extra") or {}
    meta = extra.get("metadata") or {}
    params = extra.get("invocation_params") or {}
    return meta.get("ls_model_name") or params.get("model") or params.get("model_name")


@dataclass
class TraceRecord:
    run_ids: set[str]
    question: str
    answer: str
    started_at: datetime | None
    total_ms: float
    context: list | None = None
    models: dict[str, str] = field(default_factory=dict)


def load_trace_records(path: Path, scrubber: Scrubber) -> list[TraceRecord]:
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    models_by_trace: dict[str, str] = {}
    for run in runs:
        if run.get("run_type") == "llm" and (model := _run_model(run)):
            models_by_trace.setdefault(str(run.get("trace_id") or run.get("id")), model)

    roots = [r for r in runs if not r.get("parent_run_id") and r.get("run_type") != "llm"]
    rag_by_key: dict[tuple[str, str], dict] = {}
    finals: list[dict] = []
    for run in roots:
        inputs, outputs = run.get("inputs") or {}, run.get("outputs") or {}
        if isinstance(inputs.get("query"), str) and isinstance(inputs.get("answer"), str):
            finals.append(run)
        elif isinstance(inputs.get("input"), str) and isinstance(outputs.get("answer"), str):
            rag_by_key[(question_key(inputs["input"]), outputs["answer"].strip())] = run

    records: list[TraceRecord] = []
    used: set[int] = set()

    def model_of(*rs: dict) -> dict[str, str]:
        for r in rs:
            if model := models_by_trace.get(str(r.get("trace_id") or r.get("id"))):
                return {"answer": model}
        return {}

    def record(rag: dict | None, final: dict | None) -> TraceRecord | None:
        first, last = rag or final, final or rag
        question = (first.get("inputs") or {}).get("input") or (first.get("inputs") or {}).get("query")
        if final is not None:
            answer = message_text(final.get("outputs"))
        else:
            answer = (rag.get("outputs") or {}).get("answer")
        if not question or answer is None:
            return None
        start, end = _time(first.get("start_time")), _time(last.get("end_time"))
        return TraceRecord(
            run_ids={str(x) for r in (rag, final) if r for x in (r.get("id"), r.get("trace_id")) if x},
            question=scrubber.scrub(question),
            answer=scrubber.scrub(answer),
            started_at=start,
            total_ms=(end - start).total_seconds() * 1000 if start and end else 0.0,
            context=(rag.get("outputs") or {}).get("context") if rag else None,
            models=model_of(*(r for r in (final, rag) if r)),
        )

    for final in finals:
        inputs = final["inputs"]
        rag = rag_by_key.get((question_key(inputs["query"]), inputs["answer"].strip()))
        if rag is not None:
            used.add(id(rag))
        if rec := record(rag, final):
            records.append(rec)
    for rag in rag_by_key.values():  # RAG runs whose citation pass is missing from the export
        if id(rag) not in used and (rec := record(rag, None)):
            records.append(rec)
    return records


def retrieved_from_context(context: list | None, catalog: Catalog) -> list[RetrievedItem] | None:
    if not context:
        return None
    ranked: list[RetrievedItem] = []
    seen: set[str] = set()
    for item in context:
        doc = item.get("kwargs", item) if isinstance(item, dict) else {}
        meta = doc.get("metadata") or {}
        doc_id = None
        if isinstance(doc.get("id"), str):
            doc_id = legacy_id_to_document_id(doc["id"], catalog)
        if doc_id is None:
            for value in meta.values():
                if isinstance(value, str) and value.startswith("http") and (ids := catalog.resolve_url(value)):
                    doc_id = ids[0]
                    break
        if doc_id is None and isinstance(meta.get("title"), str) and (ids := catalog.resolve_title(meta["title"])):
            doc_id = ids[0]
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            ranked.append(RetrievedItem(document_id=doc_id, rank=len(ranked) + 1))
    return ranked or None


class LegacyTracesTarget(Target):
    name = "legacy_traces"

    def __init__(self, path: Path, catalog: Catalog, max_evidence_chars: int, scrubber: Scrubber):
        self.path = path
        self.catalog = catalog
        self.max_evidence_chars = max_evidence_chars
        self.scrubber = scrubber
        self.records = load_trace_records(path, scrubber)
        self._by_id = {rid: r for r in self.records for rid in r.run_ids}
        self._by_key: dict[str, TraceRecord] = {}
        oldest = datetime.min.replace(tzinfo=None)
        for r in sorted(self.records, key=lambda r: (r.started_at.replace(tzinfo=None) if r.started_at else oldest)):
            self._by_key[question_key(r.question)] = r  # later requests overwrite earlier ones

    def describe(self) -> dict:
        return {
            "target": self.name,
            "traces_file": str(self.path),
            "requests": len(self.records),
            "catalog_docs": len(self.catalog),
        }

    def find(self, question: EvalQuestion) -> TraceRecord | None:
        for tag in question.tags:
            if tag.startswith("trace:") and (rec := self._by_id.get(tag.removeprefix("trace:"))):
                return rec
        if question.question_type == QuestionType.FOLLOW_UP:
            return None
        return self._by_key.get(question_key(self.scrubber.scrub(question.question)))

    async def answer(self, question: EvalQuestion, session_id: str) -> BotResponse:
        rec = self.find(question)
        if rec is None:
            need = "a trace:<run id> tag" if question.question_type == QuestionType.FOLLOW_UP else "a matching trace"
            return BotResponse(error=f"no exported LangSmith trace for this question (needs {need})")
        return build_legacy_response(
            rec.answer, self.catalog, self.max_evidence_chars,
            retrieved=retrieved_from_context(rec.context, self.catalog),
            total_ms=rec.total_ms,
            raw={"trace_run_ids": sorted(rec.run_ids)},
            models=rec.models or None,
        )
