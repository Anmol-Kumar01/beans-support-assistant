# Models

Every model the bot and evals use is set in `.env` and read by one module, `app/core/config.py`. No other code names a model. Changing a role's provider or model is an `.env` change only. All roles run on free tiers, and no OpenAI key is needed anywhere.

## Which model serves which role

| Role | Default | Provider | Why |
|---|---|---|---|
| `answer`: final answers and tool calling | `openai/gpt-oss-20b` | Groq | Free tier, fast (probe ≈ 0.4–0.6 s), supports tool calling and JSON-schema output. |
| `small`: chunk context headers, memory summaries | `openai/gpt-oss-20b` | Groq | Same key and model as `answer`. It has its own settings so it can move to a cheaper model later. |
| `judge`: eval scoring | `gemini-3.5-flash` | Gemini (OpenAI-compatible endpoint) | A different model family from `answer`, which avoids self-grading bias. It has a free tier and follows the judge's JSON schemas. |
| Embeddings | `gemini-embedding-001`, 1536 dims | Gemini `/embeddings` | Free tier and the same key as the judge. 1536 dims fits pgvector's HNSW limit (2,000) without `halfvec`. |
| Reranker | `jina-reranker-v2-base-multilingual` | Jina `/rerank` | Free trial allowance with one request per query. Cohere `rerank-v3.5` is the alternative. |

Keyword search stays in PostgreSQL full-text search (`chunks.search_tsv`). There are no sparse vectors.

### Why `gemini-3.5-flash` and not `gemini-2.5-flash`

On 2026-09-30, Gemini still listed `gemini-2.5-flash` in `GET /models`. Chat requests to it failed with `404: This model models/gemini-2.5-flash is no longer available to new users`. `gemini-3.8-flash` was listed too, but returned `503 high demand` at the time. `gemini-flash-latest` works but moves over time, so eval runs made with it are hard to compare. That leaves `gemini-3.5-flash`: it is pinned and it works.

This is also why the startup check sends one tiny chat request per role instead of only checking the model list.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env        # then fill in the three keys below; .env is git-ignored
.venv/bin/python -m app.core.startup_check
```

Keys (all free):

| Key | Used by | Get one |
|---|---|---|
| Groq | `LLM_ANSWER_API_KEY`, `LLM_SMALL_API_KEY` | https://console.groq.com/keys |
| Google AI Studio | `LLM_JUDGE_API_KEY`, `EMBEDDING_API_KEY` | https://aistudio.google.com/apikey |
| Jina | `RERANKER_API_KEY` | https://jina.ai/api-dashboard |

`LANGSMITH_API_KEY` is only needed to export the current bot's traces (`python -m evals export-langsmith`).

### Startup check

```bash
.venv/bin/python -m app.core.startup_check              # everything (≈ 5 small requests)
.venv/bin/python -m app.core.startup_check --roles judge --skip-retrieval
.venv/bin/python -m app.core.startup_check --no-probe   # list models only, no chat request
```

The check covers each part:
- **Each LLM role:** the key is set, `GET /models` answers and lists the model (it suggests close matches if not), and one chat request succeeds.
- **Embeddings:** one text returns `EMBEDDING_DIM` dims.
- **Reranker:** a relevant passage outranks an irrelevant one.
- **Judge vs. answer:** it warns if the judge and `answer` are the same model.

`python -m evals run` checks the judge the same way before it starts. The API server runs the full check at startup when `BOT_STARTUP_CHECK=true`; it is off while the server only proxies the current bot.

## Free-tier limits

- **Retries:** 429, 5xx and network errors are retried with exponential backoff and jitter, honouring `Retry-After` (`*_MAX_RETRIES`, default 6). This applies to every role, embeddings and the reranker (`app/llm/retry.py`).
- **Quota windows:** a `Retry-After` longer than `*_BACKOFF_MAX_S` (default 60 s) means a quota window rather than a burst. The call then fails immediately with a message instead of waiting.
- **Pacing:** `*_MAX_REQUESTS_PER_MINUTE` spaces requests. Clients on the same host with the same key share one budget at the lowest configured rate. This matters because the judge and embeddings share one Google free-tier quota.
- **Embedding batches:** requests carry up to `EMBEDDING_BATCH_SIZE` texts (default 100, Gemini's maximum), so ingestion uses few requests.
- **Rerank size:** reranking sends at most `RERANKER_CANDIDATES` (default 20) candidates per query.
- **Eval concurrency:** `EVAL_CONCURRENCY` defaults to 1. Each question makes up to 3 judge calls.
- **Cost:** reported as N/A unless `EVAL_MODEL_PRICES` is set for a paid model.

## Thinking and structured output

- **Thinking:** `LLM_<ROLE>_THINKING` is off by default for `answer` and `small` (latency) and on at low effort for the judge. How the flag is sent depends on the provider, set by `LLM_<ROLE>_THINKING_CONTROL`:
  - `reasoning_effort`: Groq, Gemini, OpenAI, Ollama. gpt-oss cannot turn reasoning off, so "off" sends `low`. Set `REASONING_EFFORT_OFF=none` for Qwen3 on Groq.
  - `chat_template_kwargs`: vLLM and SGLang (`enable_thinking`).
  - `prompt_switch`: appends `/think` or `/no_think`. Works for Qwen3 anywhere.
  - `none`: sends nothing.

  `<think>` blocks are stripped from replies either way.
- **Structured output:** `LLM_<ROLE>_JSON_MODE=json_schema` by default. `json_object` or `prompt` are fallbacks for providers without schema support. Replies are validated with Pydantic, and the model gets one repair attempt.

## Switching a role to another provider

Change the role's three lines in `.env`, then run the startup check. For example:

```bash
# Answer on a paid provider
LLM_ANSWER_BASE_URL=https://api.openai.com/v1
LLM_ANSWER_MODEL=<model id>
LLM_ANSWER_API_KEY=sk-...

