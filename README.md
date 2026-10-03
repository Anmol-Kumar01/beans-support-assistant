# Beans Support Assistant

A support assistant and knowledge hub for **Beans Route** and **Beans.ai**. Ask a question in plain English and it answers from Beans' own help articles, tutorials, training videos, release notes, API reference and product information, with numbered links to every source it used. The **Explore Beans** pages present the same content for browsing: products, APIs, maps, integrations, tutorials and release notes.

![Beans Assistant home screen](docs/images/beans-assistant.png)

```
Browser (React UI)
   │  POST /v1/chat/stream (streamed answer)      GET /v1/hub/* (Explore pages)
   ▼
FastAPI server (app/)
   ├─ answer model (Groq gpt-oss-20b) decides whether to search
   ├─ hybrid search in PostgreSQL: pgvector (meaning) + full-text (keywords) → RRF
   ├─ rerank (Jina) → evidence threshold → top chunks
   └─ streamed answer with [1] [2] citations, checked in code
PostgreSQL + pgvector (Docker)  ◄── python -m app.ingest ◄── data_sources/ + video transcripts
```

### Models used

| Step | What does it | Provider | Where it's set |
|---|---|---|---|
| **Chunking** | No model: rule-based splitting per source type (article steps, ~90-second video windows, one chunk per endpoint or tutorial), ~400 tokens per chunk | — | `app/ingest/chunking.py` |
| **Embeddings** (ingestion + each question) | `gemini-embedding-001`, 1536 dimensions | Google Gemini | `EMBEDDING_*` |
| **Keyword search** | No model: PostgreSQL full-text search | — | `app/rag/retrieval.py` |
| **Merging results** | No model: Reciprocal Rank Fusion (k = 60) | — | `BOT_RAG_RRF_K` |
| **Reranking** | `jina-reranker-v2-base-multilingual`, top 20 candidates (Cohere `rerank-v3.5` is the alternative) | Jina | `RERANKER_*` |
| **Deciding to search + writing the answer** | `openai/gpt-oss-20b`, with tool calling and streaming | Groq | `LLM_ANSWER_*` |
| **Small tasks** (chunk summaries, memory summaries) | `openai/gpt-oss-20b`. Configured, but not used yet: chunk headers are rule-based for now | Groq | `LLM_SMALL_*` |
| **Eval judge** (scoring answers in evals) | `gemini-3.5-flash`, a different model family from the answer model | Google Gemini | `LLM_JUDGE_*` |

