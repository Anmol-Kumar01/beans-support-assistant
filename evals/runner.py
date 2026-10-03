"""Run a dataset against a target and write results, summary, and report to a run folder."""

import asyncio
import hashlib
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import ModelSettings, same_model
from evals.config import EvalSettings
from evals.metrics.judge import Judge
from evals.report import render_report
from evals.schema import EvalQuestion, QuestionResult
from evals.scoring import aggregate, score_question
from evals.targets.base import Target

log = logging.getLogger("evals")


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _safe_settings(settings: EvalSettings) -> dict:
    return json.loads(settings.model_dump_json(exclude={"new_bot_token", "langsmith_api_key"}))


def _reported_models(results: list[QuestionResult]) -> dict[str, list[str]]:
    """Models per role that the target reported, across all responses."""
    seen: dict[str, set[str]] = {}
    for x in results:
        for role, model in (x.response.models or {}).items():
            seen.setdefault(role, set()).add(model)
    return {role: sorted(models) for role, models in sorted(seen.items())}


async def run_eval(
    questions: list[EvalQuestion],
    target: Target,
    judge: Judge | None,
    settings: EvalSettings,
    dataset_path: Path,
    run_name: str | None = None,
    models: ModelSettings | None = None,
) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{target.name}"
    if run_name:
        run_id += f"-{run_name}"
    run_dir = settings.reports_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "target": target.describe(),
        # Which model served each role (requirement: compare runs across model setups).
        "models": {
            "judge": judge.describe() if judge and hasattr(judge, "describe") else (
                {"model": judge.model} if judge else None
            ),
            "configured": models.manifest() if models else None,
            "target_reported": None,  # filled in when the run finishes
        },
        "dataset": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "questions": len(questions),
        "code_git_sha": _git_sha(),
        "settings": _safe_settings(settings),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    sem = asyncio.Semaphore(settings.concurrency)
    results_path = run_dir / "results.jsonl"
    write_lock = asyncio.Lock()
    results: list[QuestionResult] = []

    async def one(q: EvalQuestion) -> None:
        async with sem:
            session_id = f"{settings.session_prefix}-{run_id}-{q.id}"
            resp = await target.answer(q, session_id)
            try:
                result = await score_question(q, resp, judge, settings)
            except Exception as exc:  # judge failure must not sink the run
                log.warning("scoring failed for %s: %s", q.id, exc)
                resp.error = resp.error or f"scoring: {type(exc).__name__}: {exc}"
                result = await score_question(q, resp, None, settings)
        async with write_lock:
            results.append(result)
            with results_path.open("a", encoding="utf-8") as f:
                f.write(result.model_dump_json() + "\n")
            log.info("[%d/%d] %s %s", len(results), len(questions), q.id,
                     "ERROR " + result.response.error if result.response.error else "ok")

    try:
        await asyncio.gather(*(one(q) for q in questions))
    finally:
        await target.aclose()
        if judge is not None and hasattr(judge, "aclose"):
            await judge.aclose()

    order = {q.id: i for i, q in enumerate(questions)}
    results.sort(key=lambda x: order[x.question.id])
    summary = aggregate(results, settings)
    reported = _reported_models(results)
    manifest["models"]["target_reported"] = reported or None
    if judge is not None:
        graded_by_self = sorted(
            m for ms in reported.values() for m in ms if same_model(m, judge.model)
        )
        manifest["models"]["self_grading"] = bool(graded_by_self)
        if graded_by_self:
            log.warning("judge %s graded answers from its own model (%s): self-grading bias",
                        judge.model, ", ".join(graded_by_self))
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (run_dir / "report.md").write_text(render_report(manifest, summary, results))
    return run_dir
