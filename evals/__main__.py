"""Eval command line.

  python -m evals validate evals/datasets/golden.jsonl
  python -m evals scrub evals/datasets/drafts/from_logs.jsonl --out evals/datasets/drafts/from_logs.jsonl
  python -m evals run --dataset evals/datasets/golden.jsonl [--limit 20] [--no-judge]
  python -m evals compare evals/reports/<baseline_run> evals/reports/<candidate_run>
  python -m evals draft-from-logs <log dir> ... --out evals/datasets/drafts/from_logs.jsonl

The judge is the LLM_JUDGE_* role (app/core/config.py). No OpenAI key is needed.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from evals.config import EvalSettings, get_settings

log = logging.getLogger("evals")


def _scrubber(settings: EvalSettings):
    """PII scrubber whose name heuristics know the knowledge-base vocabulary."""
    from evals.catalog import Catalog
    from evals.tools.scrub_pii import Scrubber, vocabulary_from_texts

    vocab = None
    try:
        catalog = Catalog.from_dir(settings.sources_dir)
        vocab = vocabulary_from_texts(d.text for d in catalog.documents.values())
    except FileNotFoundError:
        log.warning("knowledge base not found at %s; name review will be noisier", settings.sources_dir)
    return Scrubber.from_files(settings.pii_names_file, vocab)


def _pii_problems(questions, scrubber) -> list[str]:
    from evals.tools.scrub_pii import question_texts

    return [q.id for q in questions if any(scrubber.contains_pii(t) for t in question_texts(q.model_dump()))]


def _cmd_validate(args) -> int:
    from evals.catalog import Catalog
    from evals.schema import load_questions

    settings = get_settings()
    qs = load_questions(Path(args.dataset), include_drafts=True)
    catalog = Catalog.from_dir(settings.sources_dir)
    unknown = [(q.id, s) for q in qs for s in q.expected_source_ids if s not in catalog.documents]
    for qid, sid in unknown:
        print(f"{qid}: unknown source id {sid}")
    pii = _pii_problems(qs, _scrubber(settings))
    for qid in pii:
        print(f"{qid}: contains personal data; run `python -m evals scrub` first")
    review = [q.id for q in qs if "pii:review" in q.tags]
    if review:
        print(f"{len(review)} question(s) tagged pii:review; check them for names, then remove the tag")
    print(f"{len(qs)} questions ({sum(q.labelled for q in qs)} labelled)")
    for t, n in sorted(Counter(q.question_type.value for q in qs).items()):
        print(f"  {t}: {n}")
    return 1 if unknown or pii else 0


def _cmd_run(args) -> int:
    from app.core.config import get_model_settings
    from evals.runner import run_eval
    from evals.targets.chat_server import ChatServerTarget
    from evals.schema import load_questions

    settings = get_settings()
    models = get_model_settings()
    if args.concurrency:
        settings.concurrency = args.concurrency
    qs = load_questions(Path(args.dataset))
    if args.types:
        wanted = set(args.types.split(","))
        qs = [q for q in qs if q.question_type in wanted]
    if args.limit:
        qs = qs[: args.limit]
    if not qs:
        print("no labelled questions selected", file=sys.stderr)
        return 1
    if not args.allow_pii and (pii := _pii_problems(qs, _scrubber(settings))):
        print(f"{len(pii)} question(s) contain personal data ({', '.join(pii[:5])}...). "
              "Run `python -m evals scrub` first; question text is sent to hosted APIs.", file=sys.stderr)
        return 1

    judge = None
    if not args.no_judge:
        judge = _make_judge(models, settings, check=not args.skip_checks)
        if judge is None:
            return 2

    token = settings.server_token.get_secret_value() if settings.server_token else None
    target = ChatServerTarget(
        settings.server_url, settings.server_chat_path, token, settings.server_timeout_s
    )
    run_dir = asyncio.run(run_eval(qs, target, judge, settings, Path(args.dataset), args.name, models))
    print((run_dir / "report.md").read_text())
    print(f"results: {run_dir}")
    return 0


def _make_judge(models, settings: EvalSettings, check: bool):
    """The LLM_JUDGE_* judge, after checking its endpoint and model. None if unusable."""
    from app.core.startup_check import format_results, run_startup_checks
    from app.llm.client import LLMClient, LLMError
    from evals.metrics.judge import LLMJudge

    if check:
        results = asyncio.run(run_startup_checks(models, roles=("judge",), retrieval=False))
        if any(not r.ok for r in results):
            print("judge check failed (use --no-judge for deterministic metrics only):\n"
                  + format_results(results), file=sys.stderr)
            return None
    elif models.self_grading():
        log.warning("judge and answer both use %s: self-grading bias", models.judge.model)
    try:
        return LLMJudge(LLMClient("judge", models.judge), settings)
    except LLMError as exc:
        print(f"judge unavailable: {exc}", file=sys.stderr)
        return None


def _cmd_scrub(args) -> int:
    from evals.tools.scrub_pii import scrub_file

    settings = get_settings()
    if args.names_file:
        settings.pii_names_file = Path(args.names_file)
    report = scrub_file(Path(args.dataset), Path(args.out or args.dataset), _scrubber(settings))
    changed = [qid for qid, c, _ in report if c]
    review = [(qid, cands) for qid, _, cands in report if cands]
    print(f"{len(report)} questions: {len(changed)} scrubbed, {len(review)} to review for names")
    for qid, cands in review:
        print(f"  {qid}: {', '.join(cands)}")  # printed only, never written to the dataset
    return 0


def _cmd_compare(args) -> int:
    from evals.compare import compare_summaries, render_comparison

    settings = get_settings()
    load = lambda d: json.loads((Path(d) / "summary.json").read_text())  # noqa: E731
    results = compare_summaries(load(args.baseline), load(args.candidate), settings.gates)
    print(render_comparison(results))
    return 1 if any(r.passed is False for r in results) else 0


def _cmd_draft(args) -> int:
    from evals.tools.draft_from_logs import draft_questions, write_drafts

    drafts = draft_questions([Path(d) for d in args.log_dirs], _scrubber(get_settings()))
    write_drafts(drafts, Path(args.out))
    print(f"wrote {len(drafts)} draft questions to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m evals")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a dataset file and its source IDs")
    v.add_argument("dataset")
    v.set_defaults(fn=_cmd_validate)

    r = sub.add_parser("run", help="run a dataset against the chat server")
    r.add_argument("--dataset", required=True)
    r.add_argument("--skip-checks", action="store_true", help="skip the judge startup check")
    r.add_argument("--allow-pii", action="store_true", help="run even if questions contain personal data")
    r.add_argument("--limit", type=int)
    r.add_argument("--types", help="comma-separated question types")
    r.add_argument("--concurrency", type=int)
    r.add_argument("--no-judge", action="store_true", help="deterministic metrics only")
    r.add_argument("--name", help="suffix for the run folder")
    r.set_defaults(fn=_cmd_run)

    c = sub.add_parser("compare", help="release gate: candidate run vs baseline run")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.set_defaults(fn=_cmd_compare)

    s = sub.add_parser("scrub", help="redact personal data from a dataset")
    s.add_argument("dataset")
    s.add_argument("--out", help="output file (default: overwrite the input)")
    s.add_argument("--names-file", help="known names to redact, one per line")
    s.set_defaults(fn=_cmd_scrub)

    d = sub.add_parser("draft-from-logs", help="extract unlabelled questions from bot logs")
    d.add_argument("log_dirs", nargs="+")
    d.add_argument("--out", required=True)
    d.set_defaults(fn=_cmd_draft)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
