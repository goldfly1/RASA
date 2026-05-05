"""LLM Gateway — Unified provider client with tiered routing, Redis caching, fallback chain."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as aioredis
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from rasa.llm_gateway.router import TierRouter

TIER_PRIORITY = {"premium": 0, "standard": 1}


class GatewayError(Exception):
    """Unrecoverable gateway failure after all fallback paths exhausted."""


class GatewayClient:
    """Async client for Ollama (OpenAI-compatible) and optional OpenAI fallback.

    Cache flow:
      miss → call provider → store in Redis → return
      hit  → return cached content immediately
      seed → bypass cache entirely (deterministic replay)
    """

    def __init__(
        self,
        config_path: str | Path = "config/gateway.yaml",
        *,
        redis_url: str | None = None,
    ) -> None:
        self._router = TierRouter(config_path)
        self._redis: aioredis.Redis | None = None
        self._redis_url = redis_url or os.environ.get(
            "RASA_REDIS_URL", "redis://localhost:6379"
        )
        self._fallback_api_key = os.environ.get("FALLBACK_API_KEY", "")

    async def _ensure_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(self._redis_url)
        return self._redis

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tier: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion, resolving tier → provider → model.

        If *model* is given it overrides tier resolution (on-the-fly override,
        not used in pilot but accepted for forward-compat).
        If *seed* is given the cache is bypassed (deterministic replay).
        If *tools* is given the cache is bypassed (tool calls are stateful).
        If *messages* is given it replaces the default [user] message construction.
        """
        provider_name, model_id = self._router.resolve(tier)
        if model:
            model_id = model

        cache_key = ""
        if seed is None and not tools:
            cache_key = _cache_key(prompt, model_id, temperature, max_tokens)
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

        result = await self._route_with_fallback(
            prompt=prompt,
            messages=messages,
            tools=tools,
            provider_name=provider_name,
            model_id=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            seed=seed,
            extra_body=extra_body,
        )

        if cache_key and not result.get("tool_calls"):
            await self._cache_set(cache_key, result, ttl=self._router.cache_ttl)

        return result

    async def flush_cache(self, pattern: str = "llm_cache:*") -> int:
        """Delete cache keys matching a glob pattern. Returns count deleted."""
        r = await self._ensure_redis()
        keys: list = []
        cursor = 0
        while True:
            cursor, batch = await r.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if keys:
            return await r.delete(*keys)
        return 0

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # Internal: routing + fallback
    # ------------------------------------------------------------------

    async def _route_with_fallback(
        self,
        *,
        prompt: str,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        provider_name: str,
        model_id: str,
        temperature: float | None,
        max_tokens: int | None,
        top_p: float | None,
        seed: int | None,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None

        # Attempts in order: primary tier → next tier(s) → OpenAI fallback
        attempts: list[tuple[str, str]] = [(provider_name, model_id)]
        # If premium fails, try standard
        tiers = self._router.tiers
        if len(tiers) > 1 and provider_name == self._router.resolve("premium")[0]:
            std_provider, std_model = self._router.resolve("standard")
            if (std_provider, std_model) != (provider_name, model_id):
                attempts.append((std_provider, std_model))

        for prov_name, mod_id in attempts:
            try:
                return await _call_provider(
                    provider_name=prov_name,
                    model_id=mod_id,
                    prompt=prompt,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    seed=seed,
                    extra_body=extra_body,
                    max_attempts=self._router.max_attempts,
                    backoff_ms=self._router.backoff_ms,
                )
            except Exception as exc:
                last_exc = exc
                continue  # try next tier

        # OpenAI fallback (optional)
        if self._fallback_api_key:
            try:
                return await _call_openai_fallback(
                    prompt=prompt,
                    api_key=self._fallback_api_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    seed=seed,
                    extra_body=extra_body,
                )
            except Exception as exc:
                last_exc = exc

        raise GatewayError(
            f"All routes exhausted for tier '{provider_name}' model '{model_id}'"
        ) from last_exc

    # ------------------------------------------------------------------
    # Internal: Redis cache
    # ------------------------------------------------------------------

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        try:
            r = await self._ensure_redis()
            raw = await r.get(f"llm_cache:{key}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def _cache_set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        try:
            r = await self._ensure_redis()
            await r.setex(f"llm_cache:{key}", ttl, json.dumps(value))
        except Exception:
            pass  # cache is best-effort


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
)
async def _call_provider(
    *,
    provider_name: str,
    model_id: str,
    prompt: str,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None,
    max_tokens: int | None,
    top_p: float | None,
    seed: int | None,
    extra_body: dict[str, Any] | None,
    max_attempts: int,
    backoff_ms: int,
) -> dict[str, Any]:
    base = "http://127.0.0.1:11434/v1"
    url = f"{base}/chat/completions"

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages or [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if top_p is not None:
        payload["top_p"] = top_p
    if seed is not None:
        payload["seed"] = seed
    if extra_body:
        payload.update(extra_body)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]["message"]
        return {
            "content": choice.get("content", ""),
            "tool_calls": choice.get("tool_calls", []),
            "model": data.get("model", model_id),
            "usage": data.get("usage", {}),
        }


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
)
async def _call_openai_fallback(
    *,
    prompt: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int | None,
    top_p: float | None,
    seed: int | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if top_p is not None:
        kwargs["top_p"] = top_p
    if seed is not None:
        kwargs["seed"] = seed
    if extra_body:
        kwargs["extra_body"] = extra_body

    resp = await client.chat.completions.create(**kwargs)
    return {
        "content": resp.choices[0].message.content or "",
        "model": resp.model,
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        },
    }


def _cache_key(
    prompt: str, model: str, temperature: float | None, max_tokens: int | None
) -> str:
    payload = f"{prompt}|{model}|{temperature}|{max_tokens}"
    return hashlib.sha256(payload.encode()).hexdigest()
