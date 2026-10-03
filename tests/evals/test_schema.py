import json

import pytest

from evals.schema import EvalQuestion, ExpectedBehavior, QuestionType, load_questions


def q(**kw):
    base = {
        "id": "q1",
        "question": "How do drivers request time off?",
        "question_type": "simple",
        "expected_source_ids": ["zendesk:111"],
        "reference_answer": "From the Calendar tab in the Hub.",
    }
    return EvalQuestion.model_validate({**base, **kw})


def test_default_behavior_from_type():
    assert q().expected_behavior == ExpectedBehavior.ANSWER
    u = q(question_type="unanswerable", expected_source_ids=[], reference_answer=None)
    assert u.expected_behavior == ExpectedBehavior.NOT_FOUND
    assert q(question_type="adversarial").expected_behavior == ExpectedBehavior.RESIST


@pytest.mark.parametrize(
    "kw, msg",
    [
        ({"reference_answer": None}, "reference_answer"),
        ({"expected_source_ids": []}, "expected_source_ids"),
        ({"expected_source_ids": ["111"]}, "bad source id"),
        ({"question_type": "follow_up"}, "prior_turns"),
        ({"question_type": "unanswerable"}, "must not list expected sources"),
    ],
)
def test_validation_errors(kw, msg):
    with pytest.raises(ValueError, match=msg):
        q(**kw)


def test_structured_data_needs_no_sources():
    s = q(question_type="structured_data", expected_source_ids=[], expected_tools=["get_route_status"])
    assert s.question_type == QuestionType.STRUCTURED_DATA


def test_drafts_skip_label_checks():
    d = q(labelled=False, reference_answer=None, expected_source_ids=[])
    assert not d.labelled


def test_load_questions_skips_drafts_and_rejects_duplicates(tmp_path):
    good = q().model_dump(mode="json")
    draft = {**good, "id": "q2", "labelled": False, "reference_answer": None}
    path = tmp_path / "set.jsonl"
    path.write_text("\n".join(["// comment", json.dumps(good), json.dumps(draft)]))
    assert [x.id for x in load_questions(path)] == ["q1"]
    assert len(load_questions(path, include_drafts=True)) == 2

    path.write_text(json.dumps(good) + "\n" + json.dumps(good))
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(path)