# Judge on Groq's larger model (same family as gpt-oss-20b answers, so weaker than Gemini here)
LLM_JUDGE_BASE_URL=https://api.groq.com/openai/v1
LLM_JUDGE_MODEL=openai/gpt-oss-120b

# Cohere reranker
RERANKER_PROVIDER=cohere
RERANKER_BASE_URL=https://api.cohere.com/v2
RERANKER_MODEL=rerank-v3.5
```

Changing the embedding model or `EMBEDDING_DIM` changes `embedding_config` (for example `gemini-embedding-001-1536`). That needs a new partial HNSW index and a re-embed (`app/db/migrations/001_initial_schema.sql`); the old vectors stay until the eval set shows the new ones are better. Set `EVAL_MODEL_PRICES` when a role moves to a paid model, so cost is reported.

More presets (Cerebras, Qwen3 on Groq, vLLM) are in `.env.example`. Model IDs change often: the startup check shows close matches when a configured model isn't offered.

## Self-hosting on a machine with a GPU

Ollama and vLLM expose the same OpenAI-compatible API, so nothing changes but `.env`. A local base URL needs no key.

```bash
ollama pull qwen3:8b          # answer/small; qwen3:4b for CPU-only machines
ollama pull qwen3:14b         # judge on 16 GB+ GPUs (or gpt-oss:20b)
LLM_ANSWER_BASE_URL=http://localhost:11434/v1
LLM_ANSWER_MODEL=qwen3:8b
LLM_ANSWER_THINKING_CONTROL=prompt_switch
```

Keep embeddings and reranking hosted unless you have a GPU. On this project's 14-core CPU machine, a local cross-encoder took about 37 s to rerank 20 candidates, far over the 300 ms budget in Section 19.

## What every run records

- **Eval runs:** `manifest.json → models` records the model for each role. That covers the configured roles (URL, model, thinking, embedding size), the judge used, the models the target reported, and whether the judge graded its own model's answers. Each judge verdict also stores the model that produced it.
- **The new bot:** it reports `debug.models` in its final SSE event (contract in `evals/targets/new_bot.py`).
- **Embeddings:** every vector is stored with `embedding_config`, `embedding_model`, `embedding_version` (as reported by the API) and `dimensions`.
