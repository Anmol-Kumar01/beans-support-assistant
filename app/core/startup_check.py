"""Startup check: every configured model is reachable and loads.

  python -m app.core.startup_check                  # all LLM roles + embeddings + reranker
  python -m app.core.startup_check --roles judge --skip-retrieval
  python -m app.core.startup_check --no-probe       # list models only, send no chat request

For each LLM role: the API key is set, the endpoint answers ``GET /models``, the
configured model is listed, and one tiny chat request succeeds. The request matters:
providers keep retired models in the listing (Gemini lists gemini-2.5-flash but refuses it
for new keys). Embeddings: one request for one text returns
EMBEDDING_DIM dims. Reranker: one request ranks a relevant passage above an irrelevant one.
Exits non-zero with a message per failure.
"""

import argparse
import asyncio
import difflib
import logging
import sys
from dataclasses import dataclass

import openai

from app.core.config import ModelSettings, get_model_settings, same_model
from app.llm.client import LLMClient, LLMError
from app.llm.retry import ProviderError

log = logging.getLogger("app.startup")

LLM_ROLES = ("answer", "small", "judge")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    warning: bool = False


class StartupCheckError(RuntimeError):
    def __init__(self, results: list[CheckResult]):
        self.results = results
        super().__init__(format_results(results))


def _listed(model: str, ids: list[str]) -> bool:
    # Gemini lists "models/gemini-2.5-flash"; Groq lists "openai/gpt-oss-20b".
    return any(i == model or i.removeprefix("models/") == model for i in ids)


async def check_llm_role(role: str, models: ModelSettings, probe: bool = True, http_client=None) -> CheckResult:
    s = models.llm(role)
    name = f"llm:{role}"
    try:
        client = LLMClient(role, s, http_client=http_client)
    except LLMError as exc:
        return CheckResult(name, False, str(exc))
    where = f"{s.base_url} ({client.env_prefix}BASE_URL)"
    try:
        try:
            ids = await client.list_models()
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            return CheckResult(name, False, f"{where} rejected {client.env_prefix}API_KEY (HTTP {exc.status_code}).")
        except openai.APIStatusError as exc:
            return CheckResult(name, False, f"{where} returned HTTP {exc.status_code} for GET /models.")
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            return CheckResult(name, False, f"can't reach {where}: {exc}")
        if not _listed(s.model, ids):
            close = difflib.get_close_matches(s.model, [i.removeprefix("models/") for i in ids], n=3, cutoff=0.4)
            hint = f" Close matches: {', '.join(close)}." if close else ""
            return CheckResult(
                name, False, f"model {s.model!r} ({client.env_prefix}MODEL) is not offered by {s.base_url}.{hint}"
            )
        detail = f"{s.model} at {s.base_url}"
        if probe:
            try:
                resp = await client.chat(
                    [{"role": "system", "content": "Reply with the word OK."}, {"role": "user", "content": "ping"}],
                    max_output_tokens=200,
                )
            except LLMError as exc:
                return CheckResult(name, False, f"probe failed: {exc}")
            detail += f", probe ok in {resp.latency_ms:.0f} ms"
        return CheckResult(name, True, detail)
    finally:
        if http_client is None:  # an injected client belongs to the caller
            await client.aclose()


def check_self_grading(models: ModelSettings) -> CheckResult | None:
    if same_model(models.judge.model, models.answer.model):
        return CheckResult(
            "judge-bias", True,
            f"judge and answer both use {models.judge.model}: the judge grades its own answers "
            "(self-grading bias). Use a different model family for LLM_JUDGE_MODEL.",
            warning=True,
        )
    return None


_QUERY = "How do drivers request time off?"
_RELEVANT = "Drivers request time off from the Calendar tab in the Beans Route app."
_IRRELEVANT = "Upload a FedEx manifest from the Routes page."


async def check_embedding(models: ModelSettings, http_client=None) -> CheckResult:
    from app.retrieval.embeddings import Embedder

    s = models.embedding
    try:
        embedder = Embedder(s, http_client=http_client)
    except ProviderError as exc:
        return CheckResult("embedding", False, str(exc))
    try:
        [vec] = await embedder.embed([_QUERY])
    except ProviderError as exc:
        return CheckResult("embedding", False, f"{s.model} (EMBEDDING_MODEL): {exc}")
    finally:
        if http_client is None:
            await embedder.aclose()
    return CheckResult("embedding", True, f"{s.model} at {s.base_url}, {len(vec)} dims")


async def check_reranker(models: ModelSettings, http_client=None) -> CheckResult:
    from app.retrieval.reranker import Reranker

    s = models.reranker
    try:
        reranker = Reranker(s, http_client=http_client)
    except ProviderError as exc:
        return CheckResult("reranker", False, str(exc))
    try:
        good, bad = await reranker.score(_QUERY, [_RELEVANT, _IRRELEVANT])
    except ProviderError as exc:
        return CheckResult("reranker", False, f"{s.model} (RERANKER_MODEL): {exc}")
    finally:
        if http_client is None:
            await reranker.aclose()
    if not good > bad:
        return CheckResult("reranker", False, f"{s.model} ranked an irrelevant passage higher ({bad:.3f} >= {good:.3f})")
    return CheckResult("reranker", True, f"{s.provider} {s.model}, {s.candidates} candidates per query")


async def run_startup_checks(
    models: ModelSettings,
    roles: tuple[str, ...] = LLM_ROLES,
    retrieval: bool = True,
    probe: bool = True,
    http_client=None,
    rerank_http_client=None,
) -> list[CheckResult]:
    """``http_client`` (httpx2, for the OpenAI SDK) and ``rerank_http_client`` (httpx) are
    for tests."""
    checks = [check_llm_role(r, models, probe, http_client) for r in roles]
    if retrieval:
        checks += [check_embedding(models, http_client), check_reranker(models, rerank_http_client)]
    results = list(await asyncio.gather(*checks))
    if "judge" in roles and (warning := check_self_grading(models)):
        results.append(warning)
    for r in results:
        if r.warning:
            log.warning("%s: %s", r.name, r.detail)
    return results


async def require_models(models: ModelSettings, **kwargs) -> list[CheckResult]:
    """Run the checks and raise StartupCheckError if any failed."""
    results = await run_startup_checks(models, **kwargs)
    if any(not r.ok for r in results):
        raise StartupCheckError(results)
    return results


def format_results(results: list[CheckResult]) -> str:
    mark = lambda r: "WARN" if r.warning else ("ok  " if r.ok else "FAIL")  # noqa: E731
    lines = [f"  {mark(r)} {r.name:<11} {r.detail}" for r in results]
    failed = sum(not r.ok for r in results)
    lines.append(f"{failed} check(s) failed." if failed else "All model checks passed.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m app.core.startup_check")
    p.add_argument("--roles", default=",".join(LLM_ROLES), help="comma-separated LLM roles to check")
    p.add_argument("--skip-retrieval", action="store_true", help="skip the embedding and reranker checks")
    p.add_argument("--no-probe", action="store_true", help="skip the one-request chat probe per role")
    args = p.parse_args(argv)
    roles = tuple(r for r in args.roles.split(",") if r)
    unknown = set(roles) - set(LLM_ROLES)
    if unknown:
        p.error(f"unknown roles: {', '.join(sorted(unknown))}")
    results = asyncio.run(
        run_startup_checks(get_model_settings(), roles=roles, retrieval=not args.skip_retrieval, probe=not args.no_probe)
    )
    print(format_results(results))
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
