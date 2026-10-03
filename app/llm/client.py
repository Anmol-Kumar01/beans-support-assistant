"""The single client for every LLM call: OpenAI-compatible chat completions.

Groq, Cerebras, Gemini, Ollama, vLLM, or a paid provider are swapped in through the role's
``LLM_<ROLE>_*`` settings; nothing here names a provider or model.

- Thinking is set per role (``thinking`` + ``thinking_control``); ``<think>`` blocks that some
  models still emit are stripped from the content.
- Retries, backoff, and pacing follow app/llm/retry.py. The SDK's own retries are off so
  that policy is the only one.
- ``complete_json`` asks for schema-shaped JSON, validates it with Pydantic, and gives the
  model one chance to repair invalid output.
"""

import asyncio
import copy
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from app.core.config import LLMRoleSettings, ProviderSettings
from app.llm.retry import (
    Pacer,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    call_with_retries,
    pacer_for,
)

T = TypeVar("T", bound=BaseModel)

LLMError = ProviderError
LLMConfigError = ProviderConfigError
LLMRateLimitError = ProviderRateLimitError

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass
class LLMResponse:
    content: str
    model: str
    role: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    attempts: int = 1


def inline_refs(schema: dict) -> dict:
    """Replace $ref with the referenced definition. Some OpenAI-compatible providers do not
    resolve $defs in response_format schemas."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def openai_compatible_client(
    settings: ProviderSettings, env_prefix: str, http_client=None
) -> openai.AsyncOpenAI:
    """SDK client for any OpenAI-compatible endpoint. Raises if a hosted endpoint has no key."""
    if settings.api_key is None and not settings.is_local:
        raise ProviderConfigError(
            f"{env_prefix}API_KEY is not set (needed for {settings.base_url}). "
            "See .env.example for free-tier presets."
        )
    key = settings.api_key.get_secret_value() if settings.api_key else "local"
    client = openai.AsyncOpenAI(
        api_key=key, base_url=settings.base_url, timeout=settings.timeout_s,
        max_retries=0, http_client=http_client,
    )
    return client


class LLMClient:
    def __init__(
        self, role: str, settings: LLMRoleSettings, http_client=None,
        sleep=asyncio.sleep, pacer: Pacer | None = None,
    ):
        self.role = role
        self.settings = settings
        self.env_prefix = f"LLM_{role.upper()}_"
        self._client = openai_compatible_client(settings, self.env_prefix, http_client)
        self._sleep = sleep
        self._pacer = pacer or pacer_for(settings)

    @property
    def model(self) -> str:
        return self.settings.model

    def describe(self) -> dict:
        s = self.settings
        return {
            "role": self.role,
            "base_url": s.base_url,
            "model": s.model,
            "thinking": s.thinking,
            "reasoning_effort": (s.reasoning_effort_on if s.thinking else s.reasoning_effort_off) or None,
        }

    async def aclose(self) -> None:
        await self._client.close()

    def _request(self, messages: list[dict], **overrides: Any) -> dict:
        s = self.settings
        messages = copy.deepcopy(messages)
        kwargs: dict[str, Any] = {
            "model": s.model,
            "messages": messages,
            "max_completion_tokens": overrides.pop("max_output_tokens", None) or s.max_output_tokens,
        }
        if s.temperature is not None:
            kwargs["temperature"] = s.temperature
        extra = dict(s.extra_body)
        if s.thinking_control == "reasoning_effort":
            effort = s.reasoning_effort_on if s.thinking else s.reasoning_effort_off
            if effort:
                kwargs["reasoning_effort"] = effort
        elif s.thinking_control == "chat_template_kwargs":
            extra["chat_template_kwargs"] = {"enable_thinking": s.thinking}
        elif s.thinking_control == "prompt_switch":
            switch = "/think" if s.thinking else "/no_think"
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = f"{messages[0]['content']}\n{switch}"
            else:
                messages.insert(0, {"role": "system", "content": switch})
        if extra:
            kwargs["extra_body"] = extra
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return kwargs

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs = self._request(
            messages, tools=tools, tool_choice=tool_choice,
            response_format=response_format, max_output_tokens=max_output_tokens,
        )
        start = time.perf_counter()
        resp, attempts = await call_with_retries(
            lambda: self._client.chat.completions.create(**kwargs),
            self.settings,
            where=f"{self.role} model {self.model} at {self.settings.base_url}",
            key_env=f"{self.env_prefix}API_KEY",
            pacer=self._pacer,
            sleep=self._sleep,
        )
        if not resp.choices:
            raise LLMError(f"{self.role} model {self.model} returned no choices")
        msg = resp.choices[0].message
        usage = resp.usage
        return LLMResponse(
            content=_THINK.sub("", msg.content or "").strip(),
            model=resp.model or self.model,
            role=self.role,
            tool_calls=[
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ],
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.perf_counter() - start) * 1000,
            attempts=attempts,
        )

    async def stream(self, messages: list[dict], *, max_output_tokens: int | None = None, **extra):
        """Yield answer text as it is generated. Retries apply until the stream opens;
        ``<think>…</think>`` at the start of the reply is dropped."""
        kwargs = self._request(messages, max_output_tokens=max_output_tokens, stream=True, **extra)
        stream, _ = await call_with_retries(
            lambda: self._client.chat.completions.create(**kwargs),
            self.settings,
            where=f"{self.role} model {self.model} at {self.settings.base_url}",
            key_env=f"{self.env_prefix}API_KEY",
            pacer=self._pacer,
            sleep=self._sleep,
        )
        buffer, thinking = "", None  # None = not decided yet
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content or ""
            if not delta:
                continue
            if thinking is None:
                buffer += delta
                if len(buffer) < 7 and "<think>".startswith(buffer.lstrip()):
                    continue
                thinking = buffer.lstrip().startswith("<think>")
                delta, buffer = (buffer, "") if not thinking else ("", buffer)
            if thinking:
                buffer += delta
                if "</think>" in buffer:
                    thinking, delta = False, buffer.split("</think>", 1)[1].lstrip()
                else:
                    continue
            if delta:
                yield delta

    async def complete_json(
        self, messages: list[dict], schema: type[T], *, name: str, repair_attempts: int = 1
    ) -> tuple[T, LLMResponse]:
        """Return a validated ``schema`` instance and the (token-summed) response."""
        spec = inline_refs(schema.model_json_schema())
        mode = self.settings.json_mode
        messages = copy.deepcopy(messages)
        response_format = None
        if mode == "json_schema":
            response_format = {"type": "json_schema", "json_schema": {"name": name, "schema": spec}}
        else:
            if mode == "json_object":
                response_format = {"type": "json_object"}
            instruction = "Reply with only a JSON object matching this JSON Schema:\n" + json.dumps(spec)
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + instruction
            else:
                messages.insert(0, {"role": "system", "content": instruction})
        total_in = total_out = 0
        for attempt in range(repair_attempts + 1):
            resp = await self.chat(messages, response_format=response_format)
            total_in += resp.input_tokens
            total_out += resp.output_tokens
            try:
                parsed = schema.model_validate_json(extract_json(resp.content))
            except ValidationError as exc:
                if attempt == repair_attempts:
                    raise LLMError(f"{self.role} model {self.model}: invalid {name} JSON: {exc}") from exc
                messages += [
                    {"role": "assistant", "content": resp.content},
                    {"role": "user", "content": f"That JSON is invalid: {exc}. Reply with only the corrected JSON."},
                ]
                continue
            resp.input_tokens, resp.output_tokens = total_in, total_out
            return parsed, resp
        raise AssertionError("unreachable")

    async def list_models(self) -> list[str]:
        page = await self._client.models.list()
        return [m.id for m in page.data]
