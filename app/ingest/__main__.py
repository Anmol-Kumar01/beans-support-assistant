"""Load all knowledge sources into PostgreSQL: documents, chunks and embeddings.

  python -m app.ingest              ingest new and changed documents
  python -m app.ingest --dry-run    show what would change, without embedding or writing
  python -m app.ingest --force      re-chunk and re-embed everything

Idempotent (Section 21): a document is re-chunked and re-embedded only when its content
hash changes or it has no embedding for the current EMBEDDING_* config. Each document is
written in one transaction. Documents no longer in the sources are marked deleted and
their chunks removed. Needs the database (./scripts/setup_db.sh) and EMBEDDING_API_KEY.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time

import psycopg

from app.core.config import get_model_settings, get_settings
from app.ingest.chunking import CHUNKER_VERSION, Chunk, chunk_document
from app.ingest.sources import SourceDoc, load_all
from app.llm.retry import ProviderError
from app.retrieval.embeddings import Embedder


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


async def _embed(texts: list[str]) -> tuple[list[list[float]], dict]:
    s = get_model_settings().embedding
    embedder = Embedder(s.model_copy(update={"max_requests_per_minute": s.ingest_max_requests_per_minute}))
    try:
        vectors = await embedder.embed(texts) if texts else []
        return vectors, embedder.identity()
    finally:
        await embedder.aclose()


def _write_document(conn: psycopg.Connection, doc: SourceDoc, chunks: list[Chunk], vectors: list[list[float]], ident: dict) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO source_documents (document_id, source_type, external_id, title, url, source_updated_at,
                                          tags, audience_tags, content_hash, raw, status, last_synced_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', now(), now())
            ON CONFLICT (document_id) DO UPDATE SET
                title = EXCLUDED.title, url = EXCLUDED.url, source_updated_at = EXCLUDED.source_updated_at,
                tags = EXCLUDED.tags, audience_tags = EXCLUDED.audience_tags, content_hash = EXCLUDED.content_hash,
                raw = EXCLUDED.raw, status = 'active', last_synced_at = now(), updated_at = now()
            """,
            (doc.document_id, doc.source_type, doc.external_id, doc.title, doc.url, doc.updated_at,
             doc.tags, doc.audience_tags, doc.content_hash(), json.dumps(doc.raw)),
        )
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc.document_id,))
        for c, vec in zip(chunks, vectors, strict=True):
            conn.execute(
                """
                INSERT INTO chunks (chunk_id, document_id, ordinal, source_type, title, section_path, content,
                                    context_header, token_count, source_url, timestamp_start, updated_at,
                                    tags, content_hash, chunker_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (c.chunk_id, c.document_id, c.ordinal, c.source_type, c.title, c.section_path, c.content,
                 c.context_header, c.token_count, c.source_url, c.timestamp_start, doc.updated_at,
                 doc.tags, hashlib.sha256(c.content.encode()).hexdigest(), CHUNKER_VERSION),
            )
            conn.execute(
                """
                INSERT INTO chunk_embeddings (chunk_id, embedding_config, embedding_model, embedding_version,
                                              dimensions, embedding, embedded_text_hash)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                """,
                (c.chunk_id, ident["embedding_config"], ident["embedding_model"], ident["embedding_version"],
                 ident["dimensions"], _vector(vec), hashlib.sha256(c.embed_text.encode()).hexdigest()),
            )


def ingest(force: bool = False, dry_run: bool = False) -> int:
    settings, models = get_settings(), get_model_settings()
    config = models.embedding.config_name
    start = time.perf_counter()
    docs = load_all(settings.data_sources_dir, settings.video_sources_dir, settings.hub_include_account_tutorials)
    print(f"Loaded {len(docs)} documents from the sources.")

    with psycopg.connect(settings.database_url) as conn:
        known = dict(conn.execute("SELECT document_id, content_hash FROM source_documents WHERE status = 'active'").fetchall())
        embedded = {row[0] for row in conn.execute(
            """SELECT DISTINCT c.document_id FROM chunks c
               JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.embedding_config = %s""", (config,)).fetchall()}

        changed = [d for d in docs if force or known.get(d.document_id) != d.content_hash() or d.document_id not in embedded]
        current = {d.document_id for d in docs}
        removed = sorted(set(known) - current)
        work = [(d, chunk_document(d)) for d in changed]
        n_chunks = sum(len(c) for _, c in work)
        print(f"  unchanged {len(docs) - len(changed)}, to (re)index {len(changed)} ({n_chunks} chunks), to remove {len(removed)}")
        if dry_run:
            for d, cs in work[:15]:
                print(f"    + {d.document_id} ({len(cs)} chunks)")
            if len(work) > 15:
                print(f"    … and {len(work) - 15} more")
            return 0

        # Embed and write in groups so progress is kept: if the provider quota runs out,
        # running ingest again continues with the documents not yet written.
        batch = get_model_settings().embedding.batch_size
        groups, current = [], []
        for doc, cs in work:
            if current and sum(len(c) for _, c in current) + len(cs) > batch:
                groups.append(current)
                current = []
            current.append((doc, cs))
        if current:
            groups.append(current)
        if work:
            print(f"  embedding {n_chunks} chunks with {models.embedding.model} ({models.embedding.dim} dims) "
                  f"in {len(groups)} requests, at most {models.embedding.ingest_max_requests_per_minute or 'unlimited'}/min...")
        written = 0
        for gi, group in enumerate(groups, 1):
            texts = [c.embed_text for _, cs in group for c in cs]
            try:
                vectors, ident = asyncio.run(_embed(texts))
            except ProviderError as exc:
                print(f"\nStopped at request {gi}/{len(groups)}: {exc}")
                print(f"Saved {written} of {len(work)} documents. Run `python -m app.ingest` again later to continue.")
                return 1
            i = 0
            for doc, cs in group:
                _write_document(conn, doc, cs, vectors[i:i + len(cs)], ident)
                i += len(cs)
                written += 1
            print(f"    {gi}/{len(groups)} done ({written}/{len(work)} documents)", flush=True)
        for doc_id in removed:
            with conn.transaction():
                conn.execute("UPDATE source_documents SET status = 'deleted', updated_at = now() WHERE document_id = %s", (doc_id,))
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))

        totals = conn.execute(
            """SELECT c.source_type, count(*) FROM chunks c JOIN source_documents d USING (document_id)
               WHERE d.status = 'active' GROUP BY 1 ORDER BY 1""").fetchall()
    print(f"Done in {time.perf_counter() - start:.1f}s. Chunks in the index: "
          + ", ".join(f"{t} {n}" for t, n in totals) + f" (total {sum(n for _, n in totals)}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.ingest")
    p.add_argument("--force", action="store_true", help="re-chunk and re-embed everything")
    p.add_argument("--dry-run", action="store_true", help="show what would change, without writing")
    args = p.parse_args(argv)
    try:
        return ingest(force=args.force, dry_run=args.dry_run)
    except psycopg.OperationalError as exc:
        print(f"Can't connect to the database: {exc}".strip())
        print("Start it with ./scripts/setup_db.sh")
        return 2


if __name__ == "__main__":
    sys.exit(main())
