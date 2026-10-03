"""Deterministic citation checks. Whether a citation actually supports its claim is
judged separately (metrics/judge.py -> citation_precision)."""

from evals.schema import Citation


def citation_validity(citations: list[Citation]) -> float | None:
    """Share of shown citations that resolve to a real provided source."""
    if not citations:
        return None
    return sum(c.valid for c in citations) / len(citations)


def has_valid_citation(citations: list[Citation]) -> bool:
    return any(c.valid for c in citations)


def cited_source_hit(citations: list[Citation], expected: list[str]) -> float | None:
    """1.0 if any valid citation points at a labelled source. A citation that resolves to
    several documents (ambiguous link/title) counts if any of them is expected."""
    if not expected:
        return None
    targets = set(expected)
    return float(any(c.valid and targets & set(c.document_ids) for c in citations))
