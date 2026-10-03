-- 001: initial schema (Architecture doc, Section 20).
-- Applied by `./scripts/setup_db.sh` (or `python -m app.db.migrate`), once, inside one
-- transaction. Never edit an applied migration: add 002_*.sql instead.
-- Open design questions are marked "OPEN:".

CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector with HNSW (>= 0.5); vector(1536) is within its 2,000-dim index limit

-- ---------------------------------------------------------------------------
-- Knowledge (shared across tenants, Section 15)
-- ---------------------------------------------------------------------------

-- One row per source item. document_id = '{source_type}:{external_id}', the same IDs the
-- eval set uses (e.g. 'zendesk:10022296840087', 'youtube:WjYQ9b751ns').
-- api_reference = one Postman endpoint; beans_content = one section of beans_content.json.
CREATE TABLE source_documents (
    document_id        text PRIMARY KEY,
    source_type        text NOT NULL CHECK (source_type IN ('zendesk', 'youtube', 'release_note', 'trainn', 'api_reference', 'beans_content')),
    external_id        text NOT NULL,
    title              text NOT NULL,
    url                text,
    language           text NOT NULL DEFAULT 'en',
    summary            text,              -- one-sentence summary used in chunk context headers
    published_at       timestamptz,
    source_updated_at  timestamptz,
    product_area       text,
    product_version    text,
    tags               text[] NOT NULL DEFAULT '{}',
    -- OPEN: audience scoping. Trainn/release-note items carry display_option_tags
    -- ('fedex', 'regionals') and account_buids. Kept here until the tenant model decides
    -- whether retrieval filters on them.
    audience_tags      text[] NOT NULL DEFAULT '{}',
    account_buids      text[] NOT NULL DEFAULT '{}',
    content_hash       text NOT NULL,     -- sha256 of normalized content; drives change detection
    raw                jsonb NOT NULL,    -- source payload as fetched
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
    is_deprecated      boolean NOT NULL DEFAULT false,
    last_synced_at     timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, external_id)
);

-- Retrieval unit. Replaced as a set per document inside one transaction (Section 21 step 6).
-- chunk_id = sha256(document_id || chunker_version || ordinal), so re-runs are idempotent.
CREATE TABLE chunks (
    chunk_id           text PRIMARY KEY,
    document_id        text NOT NULL REFERENCES source_documents (document_id) ON DELETE CASCADE,
    ordinal            integer NOT NULL,  -- position within the document (adjacent-chunk merge)
    source_type        text NOT NULL,     -- denormalized for filtering
    title              text NOT NULL,
    section_path       text,              -- 'Driver Work Schedule > Time Off Requests'
    content            text NOT NULL,
    context_header     text NOT NULL DEFAULT '',  -- enrichment text (Section 9)
    token_count        integer NOT NULL,
    source_url         text,              -- deep link; videos include ?t=
    timestamp_start    numeric,           -- videos, seconds
    step_number        integer,           -- Trainn tutorials
    published_at       timestamptz,
    updated_at         timestamptz,
    product_version    text,
    product_area       text,
    tags               text[] NOT NULL DEFAULT '{}',
    is_deprecated      boolean NOT NULL DEFAULT false,
    language           text NOT NULL DEFAULT 'en',
    content_hash       text NOT NULL,
    chunker_version    text NOT NULL,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    -- Lexical index. OPEN: switch to pg_search BM25 if hosting allows the extension.
    search_tsv         tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(section_path, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(context_header, '')), 'B') ||
        setweight(to_tsvector('english', content), 'C')
    ) STORED,
    UNIQUE (document_id, ordinal)
);
CREATE INDEX chunks_search_tsv_idx ON chunks USING gin (search_tsv) WHERE NOT is_deprecated;
CREATE INDEX chunks_document_idx ON chunks (document_id, ordinal);

