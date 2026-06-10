"""IDUN chat client + rate limiter — Phase 2.1.

A thin async wrapper over IDUN's OpenAI-compatible chat-completions endpoint
(``POST {base_url}/v1/chat/completions``). It owns three transport concerns and
nothing else (no prompt knowledge, no DB): **rate limiting**, **retrying
transient failures**, and **pulling the message content out of the response**.
Parsing the model's answer is :mod:`src.nlp.prompt`'s job; iterating articles is
the scorer's.

Like the scrapers, it takes an injected ``httpx.AsyncClient`` so tests can stub
transport with ``httpx.MockTransport`` and never hit the network.

## Rate limiting

IDUN allows **20 requests/min and 300k tokens/min**. We serialise requests with a
minimum interval (default 18 req/min for headroom). The prompt caps the article
body (see ``prompt.DEFAULT_MAX_BODY_CHARS``), so each request is on the order of
~10k tokens; at ≤18 req/min that is ≤~180k tokens/min, comfortably under the
token cap. We therefore enforce only the request rate and let the body cap keep
us under the token rate — no tokeniser needed. Run off-peak (18:00–06:00 /
weekends) as the project notes recommend.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://llm.hpc.ntnu.no"
# Headroom under the documented 20 req/min ceiling.
DEFAULT_REQ_PER_MIN = 18
# HTTP statuses worth retrying (overload / transient server faults). Auth (401)
# and bad-request (400) are not retried — they will not fix themselves.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Async minimum-interval limiter shared across concurrent callers.

    ``acquire()`` returns only once at least ``60 / max_per_minute`` seconds have
    passed since the previous acquire, so a burst of coroutines is paced out to a
    steady request rate.
    """

    def __init__(self, max_per_minute: int = DEFAULT_REQ_PER_MIN) -> None:
        self._min_interval = 60.0 / max_per_minute
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = loop.time() + self._min_interval


class IdunError(RuntimeError):
    """An IDUN request failed after exhausting retries (or for a fatal status)."""


class IdunClient:
    """Calls one IDUN model's chat-completions endpoint, rate-limited."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        json_object: bool = True,
    ) -> None:
        self._http = http
        self._model = model
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/v1/chat/completions"
        self._temperature = temperature
        # Generous enough that a reasoning model's chain-of-thought plus the final
        # JSON is not truncated; the answer itself is tiny.
        self._max_tokens = max_tokens
        self._limiter = limiter or RateLimiter()
        self._max_retries = max_retries
        # Guided JSON output, ON by default: reasoning models (NorwAI-Magistral,
        # Qwen-thinking) otherwise answer in prose or leak all tokens into
        # reasoning and return empty content. If the server turns out not to
        # support response_format, `complete` detects the 400 and drops it
        # automatically (self._json_object flips to False) — no caller knob.
        self._json_object = json_object

    @property
    def model(self) -> str:
        return self._model

    def _payload(self, messages: list[dict[str, str]]) -> dict:
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if self._json_object:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Send ``messages`` and return the assistant message content.

        Rate-limited and retried on transient failures with exponential backoff
        (honouring ``Retry-After`` on 429). Raises :class:`IdunError` on a fatal
        status or once retries are exhausted.
        """
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                resp = await self._http.post(
                    self._url, json=self._payload(messages), headers=headers
                )
                resp.raise_for_status()
                return self._content(resp.json())
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Server doesn't support guided JSON: drop response_format and
                # retry immediately (once) without it, for the rest of this client.
                if (
                    status == 400
                    and self._json_object
                    and "response_format" in exc.response.text.lower()
                ):
                    logger.warning("server rejected response_format; falling back to plain prompting")
                    self._json_object = False
                    continue
                if status not in _RETRY_STATUS or attempt == self._max_retries:
                    raise IdunError(f"IDUN {status}: {exc.response.text[:300]}") from exc
                last_exc = exc
                await asyncio.sleep(self._backoff(attempt, exc.response))
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise IdunError(f"IDUN transport error: {exc}") from exc
                last_exc = exc
                await asyncio.sleep(self._backoff(attempt, None))

        raise IdunError(f"IDUN request failed after retries: {last_exc}")

    @staticmethod
    def _content(data: dict) -> str:
        """Pull ``choices[0].message.content`` out of an OpenAI-style response."""
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise IdunError(f"unexpected response shape: {data!r}") from exc

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        """Seconds to wait before the next attempt (Retry-After wins on 429)."""
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                return float(retry_after)
        # Standard exponential backoff: attempt 0 -> 1s, 1 -> 2s, 2 -> 4s (the
        # longest wait, since max_retries defaults to 3). Capped at 60s so a
        # higher max_retries can't produce an unbounded sleep.
        return min(float(2 ** attempt), 60.0)
