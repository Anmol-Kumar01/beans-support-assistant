from pathlib import Path

from app.core.config import AnswerLLMSettings, ModelSettings, same_model


def _env_example() -> dict[str, str]:
    out = {}
    for line in Path(".env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def test_defaults_match_env_example_presets():
    """.env.example documents the defaults; they must not drift apart."""
    env, m = _env_example(), ModelSettings.from_env(env_file=None)
    for role in ("answer", "small", "judge"):
        s = m.llm(role)
        assert env[f"LLM_{role.upper()}_BASE_URL"] == s.base_url
        assert env[f"LLM_{role.upper()}_MODEL"] == s.model
    assert env["EMBEDDING_MODEL"] == m.embedding.model
    assert int(env["EMBEDDING_DIM"]) == m.embedding.dim == 1536
    assert env["RERANKER_MODEL"] == m.reranker.model
    assert env["RERANKER_PROVIDER"] == m.reranker.provider
    assert not any(v.startswith(("gsk_", "sk-", "jina_", "AIza", "AQ.")) for v in env.values()), "real key in .env.example"


def test_each_role_reads_its_own_env_vars(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_JUDGE_MODEL", "qwen3:14b")
    monkeypatch.setenv("LLM_JUDGE_THINKING", "false")
    monkeypatch.setenv("LLM_ANSWER_API_KEY", "secret-groq")
    monkeypatch.setenv("LLM_ANSWER_EXTRA_BODY", '{"service_tier": "auto"}')
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    monkeypatch.setenv("RERANKER_PROVIDER", "cohere")
    monkeypatch.setenv("RERANKER_CANDIDATES", "10")
    m = ModelSettings.from_env(env_file=None)
    assert (m.judge.model, m.judge.is_local, m.judge.thinking) == ("qwen3:14b", True, False)
    assert m.answer.api_key.get_secret_value() == "secret-groq"
    assert m.answer.extra_body == {"service_tier": "auto"}
    assert m.small.api_key is None  # roles do not share keys unless configured
    assert m.embedding.dim == 768 and m.embedding.config_name == "gemini-embedding-001-768"
    assert (m.reranker.provider, m.reranker.candidates) == ("cohere", 10)


def test_env_file_is_read(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_ANSWER_MODEL=my-model\nLLM_ANSWER_MAX_RETRIES=2\n")
    s = AnswerLLMSettings(_env_file=env)
    assert (s.model, s.max_retries) == ("my-model", 2)


def test_thinking_off_by_default_for_answer_and_small():
    m = ModelSettings.from_env(env_file=None)
    assert m.answer.thinking is False and m.small.thinking is False


def test_manifest_has_every_role_and_no_keys(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_API_KEY", "top-secret")
    manifest = ModelSettings.from_env(env_file=None).manifest()
    assert set(manifest) == {"answer", "small", "judge", "embedding", "reranker"}
    assert "top-secret" not in str(manifest)


def test_self_grading_detection(monkeypatch):
    assert same_model("openai/gpt-oss-20b", "gpt-oss-20b")
    assert same_model("models/gemini-3.5-flash", "gemini-3.5-flash")
    assert not same_model("openai/gpt-oss-20b", "openai/gpt-oss-120b")
    assert not ModelSettings.from_env(env_file=None).self_grading()
    monkeypatch.setenv("LLM_JUDGE_MODEL", "gpt-oss-20b")
    assert ModelSettings.from_env(env_file=None).self_grading()
