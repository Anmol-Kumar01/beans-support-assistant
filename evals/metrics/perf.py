"""Latency and cost aggregation (Sections 19 and 22)."""

import math

from evals.schema import BotResponse


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; p in (0, 100]."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_perf(responses: list[BotResponse]) -> dict:
    ok = [r for r in responses if r.error is None]
    ttft = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    total = [r.total_ms for r in ok]
    stages: dict[str, list[float]] = {}
    for r in ok:
        for stage, ms in r.stage_ms.items():
            stages.setdefault(stage, []).append(ms)
    costs = [r.usage.cost_usd for r in ok if r.usage and r.usage.cost_usd is not None]
    return {
        "ttft_p50_ms": percentile(ttft, 50),
        "ttft_p95_ms": percentile(ttft, 95),
        "total_p50_ms": percentile(total, 50),
        "total_p95_ms": percentile(total, 95),
        "stage_p95_ms": {s: percentile(v, 95) for s, v in sorted(stages.items())},
        "input_tokens": sum(r.usage.input_tokens for r in ok if r.usage),
        "output_tokens": sum(r.usage.output_tokens for r in ok if r.usage),
        "cost_per_answer_usd": sum(costs) / len(costs) if costs else None,
        "cost_total_usd": sum(costs) if costs else None,
    }