All of these run on free tiers, and no OpenAI key is needed. To change any model, edit its lines in `.env` (see [Configuration](#configuration)). The reasons for each choice are in `docs/models.md`.

---

## Prerequisites

| Tool | Version | Check | Install (Ubuntu) |
|---|---|---|---|
| Git | any | `git --version` | `sudo apt install git` |
| Python | 3.12+ | `python3 --version` | `sudo apt install python3 python3-venv` |
| Node.js | 20+ (22 recommended) | `node --version` | [nvm](https://github.com/nvm-sh/nvm): `nvm install 22 && nvm use 22` |
| Docker | 24+ | `docker --version` | `sudo apt install docker.io`, then `sudo usermod -aG docker $USER` and log out and back in |

You also need three free API keys (step 4): **Groq**, **Google AI Studio** and **Jina**.

---

## Quick start (first time on a new machine)

Run every command from the project folder unless a step says otherwise. Altogether it takes about 15 minutes.

### 1. Get the code

```bash
git clone https://github.com/Anmol-Kumar01/beans-support-assistant.git
cd beans-support-assistant
```

All content, including the training-video transcripts (`data_sources/Video Jsons`), is in `data_sources/`.

### 2. Install the Python backend

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

### 3. Build the React frontend

```bash
cd frontend
npm install
npm run build        # creates frontend/dist, which the backend serves
cd ..
```

### 4. Add your API keys

```bash
cp .env.example .env
```

Open `.env` and replace the `your-…-key` placeholders:

| Lines in `.env` | Used for | Get a free key |
|---|---|---|
| `LLM_ANSWER_API_KEY`, `LLM_SMALL_API_KEY` | Writing answers (Groq) | https://console.groq.com/keys |
| `LLM_JUDGE_API_KEY`, `EMBEDDING_API_KEY` | Search embeddings and eval judging (Gemini). The same key works for both. | https://aistudio.google.com/apikey |
| `RERANKER_API_KEY` | Reranking search results (Jina) | https://jina.ai/api-dashboard |
| `BOT_USER_NAME` | *Optional:* your name in the sidebar | — |

`.env` is git-ignored. **Never put real keys in `.env.example`**, because that file is committed.

Check that every key and model works (about 5 tiny requests):

```bash
.venv/bin/python -m app.core.startup_check
```

Every line should say `ok`. If one says `FAIL`, the message names the setting to fix.

### 5. Start Docker

```bash
sudo systemctl start docker
sudo systemctl enable docker      # optional: start Docker automatically on boot
```

### 6. Create the database

```bash
./scripts/setup_db.sh
```

This starts PostgreSQL 17 with pgvector in a Docker container called `beans-bot-db`, waits until it's ready, and creates all the tables. The first run downloads the image (about 150 MB). It should end with `Done. Database: postgresql://beans:***@localhost:5432/beans_bot`.

### 7. Load the knowledge base

```bash
.venv/bin/python -m app.ingest
```

This reads `data_sources/` and the video transcripts (about 225 documents), splits them into about 450 chunks, embeds them with Gemini, and stores them in the database. On a free Gemini key it takes about **6 minutes**, because requests are paced to stay under the free-tier limit. If it stops with a quota message, wait a few minutes and run the same command again: it continues where it stopped.

### 8. Run the app

```bash
.venv/bin/uvicorn app.api.main:create_app --factory --port 8001
```

Open **http://localhost:8001** and ask, for example, "How do I use Lasso?". The answer streams in with numbered source cards underneath.

---

## Every day (after a reboot)

```bash
sudo systemctl start docker                                         # skip if enabled on boot
./scripts/setup_db.sh                                               # starts the database (safe to repeat)
.venv/bin/uvicorn app.api.main:create_app --factory --port 8001
```

## Updating the knowledge base

The files in `data_sources/` feed both the chat and the Explore pages. After adding or changing a file:

```bash
.venv/bin/python -m app.ingest        # only new or changed documents are re-embedded
```

Then restart the server. Duplicates are merged automatically:
- **Tutorials:** matched by access link (ignoring `/embed`, `?mode=…` and look-alike hyphens), or else by the same title on the same platform.
- **Articles:** matched by help-center link, or else by ID or title.

| File in `data_sources/` | Used for |
|---|---|
| `articles.json` + `Article Jsons/` | Chat, and Tutorials → Articles |
| `tutorials.json` + `Tutorial Jsons/` | Chat, and Tutorials → Interactive tutorials |
| `release-notes.json` | Chat, and Release Notes |
| `Beans Route API Collection*.postman_collection.json` | Chat, and APIs & Docs → Route API reference |
| `beans_content.json` | Chat, plus Overview, Maps & Traffic, Integrations, pricing and contacts. It's the structured version of `beans_data.text`; edit the JSON. |

Tutorials made only for specific customer accounts (`account_buids`) are left out. Set `BOT_HUB_INCLUDE_ACCOUNT_TUTORIALS=true` to include them.

## Development mode

To get hot reload while editing, run these in two terminals:

```bash
# Terminal 1: API (restarts on Python changes)
.venv/bin/uvicorn app.api.main:create_app --factory --port 8001 --reload

# Terminal 2: UI (reloads on React changes)
cd frontend && npm run dev
```

Open **http://localhost:5173**. The dev server forwards `/v1` calls to port 8001. Run `npm run build` before using the normal mode on port 8001 again.

## Command reference

| Command | What it does |
|---|---|
| `./scripts/setup_db.sh` | Start or create the database and apply new migrations (safe to repeat) |
| `./scripts/setup_db.sh --status` | Show the container and applied migrations |
| `./scripts/setup_db.sh --stop` | Stop the database; data is kept |
| `./scripts/setup_db.sh --reset` | **Delete** all data and start fresh (asks first) |
| `docker exec -it beans-bot-db psql -U beans -d beans_bot` | Open a SQL shell |
| `.venv/bin/python -m app.ingest` | Load new or changed content into the knowledge base |
| `.venv/bin/python -m app.ingest --dry-run` | Show what would change, without writing |
| `.venv/bin/python -m app.ingest --force` | Re-embed everything (after changing the embedding model) |
| `.venv/bin/python -m app.core.startup_check` | Check every API key and model |
| `.venv/bin/pytest` | Run the tests (no network calls) |
| `.venv/bin/python -m evals --help` | Eval tools (see "Evals" below) |

## Configuration

Every setting lives in `.env`. `.env.example` lists them all, with comments and provider presets.

| Group | Settings | Notes |
|---|---|---|
| Models | `LLM_ANSWER_*`, `LLM_SMALL_*`, `LLM_JUDGE_*`, `EMBEDDING_*`, `RERANKER_*` | To switch a model or provider, change its `*_BASE_URL`, `*_MODEL` and `*_API_KEY`, then run the startup check. See `docs/models.md`. |
| Database | `POSTGRES_*`, `DATABASE_URL` | The defaults match the Docker database the setup script creates. |
| Answers | `BOT_RAG_TOP_K`, `BOT_RAG_MIN_RERANK_SCORE`, … | Search and threshold tuning. |
| Evals | `EVAL_*` | Concurrency, trace export, PII scrubbing. |

**Changing the database schema:** add a new numbered file, for example `app/db/migrations/002_add_x.sql`, then run `./scripts/setup_db.sh`. Never edit a migration that has already been applied: the runner checks each file's checksum and stops if one changed.

## Project layout

```
app/
  api/main.py            FastAPI: UI, /v1/chat/stream, /v1/hub/*, /v1/sources, /v1/feedback, /v1/health
  core/config.py         every setting and model choice (from .env)
  core/startup_check.py  checks keys and models
  db/                    migrate.py + migrations/*.sql
  ingest/                python -m app.ingest: sources → chunks → embeddings → PostgreSQL
  rag/                   answer pipeline: search, rerank, prompts, cited streaming answers
  llm/                   one client for all LLM calls + shared retry and rate limiting
  retrieval/             embedding (Gemini) and reranker (Jina/Cohere) clients
  hub.py                 loads and deduplicates data_sources/ for the Explore pages
frontend/src/            React UI: App.jsx, components/, pages/, styles.css
data_sources/            Beans content (articles, tutorials, release notes, API collection, product info)
scripts/setup_db.sh      one-command database setup
evals/                   eval framework (golden set, metrics, judge, reports)
design/                  UI designs and logos
docs/models.md           which model serves which role, and why
```

## Evals

```bash
.venv/bin/python -m evals validate evals/datasets/golden.jsonl                  # check a dataset
.venv/bin/python -m evals scrub <file.jsonl>                                     # remove personal data first
.venv/bin/python -m evals run --dataset evals/datasets/golden.jsonl             # score this bot
.venv/bin/python -m evals compare evals/reports/<baseline> evals/reports/<candidate>
```

Start the server before `evals run`. It scores the server at `http://localhost:8001` (set `EVAL_SERVER_URL` to change it). Reports are written to `evals/reports/<run_id>/report.md`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Docker is installed but not running` | `sudo systemctl start docker`, then `./scripts/setup_db.sh` again. |
| `permission denied … docker.sock` | `sudo usermod -aG docker $USER`, then log out and back in. |
| `Port 5432 is already in use` | Another PostgreSQL is running. Set `POSTGRES_PORT=5433` and the matching `DATABASE_URL` in `.env`. |
| Chat says "Can't connect to the database" | Run `./scripts/setup_db.sh`, then restart the server. |
| Chat says "The knowledge base is empty" | Run `.venv/bin/python -m app.ingest`. |
| Ingest stops with "quota" or "rate limited" | Free-tier limit. Wait a few minutes and run it again; it continues where it stopped. |
| Startup check: `model … not found (HTTP 404)` | The provider retired that model. Pick one of the close matches shown and update `.env`. |
| `UI not built` at http://localhost:8001 | Run `npm run build` in `frontend/`. |
| `npm run build` fails | Node is too old: `nvm use 22`. |
| Answers don't mention training videos | `BOT_VIDEO_SOURCES_DIR` in `.env` points somewhere other than `data_sources/Video Jsons`. Fix it and rerun `python -m app.ingest`. |

## Current limitations

- **No sign-in yet.** Run it locally or on an internal network only. Users, tenants and rate limits are still to be built.
- **Untuned thresholds.** Search and not-found thresholds use starting values. Tune them with the eval set once it has labelled questions.
- **Recent chats live in the browser.** The sidebar list uses the browser's local storage. Conversation turns are also stored in the database for follow-up questions.

See `docs/models.md` for model choices.
