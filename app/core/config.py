"""App and model settings. Every tunable value lives here, overridable by env vars or ``.env``.

Model names appear only in this module. Switching a role to another provider or model is
an ``.env`` change: see ``.env.example`` and ``docs/models.md``.

LLM roles (each has its own ``LLM_<ROLE>_BASE_URL`` / ``_MODEL`` / ``_API_KEY``):
  answer  final answers and tool calling
  small   chunk context headers, memory summaries
  judge   eval scoring; should be a different model family from ``answer``
Retrieval models are hosted APIs too: embeddings (``EMBEDDING_*``, OpenAI-compatible
``/embeddings``) and the reranker (``RERANKER_*``, Jina or Cohere ``/rerank``).
All providers share one retry/backoff policy (app/llm/retry.py).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOT_", **_ENV)

    # --- Database (PostgreSQL + pgvector; set up with scripts/setup_db.sh) ---
    # The default matches the local Docker database that script creates. Local use only.
    database_url: str = Field(
        default="postgresql://beans:beans_local@localhost:5432/beans_bot",
        validation_alias=AliasChoices("DATABASE_URL", "BOT_DATABASE_URL"),
    )

    # --- Temporary backend: the current Node.js bot (until the Phase 2 pipeline exists) ---
    legacy_base_url: str = "http://localhost:3000"
    legacy_timeout_s: float = 90.0
    # Source JSON folders, used to turn the bot's links into numbered source cards.
    legacy_sources_dir: Path = Path("../beans-support-bot")
    # Replies containing one of these are reported as evidence_status = "not_found".
    not_found_phrases: list[str] = [
        "i couldn't find this in the beans documentation",
        "i don't know how to answer the query",
    ]

    # --- Chat API ---
    max_message_chars: int = 2000  # Section 15, abuse protection
    source_excerpt_chars: int = 240
    # Run the model startup check (app/core/startup_check.py) when the server starts.
    # Off while the server only proxies the current bot, which uses none of these models.
    startup_check: bool = False
    # Name shown in the UI until auth (Section 15) provides the signed-in user.
    user_name: str = "Guest"

    # --- Answer pipeline (app/rag, ingestion in app/ingest) ---
    # "rag" answers from the knowledge base in PostgreSQL; "legacy" proxies the old Node bot.
    chat_backend: Literal["rag", "legacy"] = "rag"
    # Training-video transcripts (the old bot's Video Jsons folder). Empty = no videos.
    video_sources_dir: Path | None = Path("../beans-support-bot/Video Jsons")
    rag_dense_k: int = 30          # Section 8: dense candidates
    rag_lexical_k: int = 30        # Section 8: full-text candidates
    rag_rrf_k: int = 60            # Reciprocal Rank Fusion constant
    rag_top_k: int = 6             # chunks given to the LLM
    # Below this reranker score a chunk is not used as evidence (Section 13).
    # Starting value: calibrate on the eval set's unanswerable questions.
    rag_min_rerank_score: float = 0.1
    rag_max_tool_calls: int = 3    # Section 11
    rag_history_turns: int = 6     # Section 14: earlier turns sent to the LLM
    # Include the eval-only `debug` block (retrieved chunks, timings, models) in replies.
    rag_return_debug: bool = True

    # --- Explore Beans pages (app/hub.py) ---
    data_sources_dir: Path = Path("data_sources")
    # Tutorials tied to specific customer accounts are hidden unless this is true.
    hub_include_account_tutorials: bool = False

    # --- Feedback (Section 17). Local JSONL until the feedback table exists. ---
    feedback_path: Path = Path("var/feedback.jsonl")
    # Recent answers kept in memory so feedback can be stored with the full question/answer.
    recent_messages_kept: int = 500


# ---------------------------------------------------------------------------
# LLM roles
# ---------------------------------------------------------------------------

# How the thinking flag reaches the model, which differs by provider:
#   reasoning_effort      send reasoning_effort (Groq, Gemini, OpenAI, Ollama)
#   chat_template_kwargs  send chat_template_kwargs.enable_thinking (vLLM, SGLang)
#   prompt_switch         append /think or /no_think to the system prompt (Qwen3 anywhere)
#   none                  send nothing (models without a switch)
ThinkingControl = Literal["reasoning_effort", "chat_template_kwargs", "prompt_switch", "none"]
# How structured output is requested: json_schema (strict schema), json_object (JSON
# mode + schema in the prompt), or prompt (schema in the prompt only).
JsonMode = Literal["json_schema", "json_object", "prompt"]


class ProviderSettings(BaseSettings):
    """A hosted model endpoint: where it is, which model, and how hard to retry."""

    model_config = SettingsConfigDict(**_ENV)

    base_url: str
    model: str
    api_key: SecretStr | None = None
    timeout_s: float = 60.0

    # Free-tier rate limits: retry 429/5xx/network errors with exponential backoff,
    # honouring Retry-After. A Retry-After above backoff_max_s (e.g. a daily quota) fails
    # at once instead of waiting.
    max_retries: int = 6
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    # Client-side pacing below the provider's per-minute limit. Empty = no pacing.
    # Clients that use the same host and key share one budget, at the lowest value set
    # (e.g. the judge and embeddings on one Google key).
    max_requests_per_minute: float | None = None

    @property
    def is_local(self) -> bool:
        return urlparse(self.base_url).hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class LLMRoleSettings(ProviderSettings):
    """One LLM role behind an OpenAI-compatible chat API."""

    thinking: bool = False
    thinking_control: ThinkingControl = "reasoning_effort"
    # reasoning_effort values sent when thinking is on / off. gpt-oss cannot turn reasoning
    # off, so "off" means "low" for it; Qwen3 on Groq and Gemini Flash accept "none".
    # An empty value sends nothing.
    reasoning_effort_on: str = "medium"
    reasoning_effort_off: str = "low"

    json_mode: JsonMode = "json_schema"
    temperature: float | None = 0.1
    max_output_tokens: int = 1024
    # Provider-specific request fields, as JSON, e.g. {"service_tier": "auto"}.
    extra_body: dict = {}


_GROQ = "https://api.groq.com/openai/v1"
_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"


class AnswerLLMSettings(LLMRoleSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_ANSWER_", **_ENV)
    base_url: str = _GROQ
    model: str = "openai/gpt-oss-20b"
    max_output_tokens: int = 1024


class SmallLLMSettings(LLMRoleSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_SMALL_", **_ENV)
    base_url: str = _GROQ
    model: str = "openai/gpt-oss-20b"
    max_output_tokens: int = 400


class JudgeLLMSettings(LLMRoleSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_JUDGE_", **_ENV)
    base_url: str = _GEMINI
    model: str = "gemini-3.5-flash"
    thinking: bool = True
    reasoning_effort_on: str = "low"
    reasoning_effort_off: str = "none"
    temperature: float | None = 0.0
    max_output_tokens: int = 4000


# ---------------------------------------------------------------------------
# Retrieval models (hosted)
# ---------------------------------------------------------------------------


class EmbeddingSettings(ProviderSettings):
    """Any OpenAI-compatible /embeddings endpoint (Gemini, OpenAI, Ollama, vLLM)."""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", **_ENV)

    provider: str = "gemini"  # label for manifests; every provider uses the same API
    base_url: str = _GEMINI
    model: str = "gemini-embedding-001"
    dim: int = 1536
    # Texts per request (Gemini accepts up to 100). Gemini's free tier also limits tokens
    # per minute: 100 chunks (~30K tokens) in one request hits it, so the defaults send
    # 20 chunks (~7K tokens) at most 4 times a minute. Raise both on a paid key.
    batch_size: int = 20
    # Pacing for bulk ingestion only (python -m app.ingest); chat queries are not paced.
    ingest_max_requests_per_minute: float | None = 4
    # Send `dimensions` (Matryoshka truncation). Off for models with a fixed size.
    send_dimensions: bool = True
    # L2-normalize vectors. Gemini only normalizes its full 3072-dim output.
    normalize: bool = True

    @property
    def config_name(self) -> str:
        """Key in chunk_embeddings.embedding_config, e.g. 'gemini-embedding-001-1536'."""
        return f"{self.model.rsplit('/', 1)[-1].lower()}-{self.dim}"


RerankProvider = Literal["jina", "cohere"]


class RerankerSettings(ProviderSettings):
    """A hosted rerank API. Jina and Cohere share the request/response shape."""

    model_config = SettingsConfigDict(env_prefix="RERANKER_", **_ENV)

    provider: RerankProvider = "jina"
    base_url: str = "https://api.jina.ai/v1"  # Cohere: https://api.cohere.com/v2
    model: str = "jina-reranker-v2-base-multilingual"  # Cohere: rerank-v3.5
    # Fused candidates scored per query. Section 8 starts at ~40; 20 halves rerank tokens.
    candidates: int = 20
    # Characters of each candidate sent (chunks are ~300-500 tokens plus the header).
    max_chars_per_document: int = 4000


def _model_key(name: str) -> str:
    """'openai/gpt-oss-20b', 'models/gpt-oss-20b' and 'GPT-OSS-20B' are the same model."""
    return name.rsplit("/", 1)[-1].lower()


class ModelSettings(BaseModel):
    answer: AnswerLLMSettings
    small: SmallLLMSettings
    judge: JudgeLLMSettings
    embedding: EmbeddingSettings
    reranker: RerankerSettings

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "ModelSettings":
        kw = {"_env_file": env_file}
        return cls(
            answer=AnswerLLMSettings(**kw),
            small=SmallLLMSettings(**kw),
            judge=JudgeLLMSettings(**kw),
            embedding=EmbeddingSettings(**kw),
            reranker=RerankerSettings(**kw),
        )

    def llm(self, role: str) -> LLMRoleSettings:
        return getattr(self, role)

    def self_grading(self) -> bool:
        """True when the judge would grade answers from its own model (Section 22 bias)."""
        return _model_key(self.judge.model) == _model_key(self.answer.model)

    def manifest(self) -> dict:
        """Which model serves each role, for eval runs and traces. Never includes keys."""
        roles = {
            role: {
                "base_url": s.base_url,
                "model": s.model,
                "thinking": s.thinking,
                "reasoning_effort": s.reasoning_effort_on if s.thinking else s.reasoning_effort_off,
            }
            for role in ("answer", "small", "judge")
            for s in [self.llm(role)]
        }
        roles["embedding"] = {
            "provider": self.embedding.provider,
            "base_url": self.embedding.base_url,
            "model": self.embedding.model,
            "dim": self.embedding.dim,
            "config": self.embedding.config_name,
        }
        roles["reranker"] = {
            "provider": self.reranker.provider,
            "base_url": self.reranker.base_url,
            "model": self.reranker.model,
            "candidates": self.reranker.candidates,
        }
        return roles


def same_model(a: str, b: str) -> bool:
    return _model_key(a) == _model_key(b)


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache
def get_model_settings() -> ModelSettings:
    return ModelSettings.from_env()
