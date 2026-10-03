import asyncio
import json
from pathlib import Path

from evals.schema import EvalQuestion
from evals.targets.legacy_traces import LegacyTracesTarget, message_text
from evals.tools.export_langsmith import export_runs
from evals.tools.scrub_pii import Scrubber

ARTICLE = "https://beansai.zendesk.com/hc/en-us/articles/111-Drivers-Work-Schedule"
DRAFT = "Drivers request time off from the Calendar tab."
FINAL = f"Drivers request time off from the Calendar tab. [Drivers: Work Schedule and Time Off Requests]({ARTICLE})"


def rag_run(run_id, question, draft, start, end, context=None):
    return {
        "id": run_id, "trace_id": run_id, "parent_run_id": None, "name": "RunnableWithMessageHistory",
        "run_type": "chain", "start_time": start, "end_time": end,
        "inputs": {"input": question},
        "outputs": {"answer": draft, "context": context or []},
    }


def final_run(run_id, question, draft, final, start, end):
    # LangChain JS serializes the AIMessage like this in run outputs.
    message = {"lc": 1, "type": "constructor", "id": ["langchain_core", "messages", "AIMessage"],
               "kwargs": {"content": final, "tool_calls": []}}
    return {
        "id": run_id, "trace_id": run_id, "parent_run_id": None, "name": "RunnableSequence",
        "run_type": "chain", "start_time": start, "end_time": end,
        "inputs": {"query": question, "answer": draft, "metadata": "[]"},
        "outputs": {"output": message},
    }


def llm_run(trace_id, model):
    return {"id": f"llm-{trace_id}", "trace_id": trace_id, "parent_run_id": trace_id, "run_type": "llm",
            "extra": {"metadata": {"ls_model_name": model}}}


def write(path: Path, runs: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in runs))
    return path


def q(**kw) -> EvalQuestion:
    base = dict(id="k1", question="How do drivers request time off?", question_type="simple",
                expected_source_ids=["zendesk:111"], reference_answer="Calendar tab.")
    return EvalQuestion.model_validate({**base, **kw})


CONTEXT = [
    {"lc": 1, "type": "constructor", "id": ["langchain_core", "documents", "Document"],
     "kwargs": {"page_content": "...", "metadata": {"title": "Access and Add Gate Codes"}, "id": "vid1 Part 2"}},
    {"pageContent": "...", "metadata": {}, "id": "111"},
]


def test_joins_rag_and_citation_runs_and_scores_the_final_text(tmp_path, catalog):
    path = write(tmp_path / "runs.jsonl", [
        rag_run("r1", "how do drivers request time off", DRAFT, "2025-07-15T19:48:30.000Z", "2025-07-15T19:48:32.000Z", CONTEXT),
        final_run("f1", "how do drivers request time off", DRAFT, FINAL, "2025-07-15T19:48:32.100Z", "2025-07-15T19:48:33.500Z"),
        llm_run("f1", "gpt-4o-mini"),
    ])
    target = LegacyTracesTarget(path, catalog, 1000, Scrubber())
    assert target.describe()["requests"] == 1
    r = asyncio.run(target.answer(q(), "s"))
    assert r.error is None
    assert r.answer == FINAL
    assert [c.document_ids for c in r.citations] == [["zendesk:111"]]
    assert [x.document_id for x in r.retrieved] == ["youtube:vid1", "zendesk:111"]  # recall is scorable
    assert r.total_ms == 3500 and r.ttft_ms == 3500
    assert r.models == {"answer": "gpt-4o-mini"}
    assert r.evidence[0].document_id == "zendesk:111"


