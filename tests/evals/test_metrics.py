import pytest

from evals.metrics import behavior as bh
from evals.metrics import citations as cit
from evals.metrics import retrieval as ret
from evals.metrics.judge import CitationCheck, ClaimCheck, GroundingVerdict, grounding_scores
from evals.metrics.perf import percentile, summarize_perf
from evals.schema import BotResponse, Citation, ExpectedBehavior, ObservedBehavior, RetrievedItem, Usage


def test_ranked_document_ids_collapses_chunks():
    items = [
        RetrievedItem(document_id="zendesk:2", chunk_id="b", rank=2),
        RetrievedItem(document_id="zendesk:1", chunk_id="a", rank=1),
        RetrievedItem(document_id="zendesk:1", chunk_id="c", rank=3),
        RetrievedItem(document_id="youtube:x", rank=4),
    ]
    assert ret.ranked_document_ids(items) == ["zendesk:1", "zendesk:2", "youtube:x"]


def test_retrieval_metrics():
    ranked = ["a", "b", "c", "d"]
    assert ret.hit_at_k(ranked, ["c", "z"], 3) == 1.0
    assert ret.hit_at_k(ranked, ["d"], 3) == 0.0
    assert ret.recall_at_k(ranked, ["a", "d"], 3) == 0.5
    assert ret.reciprocal_rank(ranked, ["c"]) == pytest.approx(1 / 3)
    assert ret.reciprocal_rank(ranked, ["z"]) == 0.0
    with pytest.raises(ValueError):
        ret.recall_at_k(ranked, [], 3)


def test_citation_metrics():
    cits = [
        Citation(document_ids=["t:1", "t:2"]),
        Citation(document_ids=[], valid=False),
    ]
    assert cit.citation_validity(cits) == 0.5
    assert cit.citation_validity([]) is None
    assert cit.cited_source_hit(cits, ["t:2"]) == 1.0
    assert cit.cited_source_hit(cits, ["t:9"]) == 0.0
    assert cit.cited_source_hit(cits, []) is None
    assert not cit.has_valid_citation([Citation(valid=False)])


def test_behavior_signals_and_acceptance():
    phrases = ["i couldn't find this in the beans documentation"]
    assert bh.behavior_from_signals(BotResponse(evidence_status="not_found"), phrases) == ObservedBehavior.NOT_FOUND
    r = BotResponse(answer="I couldn't find this in the Beans documentation. Contact support?")
    assert bh.behavior_from_signals(r, phrases) == ObservedBehavior.NOT_FOUND
    assert bh.behavior_from_signals(BotResponse(answer="Click Save."), phrases) is None

    assert bh.behavior_correct(ExpectedBehavior.RESIST, ObservedBehavior.DECLINED)
    assert not bh.behavior_correct(ExpectedBehavior.RESIST, ObservedBehavior.COMPLIED_WITH_INJECTION)
    assert not bh.behavior_correct(ExpectedBehavior.NOT_FOUND, ObservedBehavior.ANSWERED)


def test_routing():
    assert bh.routing_correct(["search_knowledge_base"], ["search_knowledge_base"])
    assert bh.routing_correct([], []) is True
    assert bh.routing_correct(["a", "b"], ["a"]) is False
    assert bh.routing_correct(None, ["a"]) is None
    assert bh.routing_correct(["a"], None) is None


def test_grounding_scores():
    v = GroundingVerdict(claims=[
        ClaimCheck(claim="a", supported_by_any_evidence=True, citations=[CitationCheck(label="[1]", supports=True)]),
        ClaimCheck(claim="b", supported_by_any_evidence=False, citations=[CitationCheck(label="[2]", supports=False)]),
        ClaimCheck(claim="c", supported_by_any_evidence=True, citations=[]),
    ])
    g, p = grounding_scores(v)
    assert g == pytest.approx(2 / 3) and p == 0.5
    assert grounding_scores(GroundingVerdict(claims=[])) == (None, None)


def test_percentile_and_perf():
    assert percentile([], 95) is None
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([5.0], 50) == 5.0
    rs = [
        BotResponse(ttft_ms=100, total_ms=900, stage_ms={"rerank": 200}, usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.01)),
        BotResponse(ttft_ms=300, total_ms=1100, stage_ms={"rerank": 250}, usage=Usage(cost_usd=0.03)),
        BotResponse(error="timeout"),
    ]
    s = summarize_perf(rs)
    assert s["ttft_p95_ms"] == 300 and s["stage_p95_ms"] == {"rerank": 250}
    assert s["cost_per_answer_usd"] == pytest.approx(0.02)
    assert s["input_tokens"] == 10
