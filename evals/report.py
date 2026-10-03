"""Markdown report for one run."""

from evals.schema import QuestionResult

HEADLINE = [
    ("retrieval_recall_at_10", "Retrieval recall@10 (correct source in top 10)", "pct"),
    ("retrieval_full_recall_at_10", "All expected sources in top 10", "pct"),
    ("retrieval_mrr", "Retrieval MRR", "num"),
    ("answer_correctness", "Answer correctness (LLM-judged)", "pct"),
    ("groundedness", "Groundedness", "pct"),
    ("citation_precision", "Citation precision (cited source supports claim)", "pct"),
    ("citation_validity", "Citation validity (resolves to a real source)", "pct"),
    ("cited_source_hit", "Cited a labelled source", "pct"),
    ("uncited_answer_rate", "Uncited knowledge answers", "pct"),
    ("not_found_accuracy", "Correct not-found on unanswerable", "pct"),
    ("false_not_found_rate", "Not-found on answerable questions", "pct"),
    ("out_of_scope_decline_rate", "Out-of-scope declined", "pct"),
    ("adversarial_pass_rate", "Adversarial pass rate", "pct"),
    ("routing_accuracy", "Tool/routing accuracy", "pct"),
    ("ttft_p95_ms", "Time to first token p95 (ms)", "ms"),
    ("total_p95_ms", "Full answer p95 (ms)", "ms"),
    ("cost_per_answer_usd", "Cost per answer (USD)", "usd"),
]


def fmt(value, kind: str) -> str:
    if value is None:
        return "N/A"
    return {"pct": f"{value:.1%}", "ms": f"{value:,.0f}", "usd": f"${value:.4f}"}.get(kind, f"{value:.3f}")


def _judge(manifest: dict) -> str:
    judge = (manifest.get("models") or {}).get("judge")
    if not judge:
        return "none (deterministic metrics only)"
    text = f"`{judge.get('model')}`" + (f" at {judge['base_url']}" if judge.get("base_url") else "")
    if (manifest.get("models") or {}).get("self_grading"):
        text += " ⚠ also produced the answers (self-grading bias)"
    return text


def _reported(manifest: dict) -> str:
    reported = (manifest.get("models") or {}).get("target_reported")
    if not reported:
        return "not reported"
    return ", ".join(f"{role} `{'`, `'.join(ms)}`" for role, ms in reported.items())


def render_report(manifest: dict, summary: dict, results: list[QuestionResult]) -> str:
    g, a = summary["gated"], summary["all"]
    lines = [
        f"# Eval run {manifest['run_id']}",
        "",
        f"- Target: `{manifest['target']}`",
        f"- Dataset: `{manifest['dataset']}` ({manifest['questions']} questions, sha256 {manifest['dataset_sha256'][:12]})",
        f"- Judge: {_judge(manifest)} · judge cost: {fmt(summary.get('judge_cost_usd'), 'usd')}",
        f"- Models reported by target: {_reported(manifest)}",
        f"- Errors: {a['errors']} / {a['n']}",
        "",
        "N/A means the target does not report the signal or no question in the slice applies.",
        "",
        "| Metric | Gated types | All |",
        "|---|---|---|",
    ]
    lines += [f"| {label} | {fmt(g.get(k), kind)} | {fmt(a.get(k), kind)} |" for k, label, kind in HEADLINE]
    if a.get("stage_p95_ms"):
        lines += ["", "## Stage latency p95 (ms)", "", "| Stage | p95 |", "|---|---|"]
        lines += [f"| {s} | {fmt(v, 'ms')} |" for s, v in a["stage_p95_ms"].items()]
    lines += [
        "",
        "## By question type",
        "",
        "| Type | n | Recall@10 | Correctness | Grounded | Cit. precision | Behavior OK |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, m in summary["by_type"].items():
        behavior = next(
            (m[k] for k in ("not_found_accuracy", "out_of_scope_decline_rate", "adversarial_pass_rate") if m[k] is not None),
            None,
        )
        lines.append(
            f"| {t} | {m['n']} | {fmt(m['retrieval_recall_at_10'], 'pct')} | {fmt(m['answer_correctness'], 'pct')} "
            f"| {fmt(m['groundedness'], 'pct')} | {fmt(m['citation_precision'], 'pct')} | {fmt(behavior, 'pct')} |"
        )
    configured = (manifest.get("models") or {}).get("configured")
    if configured:
        lines += ["", "## Models configured for this run", "", "| Role | Model | Where |", "|---|---|---|"]
        for role, m in configured.items():
            where = m.get("base_url") or ("local CPU" + (f", rev {m['revision']}" if m.get("revision") else ""))
            lines.append(f"| {role} | `{m.get('model')}` | {where} |")
    failures = [
        x for x in results
        if x.response.error or x.scores.behavior_correct is False or (x.scores.correctness is not None and x.scores.correctness < 1)
    ]
    if failures:
        lines += ["", "## Questions to review", "", "| ID | Type | Issue |", "|---|---|---|"]
        for x in failures:
            issue = x.response.error or (
                f"behavior {x.scores.observed_behavior}" if x.scores.behavior_correct is False
                else f"correctness {x.scores.correctness}"
            )
            lines.append(f"| {x.question.id} | {x.question.question_type} | {issue} |")
    return "\n".join(lines) + "\n"
