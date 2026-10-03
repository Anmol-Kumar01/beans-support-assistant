"""Release gate (Section 22): compare a candidate run against a baseline run.

A metric fails when the candidate is worse than the baseline by more than its tolerance.
Metrics the baseline cannot report (N/A) are checked against the absolute target instead.
"""

from dataclasses import dataclass

from evals.config import MetricGate


@dataclass
class GateResult:
    metric: str
    baseline: float | None
    candidate: float | None
    target: float | None
    passed: bool | None  # None = not measurable in the candidate
    reason: str


def check_gate(name: str, gate: MetricGate, baseline: float | None, candidate: float | None) -> GateResult:
    if candidate is None:
        return GateResult(name, baseline, candidate, gate.target, None, "candidate did not report this metric")
    sign = 1 if gate.higher_is_better else -1
    if baseline is not None and sign * (candidate - baseline) < -gate.tolerance:
        return GateResult(name, baseline, candidate, gate.target, False, f"regressed beyond tolerance {gate.tolerance}")
    if baseline is None and gate.target is not None and sign * (candidate - gate.target) < 0:
        return GateResult(name, baseline, candidate, gate.target, False, "no baseline; below absolute target")
    reason = "ok"
    if gate.target is not None and sign * (candidate - gate.target) < 0:
        reason = "ok vs baseline, but short of target"
    return GateResult(name, baseline, candidate, gate.target, True, reason)


def compare_summaries(baseline: dict, candidate: dict, gates: dict[str, MetricGate]) -> list[GateResult]:
    b, c = baseline["gated"], candidate["gated"]
    return [check_gate(name, gate, b.get(name), c.get(name)) for name, gate in gates.items()]


def render_comparison(results: list[GateResult]) -> str:
    def f(v):
        return "N/A" if v is None else f"{v:.4g}"

    status = {True: "PASS", False: "FAIL", None: "N/A"}
    lines = ["| Metric | Baseline | Candidate | Target | Result | Note |", "|---|---|---|---|---|---|"]
    lines += [
        f"| {r.metric} | {f(r.baseline)} | {f(r.candidate)} | {f(r.target)} | {status[r.passed]} | {r.reason} |"
        for r in results
    ]
    if any(r.passed is False for r in results):
        overall = "FAIL"
    elif any(r.passed is None for r in results):
        overall = "INCOMPLETE (some gated metrics not reported)"
    else:
        overall = "PASS"
    return "\n".join(lines) + f"\n\nOverall: **{overall}**\n"
