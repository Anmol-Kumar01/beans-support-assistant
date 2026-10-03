"""Evaluation settings. Every tunable value lives here and can be overridden by env vars.

Env vars use the ``EVAL_`` prefix (e.g. ``EVAL_CONCURRENCY``). The judge model is not set
here: it is the ``judge`` LLM role in app/core/config.py (``LLM_JUDGE_*``).
Secrets are read from the environment only.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    input: float
    output: float


class MetricGate(BaseModel):
    """Release-gate rule for one metric (Section 22).

    ``target`` is the absolute goal; ``tolerance`` is how far a candidate may fall
    behind the baseline before the gate fails. ``higher_is_better`` flips the direction
    for latency and cost metrics.
    """

    target: float | None = None
    tolerance: float = 0.0
    higher_is_better: bool = True


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVAL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Current Node.js bot ---
    legacy_base_url: str = "http://localhost:3000"
    legacy_timeout_s: float = 90.0
    # Folder holding the legacy source JSONs (Article/Video/Release Notes/Tutorial Jsons).
    # Used to map cited links back to document IDs and to load cited text for grounding.
    legacy_sources_dir: Path = Path("data_sources")

    # --- New bot (Phase 2). The SSE contract is defined in evals/targets/new_bot.py ---
    new_bot_base_url: str = "http://localhost:8000"
    new_bot_chat_path: str = "/v1/chat/stream"
    new_bot_token: SecretStr | None = None
    new_bot_timeout_s: float = 60.0

    # --- Current bot, scored from exported LangSmith traces (no live calls, no OpenAI key) ---
    # Written by `python -m evals export-langsmith`; var/ is git-ignored (traces hold user text).
    legacy_traces_path: Path = Path("var/langsmith/legacy_runs.jsonl")
    langsmith_project: str | None = None
    # Read from .env too (the LangSmith SDK itself only looks at the process environment).
    langsmith_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LANGSMITH_API_KEY", "EVAL_LANGSMITH_API_KEY")
    )

    # --- PII scrub (golden-set text goes to free-tier APIs) ---
    # Optional file of known names (drivers, customers), one per line, always redacted.
    pii_names_file: Path | None = None

    # --- Runner ---
    # Questions scored in parallel. Each makes up to 3 judge calls, so keep this low on
    # free tiers; 429s are retried with backoff (LLM_JUDGE_MAX_RETRIES).
    concurrency: int = 1
    # Each question gets its own session ID so history never leaks between questions.
    session_prefix: str = "eval"
    reports_dir: Path = Path("evals/reports")

    # --- Judge (model and provider: LLM_JUDGE_* in app/core/config.py) ---
    # Cap on cited-document text sent to the groundedness judge (legacy bot cites whole docs).
    judge_max_evidence_chars_per_doc: int = 12000

    # USD per 1M tokens, keyed by model name. Empty by default: free tiers and local models
    # cost nothing, so cost is reported as N/A. For a paid provider set e.g.
    # EVAL_MODEL_PRICES='{"<model>": {"input": 0.1, "output": 0.5}}'.
    model_prices: dict[str, ModelPrice] = {}

    # --- Metrics ---
    retrieval_k_values: list[int] = [5, 10]
    # Phrases that mark a not-found reply when a target does not report evidence_status.
    # Includes the current bot's fallback sentence (RAG.js) and the new pattern (Section 13).
    not_found_phrases: list[str] = [
        "i couldn't find this in the beans documentation",
        "i don't know how to answer the query",
    ]
    # Question types that count toward the Phase 2 release gate (knowledge questions only).
    gated_question_types: list[str] = [
        "simple",
        "difficult",
        "follow_up",
        "exact_terminology",
        "multi_source",
        "unanswerable",
        "out_of_scope",
        "adversarial",
        "general_conversation",
    ]

    # Section 22 proposed targets. Tolerances are placeholders until agreed after the baseline.
    gates: dict[str, MetricGate] = {
        "retrieval_recall_at_10": MetricGate(target=0.90, tolerance=0.02),
        "answer_correctness": MetricGate(target=0.85, tolerance=0.02),
        "groundedness": MetricGate(target=0.95, tolerance=0.02),
        "citation_precision": MetricGate(target=0.95, tolerance=0.02),
        "not_found_accuracy": MetricGate(target=0.90, tolerance=0.02),
        "routing_accuracy": MetricGate(target=0.95, tolerance=0.02),
        "adversarial_pass_rate": MetricGate(target=1.0, tolerance=0.0),
        "ttft_p95_ms": MetricGate(target=2500, tolerance=200, higher_is_better=False),
        "cost_per_answer_usd": MetricGate(target=None, tolerance=0.002, higher_is_better=False),
    }


@lru_cache
def get_settings() -> EvalSettings:
    return EvalSettings()
