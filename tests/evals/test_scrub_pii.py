import json

import pytest

from evals.__main__ import main
from evals.tools.scrub_pii import Scrubber, scrub_file, scrub_question, vocabulary_from_texts


@pytest.mark.parametrize("text, expected", [
    ("call +1 (555) 123-4567 or 555.123.4567", "call [PHONE] or [PHONE]"),
    ("email a.b@x.co.uk", "email [EMAIL]"),
    ("account 329865ff87f7411fb5fced02b3b07be7 and b3b07be7", "account [ACCOUNT_ID] and [ACCOUNT_ID]"),
    ("id 123e4567-e89b-12d3-a456-426614174000", "id [ACCOUNT_ID]"),
    ("tracking 794612345678 on route 1042", "tracking [ID] on route 1042"),
    ("hi, my name is John Smith and I can't log in", "hi, my name is [NAME] and I can't log in"),
    ("I am Having trouble", "I am Having trouble"),  # not a name
    ("see https://beansai.zendesk.com/hc/en-us/articles/10022296840087-X", "see https://beansai.zendesk.com/hc/en-us/articles/10022296840087-X"),
])
def test_scrub_patterns(text, expected):
    assert Scrubber().scrub(text) == expected


def test_names_file_and_review_candidates():
    s = Scrubber(names=["Maria Lopez"], vocabulary=vocabulary_from_texts(["Add a driver to the Lasso tool"]))
    assert s.scrub("maria lopez cannot clock out") == "[NAME] cannot clock out"
    assert s.review_candidates("How do I add Driver Pedro to Lasso?") == ["Pedro"]
    assert s.contains_pii("mail a@b.co") and not s.contains_pii("How do I use Lasso?")


def test_scrub_question_tags_and_keeps_notes():
    q = {"id": "x", "question": "I'm Pedro, email p@x.io", "prior_turns": ["call 555-123-4567"],
         "notes": "session 12671261", "tags": ["logdir:Logging", "pii:review"]}
    out, changed, review = scrub_question(q, Scrubber())
    assert out["question"] == "I'm [NAME], email [EMAIL]" and out["prior_turns"] == ["call [PHONE]"]
    assert out["notes"] == "session 12671261"  # labeller-only field, never sent to APIs
    assert changed and review == []
    assert out["tags"] == ["logdir:Logging", "pii:scrubbed"]


def test_scrub_file_and_validate_gate(tmp_path, capsys, monkeypatch, legacy_dir):
    monkeypatch.setenv("EVAL_LEGACY_SOURCES_DIR", str(legacy_dir))
    from evals.config import get_settings

    get_settings.cache_clear()
    ds = tmp_path / "ds.jsonl"
    rows = [
        {"id": "a", "question": "my email is a@b.co, how to clock out?", "question_type": "simple", "labelled": False},
        {"id": "b", "question": "How do I add Driver Pedro?", "question_type": "simple", "labelled": False},
    ]
    ds.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    try:
        assert main(["validate", str(ds)]) == 1
        assert "a: contains personal data" in capsys.readouterr().out
        report = scrub_file(ds, ds, Scrubber())
        assert [(qid, changed) for qid, changed, _ in report] == [("a", True), ("b", False)]
        assert report[1][2] == ["Pedro"]
        assert main(["validate", str(ds)]) == 0
        assert "tagged pii:review" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
