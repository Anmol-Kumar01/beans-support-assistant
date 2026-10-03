"""Score one question, and aggregate scored results into Section 22 metrics."""

from collections import defaultdict
from statistics import mean

from evals.config import EvalSettings
from evals.metrics import behavior as bh
from evals.metrics import citations as cit
from evals.metrics import retrieval as ret
from evals.metrics.judge import Judge, grounding_scores
from evals.metrics.perf import summarize_perf
from evals.schema import (
    BotResponse,
    EvalQuestion,
    ExpectedBehavior,
    JudgeTrace,
    ObservedBehavior,
    QuestionResult,
    QuestionScores,
)


async def score_question(
    q: EvalQuestion, r: BotResponse, judge: Judge | None, settings: EvalSettings
) -> QuestionResult:
    s = QuestionScores()
    traces: list[JudgeTrace] = []
    if r.error is not None:
        return QuestionResult(question=q, response=r, scores=s)

    # Retrieval (document level)
    if q.expected_source_ids and r.retrieved is not None:
        ranked = ret.ranked_document_ids(r.retrieved)
        for k in settings.retrieval_k_values:
            s.hit_at_k[k] = ret.hit_at_k(ranked, q.expected_source_ids, k)
            s.recall_at_k[k] = ret.recall_at_k(ranked, q.expected_source_ids, k)
        s.mrr = ret.reciprocal_rank(ranked, q.expected_source_ids)

    # Behavior
    observed = bh.behavior_from_signals(r, settings.not_found_phrases)
    if observed is None and judge is not None:
        observed, t = await judge.behavior(q.question, r.answer)
        traces.append(t)
    s.observed_behavior = observed
    if observed is not None:
        s.behavior_correct = bh.behavior_correct(q.expected_behavior, observed)
    s.routing_correct = bh.routing_correct(q.expected_tools, r.tool_calls)

    # Citations and answer quality: only for questions that should be answered and were.
    s.citation_validity = cit.citation_validity(r.citations)
    if q.expected_behavior == ExpectedBehavior.ANSWER:
        s.cited_source_hit = cit.cited_source_hit(r.citations, q.expected_source_ids)
        answered = observed in (ObservedBehavior.ANSWERED, None)
        if answered:
            s.uncited_answer = not cit.has_valid_citation(r.citations)
        if judge is not None and q.reference_answer:
            if observed == ObservedBehavior.NOT_FOUND:
                s.correctness = 0.0  # answerable question reported as not found
            else:
                s.correctness, t = await judge.correctness(q.question, r.answer, q.reference_answer)
                traces.append(t)
        if judge is not None and answered:
            if not r.evidence:
                s.groundedness = 0.0  # an uncited knowledge answer is ungrounded by definition
            else:
                verdict, t = await judge.grounding(r.answer, r.evidence)
                traces.append(t)
                s.groundedness, s.citation_precision = grounding_scores(verdict)

    return QuestionResult(question=q, response=r, scores=s, judge=traces)


def _avg(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return mean(vals) if vals else None


def _quality(results: list[QuestionResult], k_values: list[int]) -> dict:
    sc = [x.scores for x in results]
    by_exp = lambda b: [x.scores for x in results if x.question.expected_behavior == b]  # noqa: E731
    answer = by_exp(ExpectedBehavior.ANSWER)
    out = {
        "n": len(results),
        "errors": sum(x.response.error is not None for x in results),
        "retrieval_mrr": _avg(s.mrr for s in sc),
        "answer_correctness": _avg(s.correctness for s in sc),
        "groundedness": _avg(s.groundedness for s in sc),
        "citation_precision": _avg(s.citation_precision for s in sc),
        "citation_validity": _avg(s.citation_validity for s in sc),
        "cited_source_hit": _avg(s.cited_source_hit for s in sc),
        "uncited_answer_rate": _avg(s.uncited_answer for s in answer),
        "false_not_found_rate": _avg(
            s.observed_behavior == ObservedBehavior.NOT_FOUND
            for s in answer
            if s.observed_behavior is not None
        ),
        "not_found_accuracy": _avg(s.behavior_correct for s in by_exp(ExpectedBehavior.NOT_FOUND)),
        "out_of_scope_decline_rate": _avg(s.behavior_correct for s in by_exp(ExpectedBehavior.DECLINE)),
        "adversarial_pass_rate": _avg(s.behavior_correct for s in by_exp(ExpectedBehavior.RESIST)),
        "routing_accuracy": _avg(s.routing_correct for s in sc),
    }
    for k in k_values:
        # Section 22 "recall@k" = correct source in the top k.
        out[f"retrieval_recall_at_{k}"] = _avg(s.hit_at_k.get(k) for s in sc)
        out[f"retrieval_full_recall_at_{k}"] = _avg(s.recall_at_k.get(k) for s in sc)
    return out


def aggregate(results: list[QuestionResult], settings: EvalSettings) -> dict:
    gated = [x for x in results if x.question.question_type in settings.gated_question_types]
    by_type: dict[str, list[QuestionResult]] = defaultdict(list)
    for x in results:
        by_type[x.question.question_type].append(x)
    judge_costs = [t.cost_usd for x in results for t in x.judge if t.cost_usd is not None]
    return {
        "gated": {**_quality(gated, settings.retrieval_k_values), **summarize_perf([x.response for x in gated])},
        "all": {**_quality(results, settings.retrieval_k_values), **summarize_perf([x.response for x in results])},
        "by_type": {t: _quality(v, settings.retrieval_k_values) for t, v in sorted(by_type.items())},
        "judge_cost_usd": sum(judge_costs) if judge_costs else None,
    }
