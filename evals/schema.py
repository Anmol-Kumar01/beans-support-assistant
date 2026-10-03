"""Data models for the evaluation set, bot responses, and scored results.

Document IDs use the form ``{source_type}:{external_id}``, e.g. ``zendesk:10022296840087``,
``youtube:WjYQ9b751ns``, ``release_note:creating-preset-filters``,
``trainn:adding-driver-beansroute``. The same IDs are used by the catalog (evals/catalog.py)
and by ``source_documents.document_id`` in the database, so both share one label set.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DOCUMENT_ID_PREFIXES = ("zendesk:", "youtube:", "release_note:", "trainn:")


class QuestionType(StrEnum):
    """Section 22 categories, plus general conversation for the no-tool route (Section 11)."""

    SIMPLE = "simple"
    DIFFICULT = "difficult"
    FOLLOW_UP = "follow_up"
    EXACT_TERMINOLOGY = "exact_terminology"
    MULTI_SOURCE = "multi_source"
    STRUCTURED_DATA = "structured_data"
    MIXED_DATA_DOCS = "mixed_data_docs"
    UNANSWERABLE = "unanswerable"
    OUT_OF_SCOPE = "out_of_scope"
    ADVERSARIAL = "adversarial"
    GENERAL_CONVERSATION = "general_conversation"


class ExpectedBehavior(StrEnum):
    ANSWER = "answer"  # answer from evidence, with citations
    NOT_FOUND = "not_found"  # say it wasn't found and offer escalation (Section 13)
    DECLINE = "decline"  # out of scope: decline and restate what the bot can do
    RESIST = "resist"  # adversarial: must not follow injected instructions or leak data
    CHAT = "chat"  # greeting/small talk: reply directly, no sources needed


DEFAULT_BEHAVIOR: dict[QuestionType, ExpectedBehavior] = {
    QuestionType.UNANSWERABLE: ExpectedBehavior.NOT_FOUND,
    QuestionType.OUT_OF_SCOPE: ExpectedBehavior.DECLINE,
    QuestionType.ADVERSARIAL: ExpectedBehavior.RESIST,
    QuestionType.GENERAL_CONVERSATION: ExpectedBehavior.CHAT,
}

Origin = Literal["bot_log", "trace", "support_ticket", "feedback", "synthetic"]


class EvalQuestion(BaseModel):
    id: str
    question: str
    question_type: QuestionType
    # Earlier user messages, replayed in the same session before ``question`` (follow-ups).
    prior_turns: list[str] = Field(default_factory=list)
    expected_behavior: ExpectedBehavior | None = None
    # Document-level labels. Any one of them counts as a retrieval hit; recall uses all.
    expected_source_ids: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    # Tool-routing label (Section 11). None = not labelled; [] = no tool expected.
    expected_tools: list[str] | None = None
    # Named test identity (tenant + role) the runner authenticates as. Pending the tenant
    # model decision (Section 27).
    persona: str | None = None
    origin: Origin = "synthetic"
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    # False while a draft awaits human labels; drafts are skipped by the runner.
    labelled: bool = True

    @model_validator(mode="after")
    def _check(self) -> "EvalQuestion":
        if self.expected_behavior is None:
            self.expected_behavior = DEFAULT_BEHAVIOR.get(self.question_type, ExpectedBehavior.ANSWER)
        if self.question_type == QuestionType.FOLLOW_UP and not self.prior_turns:
            raise ValueError(f"{self.id}: follow_up questions need prior_turns")
        for sid in self.expected_source_ids:
            if not sid.startswith(DOCUMENT_ID_PREFIXES):
                raise ValueError(f"{self.id}: bad source id {sid!r}")
        if not self.labelled:
            return self
        if self.expected_behavior == ExpectedBehavior.ANSWER:
            is_data = self.question_type == QuestionType.STRUCTURED_DATA
            if not self.reference_answer:
                raise ValueError(f"{self.id}: answerable questions need reference_answer")
            if not is_data and not self.expected_source_ids:
                raise ValueError(f"{self.id}: knowledge questions need expected_source_ids")
        elif self.expected_source_ids and self.expected_behavior != ExpectedBehavior.RESIST:
            raise ValueError(
                f"{self.id}: {self.expected_behavior} questions must not list expected sources"
            )
        return self


def load_questions(path: Path, include_drafts: bool = False) -> list[EvalQuestion]:
    """Load a JSONL dataset. Duplicate IDs are an error."""
    questions: list[EvalQuestion] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            q = EvalQuestion.model_validate(json.loads(line))
        except Exception as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from exc
        if q.id in seen:
            raise ValueError(f"{path}:{lineno}: duplicate id {q.id}")
        seen.add(q.id)
        if q.labelled or include_drafts:
            questions.append(q)
    return questions


# ---------------------------------------------------------------------------
# Bot responses (normalized across targets)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A source shown to the user. ``document_ids`` has several entries when a link or
    title matches more than one catalog document (e.g. shared Trainn URLs)."""

    n: int | None = None
    document_ids: list[str] = Field(default_factory=list)
    title: str | None = None
    url: str | None = None
    # False when the marker or link does not match anything the bot actually had.
    valid: bool = True


class RetrievedItem(BaseModel):
    document_id: str
    chunk_id: str | None = None
    rank: int
    score: float | None = None


class EvidenceItem(BaseModel):
    """Text a citation points to; used by the groundedness judge."""

    label: str  # e.g. "[1]" or the document title
    document_id: str | None = None
    text: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


EvidenceStatus = Literal["found", "partial", "not_found"]


class BotResponse(BaseModel):
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    # None = the target cannot report retrieval (metric shown as N/A, not 0).
    retrieved: list[RetrievedItem] | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_status: EvidenceStatus | None = None
    tool_calls: list[str] | None = None
    ttft_ms: float | None = None
    total_ms: float = 0.0
    stage_ms: dict[str, float] = Field(default_factory=dict)
    usage: Usage | None = None
    # Model per role as reported by the target (e.g. {"answer": "gpt-4o-mini"}), so runs of
    # different model setups can be compared. None = the target does not report it.
    models: dict[str, str] | None = None
    error: str | None = None
    raw: dict | str | None = None


# ---------------------------------------------------------------------------
# Scored results
# ---------------------------------------------------------------------------


class ObservedBehavior(StrEnum):
    ANSWERED = "answered"
    NOT_FOUND = "not_found"
    DECLINED = "declined"
    CHAT = "chat"
    COMPLIED_WITH_INJECTION = "complied_with_injection"


class QuestionScores(BaseModel):
    """Per-question metric values. None = not applicable to this question or target."""

    hit_at_k: dict[int, float] = Field(default_factory=dict)
    recall_at_k: dict[int, float] = Field(default_factory=dict)
    mrr: float | None = None
    cited_source_hit: float | None = None  # any cited doc is an expected doc
    citation_validity: float | None = None  # share of citations that resolve
    uncited_answer: bool | None = None
    correctness: float | None = None  # 0, 0.5, 1 from the judge
    groundedness: float | None = None  # supported claims / claims
    citation_precision: float | None = None  # citations that support their claim / citations
    observed_behavior: ObservedBehavior | None = None
    behavior_correct: bool | None = None
    routing_correct: bool | None = None


class JudgeTrace(BaseModel):
    name: str
    output: dict
    model: str | None = None  # model that produced the verdict, as reported by the API
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class QuestionResult(BaseModel):
    question: EvalQuestion
    response: BotResponse
    scores: QuestionScores
    judge: list[JudgeTrace] = Field(default_factory=list)
