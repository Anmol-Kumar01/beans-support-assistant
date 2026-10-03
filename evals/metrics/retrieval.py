"""Document-level retrieval metrics. Chunks are collapsed to their parent document so
results are compared against the golden set's document-level labels."""

from evals.schema import RetrievedItem


def ranked_document_ids(items: list[RetrievedItem]) -> list[str]:
    seen: dict[str, None] = {}
    for item in sorted(items, key=lambda i: i.rank):
        seen.setdefault(item.document_id, None)
    return list(seen)


def hit_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    """1.0 if any expected document is in the top k (Section 22 'recall@10')."""
    return float(bool(set(ranked[:k]) & set(expected)))


def recall_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    """Fraction of expected documents found in the top k (stricter, for multi-source)."""
    if not expected:
        raise ValueError("expected must not be empty")
    return len(set(ranked[:k]) & set(expected)) / len(set(expected))


def reciprocal_rank(ranked: list[str], expected: list[str]) -> float:
    targets = set(expected)
    for i, doc_id in enumerate(ranked, 1):
        if doc_id in targets:
            return 1.0 / i
    return 0.0
