"""Export the current bot's LangSmith runs to JSONL for the ``legacy_traces`` eval target.

Writes root runs (the RAG chain and the citation pass, with inputs/outputs) and LLM runs
(model name only, no text), which is all evals/targets/legacy_traces.py needs.
User text is PII-scrubbed before it is written. Needs LANGSMITH_API_KEY (not a model key).

  python -m evals export-langsmith --project <name> [--since 2025-07-01] [--limit 5000]
"""

import json
from datetime import datetime
from pathlib import Path

from evals.tools.scrub_pii import Scrubber

_ROOT_FIELDS = ("id", "trace_id", "parent_run_id", "name", "run_type", "start_time", "end_time", "error")


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _model_extra(extra: dict | None) -> dict:
    extra = extra or {}
    meta = extra.get("metadata") or {}
    params = extra.get("invocation_params") or {}
    return {
        "metadata": {k: meta[k] for k in ("ls_model_name", "ls_provider") if k in meta},
        "invocation_params": {k: params[k] for k in ("model", "model_name") if k in params},
    }


def _scrub_inputs(inputs: dict, scrubber: Scrubber) -> dict:
    out = dict(inputs)
    for key in ("input", "query", "answer"):
        if isinstance(out.get(key), str):
            out[key] = scrubber.scrub(out[key])
    out.pop("chat_history", None)  # earlier turns: not needed, and the most PII-dense field
    return out


def _scrub_outputs(outputs, scrubber: Scrubber):
    """Scrub answer text; keep retrieved context (knowledge-base content) as is."""
    if isinstance(outputs, str):
        return scrubber.scrub(outputs)
    if isinstance(outputs, list):
        return [_scrub_outputs(v, scrubber) for v in outputs]
    if isinstance(outputs, dict):
        out = {}
        for k, v in outputs.items():
            if k == "chat_history":
                continue
            out[k] = v if k == "context" else _scrub_outputs(v, scrubber)
        return out
    return outputs


def _get(run, name):
    return run.get(name) if isinstance(run, dict) else getattr(run, name, None)


def export_runs(
    project: str,
    out: Path,
    scrubber: Scrubber,
    since: datetime | None = None,
    limit: int | None = None,
    client=None,
    api_key: str | None = None,
) -> tuple[int, int]:
    """Write runs to ``out``. Returns (root runs, llm runs) written."""
    if client is None:
        from langsmith import Client

        client = Client(api_key=api_key)  # None falls back to LANGSMITH_API_KEY in the environment
    out.parent.mkdir(parents=True, exist_ok=True)
    roots = llms = 0
    with out.open("w", encoding="utf-8") as f:
        for run in client.list_runs(project_name=project, is_root=True, start_time=since, limit=limit):
            row = {k: _get(run, k) for k in _ROOT_FIELDS}
            row["inputs"] = _scrub_inputs(_get(run, "inputs") or {}, scrubber)
            row["outputs"] = _scrub_outputs(_get(run, "outputs") or {}, scrubber)
            row["extra"] = _model_extra(_get(run, "extra"))
            f.write(json.dumps(_jsonable(row), ensure_ascii=False) + "\n")
            roots += 1
        for run in client.list_runs(project_name=project, run_type="llm", start_time=since, limit=limit):
            row = {k: _get(run, k) for k in _ROOT_FIELDS}
            row["extra"] = _model_extra(_get(run, "extra"))
            f.write(json.dumps(_jsonable(row), ensure_ascii=False) + "\n")
            llms += 1
    return roots, llms
