"""Retry, backoff, and request pacing shared by every hosted-model client
(chat, embeddings, rerank), so all providers get the same free-tier handling.

- 429: wait for Retry-After (or backoff) and retry. A Retry-After longer than
  ``backoff_max_s`` means a quota window, not a burst, so it fails at once.
- 408/409/5xx and network errors: exponential backoff with jitter.
- 401/403/404 and other 4xx: fail at once with a message naming the env var to fix.
- Pacing: clients on the same host with the same key share one requests-per-minute
  budget (the lowest ``max_requests_per_minute`` among them).
"""

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Literal, TypeVar
from urllib.parse import urlparse

import httpx
import openai

from app.core.config import ProviderSettings

log = logging.getLogger("app.providers")
R = TypeVar("R")


class ProviderError(RuntimeError):
    """A failed model API call, with a message naming the provider and what to fix."""


class ProviderConfigError(ProviderError):
    """Wrong key, URL, or model: retrying will not help."""


class ProviderRateLimitError(ProviderError):
    pass


FailureKind = Literal["rate_limit", "retryable", "auth", "not_found", "fatal"]


@dataclass
class Failure:
    kind: FailureKind
    status: int | None = None
    retry_after_s: float | None = None
    message: str = ""


def retry_after_s(headers) -> float | None:
    if ms := headers.get("retry-after-ms"):
        try:
            return float(ms) / 1000
        except ValueError:
            pass
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _by_status(status: int, headers, message: str) -> Failure:
    if status == 429:
        return Failure("rate_limit", status, retry_after_s(headers), message)
    if status in (401, 403):
        return Failure("auth", status, message=message)
    if status == 404:
        return Failure("not_found", status, message=message)
    if status in (408, 409) or status >= 500:
        return Failure("retryable", status, message=message)
    return Failure("fatal", status, message=message)


def classify(exc: BaseException) -> Failure | None:
    """Map an SDK or httpx error to a retry decision. None = not an API error."""
    if isinstance(exc, openai.APIStatusError):
        return _by_status(exc.status_code, exc.response.headers, exc.message)
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return Failure("retryable", message=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        return _by_status(exc.response.status_code, exc.response.headers, exc.response.text[:300])
    if isinstance(exc, httpx.TransportError):
        return Failure("retryable", message=f"{type(exc).__name__}: {exc}")
    return None


class Pacer:
    def __init__(self) -> None:
        self._rpms: list[float] = []
        self._next = 0.0

    def register(self, rpm: float | None) -> None:
        if rpm:
            self._rpms.append(rpm)

    def reserve(self, now: float) -> float:
        """Seconds to wait before the next request may start."""
        if not self._rpms:
            return 0.0
        wait = max(0.0, self._next - now)
        self._next = max(now, self._next) + 60.0 / min(self._rpms)
        return wait


_PACERS: dict[tuple[str, str], Pacer] = {}


def pacer_for(settings: ProviderSettings) -> Pacer:
    key = settings.api_key.get_secret_value() if settings.api_key else ""
    ident = (urlparse(settings.base_url).hostname or "", hashlib.sha256(key.encode()).hexdigest()[:12])
    pacer = _PACERS.setdefault(ident, Pacer())
    pacer.register(settings.max_requests_per_minute)
    return pacer


def reset_pacers() -> None:
    _PACERS.clear()


def _backoff(settings: ProviderSettings, attempt: int) -> float:
    return min(settings.backoff_max_s, settings.backoff_base_s * 2**attempt) * (0.5 + random.random() / 2)


async def call_with_retries(
    fn: Callable[[], Awaitable[R]],
    settings: ProviderSettings,
    *,
    where: str,
    key_env: str,
    pacer: Pacer,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[R, int]:
    """Run ``fn`` under the retry policy. Returns (result, attempts)."""
    for attempt in range(settings.max_retries + 1):
        if wait := pacer.reserve(time.monotonic()):
            await sleep(wait)
        try:
            return await fn(), attempt + 1
        except Exception as exc:
            failure = classify(exc)
            if failure is None:
                raise
            last = attempt == settings.max_retries
            if failure.kind == "auth":
                raise ProviderConfigError(f"{where} rejected {key_env} (HTTP {failure.status}).") from exc
            if failure.kind == "not_found":
                detail = f": {failure.message}" if failure.message else ""
                raise ProviderConfigError(f"{where}: model or endpoint not found (HTTP 404){detail}") from exc
            if failure.kind == "fatal":
                raise ProviderError(f"{where} returned HTTP {failure.status}: {failure.message}") from exc
            if failure.kind == "rate_limit":
                wait = failure.retry_after_s
                if wait is not None and wait > settings.backoff_max_s:
                    raise ProviderRateLimitError(
                        f"{where} is rate limited for {wait:.0f}s (quota exhausted?). "
                        "Lower EVAL_CONCURRENCY, set max_requests_per_minute, or switch provider in .env."
                    ) from exc
                if last:
                    raise ProviderRateLimitError(
                        f"{where} still rate limited after {settings.max_retries} retries."
                    ) from exc
                wait = wait if wait is not None else _backoff(settings, attempt)
            else:
                if last:
                    detail = f"HTTP {failure.status}" if failure.status else failure.message
                    raise ProviderError(f"{where} failed after {settings.max_retries} retries ({detail}).") from exc
                wait = _backoff(settings, attempt)
            log.warning("%s: %s, retry %d/%d in %.1fs", where, failure.kind, attempt + 1, settings.max_retries, wait)
            await sleep(wait)
    raise AssertionError("unreachable")