def test_most_recent_trace_wins_and_trace_tag_overrides(tmp_path, catalog):
    old = "Old answer."
    path = write(tmp_path / "runs.jsonl", [
        final_run("f-old", "How do drivers request time off?", old, old, "2025-07-01T00:00:00Z", "2025-07-01T00:00:01Z"),
        final_run("f-new", "How do drivers request time off?", DRAFT, FINAL, "2025-07-20T00:00:00Z", "2025-07-20T00:00:01Z"),
    ])
    target = LegacyTracesTarget(path, catalog, 1000, Scrubber())
    assert asyncio.run(target.answer(q(), "s")).answer == FINAL
    assert asyncio.run(target.answer(q(tags=["trace:f-old"]), "s")).answer == old
    assert asyncio.run(target.answer(q(), "s")).retrieved is None  # no RAG run exported: recall N/A


def test_unmatched_and_follow_ups_report_errors(tmp_path, catalog):
    path = write(tmp_path / "runs.jsonl", [
        final_run("f1", "How do drivers request time off?", DRAFT, FINAL, "2025-07-20T00:00:00Z", "2025-07-20T00:00:01Z"),
    ])
    target = LegacyTracesTarget(path, catalog, 1000, Scrubber())
    miss = asyncio.run(target.answer(q(question="Something never asked"), "s"))
    assert "no exported LangSmith trace" in miss.error
    follow = q(question_type="follow_up", question="How do drivers request time off?", prior_turns=["hi"])
    assert "trace:<run id>" in asyncio.run(target.answer(follow, "s")).error


def test_matching_uses_scrubbed_text(tmp_path, catalog):
    path = write(tmp_path / "runs.jsonl", [
        final_run("f1", "my email is a.b@x.com, how do I clock out?", "Use Clock Out.", "Use Clock Out.",
                  "2025-07-20T00:00:00Z", "2025-07-20T00:00:01Z"),
    ])
    target = LegacyTracesTarget(path, catalog, 1000, Scrubber())
    r = asyncio.run(target.answer(q(question="my email is [EMAIL], how do I clock out?"), "s"))
    assert r.answer == "Use Clock Out."


def test_message_text_shapes():
    assert message_text("plain") == "plain"
    assert message_text({"content": "c"}) == "c"
    assert message_text({"output": {"kwargs": {"content": "k"}}}) == "k"
    assert message_text({"generations": [[{"text": "g"}]]}) == "g"
    assert message_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "ab"


class FakeRun(dict):
    def __getattr__(self, name):
        return self.get(name)


class FakeLangSmith:
    def __init__(self, roots, llms):
        self.roots, self.llms, self.calls = roots, llms, []

    def list_runs(self, **kw):
        self.calls.append(kw)
        return iter(self.llms if kw.get("run_type") == "llm" else self.roots)


def test_export_scrubs_user_text_and_round_trips(tmp_path, catalog):
    roots = [
        FakeRun(rag_run("r1", "I'm Maria, call 555-123-4567: time off?", DRAFT, "2025-07-15T00:00:00Z", "2025-07-15T00:00:01Z", CONTEXT)),
        FakeRun(final_run("f1", "I'm Maria, call 555-123-4567: time off?", DRAFT, FINAL, "2025-07-15T00:00:01Z", "2025-07-15T00:00:02Z")),
    ]
    roots[0]["inputs"]["chat_history"] = [{"content": "earlier turn from Maria"}]
    client = FakeLangSmith(roots, [FakeRun(llm_run("f1", "gpt-4o-mini"), inputs={"messages": "secret prompt"})])
    out = tmp_path / "var" / "runs.jsonl"
    assert export_runs("beans", out, Scrubber(names=["Maria"]), client=client) == (2, 1)
    text = out.read_text()
    assert "Maria" not in text and "555-123-4567" not in text and "secret prompt" not in text
    assert "[PHONE]" in text and ARTICLE in text  # links in answers survive
    assert client.calls[0]["is_root"] is True and client.calls[1]["run_type"] == "llm"

    target = LegacyTracesTarget(out, catalog, 1000, Scrubber(names=["Maria"]))
    r = asyncio.run(target.answer(q(question="I'm Maria, call 555-123-4567: time off?"), "s"))
    assert r.error is None and r.models == {"answer": "gpt-4o-mini"} and r.retrieved