-- Embeddings live in their own table (a change from the Section 20 field list) so another
-- model or size can be added and benchmarked next to the current one without re-ingesting.
-- Current model (docs/models.md): Gemini gemini-embedding-001 at 1536 dims, L2-normalized.
-- Dense only: keyword search is chunks.search_tsv (PostgreSQL full-text search).
-- Each model/size gets its own partial HNSW index, keyed by embedding_config.
CREATE TABLE chunk_embeddings (
    chunk_id           text NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    embedding_config   text NOT NULL,     -- EmbeddingSettings.config_name, e.g. 'gemini-embedding-001-1536'
    embedding_model    text NOT NULL,     -- EMBEDDING_MODEL, e.g. 'gemini-embedding-001'
    embedding_version  text NOT NULL,     -- model version as reported by the provider API
    dimensions         integer NOT NULL,
    embedding          vector NOT NULL,   -- untyped; cast in the index and the query
    embedded_text_hash text NOT NULL,     -- hash of context_header + content actually embedded
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, embedding_config)
);
CREATE INDEX chunk_embeddings_gemini_embedding_001_1536_hnsw ON chunk_embeddings
    USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
    WHERE embedding_config = 'gemini-embedding-001-1536';
-- Queries must match the index expression, e.g.
--   ORDER BY embedding::vector(1536) <=> $1::vector(1536) WHERE embedding_config = 'gemini-embedding-001-1536'
-- and filter out deprecated chunks through a join on chunks.
-- To add a model: add a partial index for its embedding_config (vector up to 2,000 dims,
-- halfvec up to 4,000), embed into the same table, and compare on the eval set.

-- ---------------------------------------------------------------------------
-- Tenant-scoped tables (row-level security as a second layer, Section 15)
-- Session sets: SET LOCAL app.tenant_id = '<from JWT>'.
-- OPEN: tenant/user ID types and role model depend on the tenant-model decision.
-- OPEN: retention periods (Section 27) -> scheduled delete jobs.
-- ---------------------------------------------------------------------------

CREATE TABLE conversations (
    conversation_id    uuid PRIMARY KEY,
    tenant_id          text NOT NULL,
    user_id            text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_activity_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_tenant_user_idx ON conversations (tenant_id, user_id, last_activity_at DESC);

-- Durable record of each turn. Live memory (last 6 turns + summary, TTL) stays in Redis.
CREATE TABLE messages (
    message_id         uuid PRIMARY KEY,
    conversation_id    uuid NOT NULL REFERENCES conversations (conversation_id) ON DELETE CASCADE,
    tenant_id          text NOT NULL,
    question           text NOT NULL,
    answer             text NOT NULL,
    cited_chunk_ids    text[] NOT NULL DEFAULT '{}',
    evidence_status    text CHECK (evidence_status IN ('found', 'partial', 'not_found')),
    tool_calls         text[] NOT NULL DEFAULT '{}',
    model              text NOT NULL,
    input_tokens       integer,
    output_tokens      integer,
    cost_usd           numeric(12, 6),
    latency_ms         jsonb,             -- per-stage timings (Section 19)
    trace_id           text,              -- tracing ID, if tracing is enabled
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at);

CREATE TABLE feedback (
    feedback_id        uuid PRIMARY KEY,
    message_id         uuid NOT NULL REFERENCES messages (message_id) ON DELETE CASCADE,
    tenant_id          text NOT NULL,
    user_id            text NOT NULL,
    rating             smallint NOT NULL CHECK (rating IN (-1, 1)),
    reason             text CHECK (reason IN ('wrong', 'outdated', 'missing_source', 'not_helpful')),
    comment            text,
    reviewed           boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, user_id)
);

-- Written by the structured-data tools (Phase 3).
CREATE TABLE tool_audit_log (
    id                 bigserial PRIMARY KEY,
    tenant_id          text NOT NULL,
    user_id            text NOT NULL,
    message_id         uuid REFERENCES messages (message_id) ON DELETE SET NULL,
    tool               text NOT NULL,
    parameters         jsonb NOT NULL,
    row_count          integer,
    latency_ms         integer,
    status             text NOT NULL,     -- ok | error | timeout | rejected
    created_at         timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback       ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON conversations  USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON messages       USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON feedback       USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON tool_audit_log USING (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------------------
-- Evaluation (Section 22). The JSONL files in evals/datasets are the source of truth
-- (reviewable in git) and are synced here for dashboards. OPEN: confirm this choice.
-- ---------------------------------------------------------------------------

CREATE TABLE eval_questions (
    question_id        text PRIMARY KEY,
    question_type      text NOT NULL,
    labelled           boolean NOT NULL,
    payload            jsonb NOT NULL,    -- full EvalQuestion
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
    run_id             text PRIMARY KEY,
    target             text NOT NULL,     -- eval target name, e.g. chat_server
    dataset_sha256     text NOT NULL,
    manifest           jsonb NOT NULL,    -- config snapshot, git sha, judge model
    summary            jsonb NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);
