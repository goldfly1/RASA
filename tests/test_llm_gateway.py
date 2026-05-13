"""Tests for the LLM Gateway — tier routing, caching, fallback logic."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from rasa.llm_gateway import GatewayClient, GatewayError, TierRouter
from rasa.llm_gateway.client import _cache_key


class TestTierRouter:
    def test_resolve_standard(self):
        r = TierRouter()
        prov, model = r.resolve("standard")
        assert prov == "ollama"
        assert model == "deepseek-v4-pro:cloud"

    def test_resolve_premium(self):
        r = TierRouter()
        prov, model = r.resolve("premium")
        assert prov == "ollama"
        assert model == "deepseek-v4-pro:cloud"

    def test_resolve_unknown_tier_falls_back(self):
        r = TierRouter()
        prov, model = r.resolve("bogus")
        assert prov == "ollama"
        assert model != ""

    def test_resolve_none(self):
        r = TierRouter()
        prov, model = r.resolve(None)
        assert prov == "ollama"
        assert model == "deepseek-v4-pro:cloud"

    def test_tiers_list(self):
        r = TierRouter()
        assert "standard" in r.tiers
        assert "premium" in r.tiers

    def test_cache_ttl(self):
        r = TierRouter()
        assert r.cache_ttl == 3600

    def test_fallback_config(self):
        r = TierRouter()
        assert r.fallback_enabled is True
        assert r.max_attempts == 3
        assert r.backoff_ms == 1000

    def test_empty_config_tmp(self, tmp_path: Path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "tiers": {},
                    "providers": {},
                    "cache": {},
                    "fallback": {},
                }
            )
        )
        r = TierRouter(str(cfg))
        assert r.tiers == []
        assert r.resolve(None) == ("ollama", "")


class TestCacheKey:
    def test_deterministic(self):
        k1 = _cache_key("hello", "m1", 0.2, 100)
        k2 = _cache_key("hello", "m1", 0.2, 100)
        assert k1 == k2

    def test_different_prompt_produces_different_key(self):
        k1 = _cache_key("hello", "m1", 0.2, 100)
        k2 = _cache_key("world", "m1", 0.2, 100)
        assert k1 != k2

    def test_different_model_different_key(self):
        k1 = _cache_key("p", "m1", 0.2, 100)
        k2 = _cache_key("p", "m2", 0.2, 100)
        assert k1 != k2

    def test_key_is_hex_sha256(self):
        k = _cache_key("test", "m", None, None)
        assert len(k) == 64
        int(k, 16)  # valid hex


class TestGatewayClient:
    @pytest.mark.asyncio
    async def test_instantiate_and_close(self):
        client = GatewayClient()
        await client.close()
        assert client._redis is None

    @pytest.mark.asyncio
    async def test_flush_cache_empty(self):
        client = GatewayClient()
        try:
            count = await client.flush_cache("nonexistent_pattern_xyz:*")
            assert count == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self):
        client = GatewayClient()
        try:
            r = await client._ensure_redis()
            await r.setex("llm_cache:test_key", 60, json.dumps({"content": "cached"}))
            result = await client._cache_get("test_key")
            assert result == {"content": "cached"}
            await r.delete("llm_cache:test_key")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        client = GatewayClient()
        try:
            result = await client._cache_get("nonexistent_key_xyz")
            assert result is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_flush_cache_deletes_keys(self):
        client = GatewayClient()
        try:
            r = await client._ensure_redis()
            await r.setex("llm_cache:test_flush_a", 60, '{"x":1}')
            await r.setex("llm_cache:test_flush_b", 60, '{"x":2}')
            count = await client.flush_cache("llm_cache:test_flush_*")
            assert count == 2
            assert await client._cache_get("test_flush_a") is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_complete_checks_cache_first(self, monkeypatch):
        """Verify cache hit skips provider call."""

        client = GatewayClient()
        called = False

        async def fake_route(*args, **kwargs):
            nonlocal called
            called = True
            return {"content": "from_provider"}

        # Pre-populate cache
        r = await client._ensure_redis()
        cache_key = _cache_key("cached_prompt", "deepseek-v4-pro:cloud", None, None)
        await r.setex(
            f"llm_cache:{cache_key}",
            60,
            json.dumps({"content": "from_cache"}),
        )

        try:
            client._route_with_fallback = fake_route  # type: ignore[method-assign]
            result = await client.complete("cached_prompt", tier="standard")
            assert result == {"content": "from_cache"}
            assert not called, "provider was called despite cache hit"
        finally:
            await r.delete(f"llm_cache:{cache_key}")
            await client.close()

    @pytest.mark.asyncio
    async def test_seed_bypasses_cache(self, monkeypatch):
        """Seeded requests must never hit the cache."""
        client = GatewayClient()
        provider_called = False

        async def fake_route(*args, **kwargs):
            nonlocal provider_called
            provider_called = True
            return {"content": "seeded_result"}

        # Pre-populate — seed should ignore it
        r = await client._ensure_redis()
        key = _cache_key("seed_prompt", "deepseek-v4-pro:cloud", 0.0, 100)
        await r.setex(f"llm_cache:{key}", 60, json.dumps({"content": "stale"}))

        try:
            client._route_with_fallback = fake_route  # type: ignore[method-assign]
            result = await client.complete(
                "seed_prompt", tier="standard", temperature=0.0, max_tokens=100, seed=42
            )
            assert result == {"content": "seeded_result"}
            assert provider_called, "seed should have bypassed cache"
        finally:
            await r.delete(f"llm_cache:{key}")
            await client.close()


class TestEnvVarResolution:
    def test_default_used_when_var_unset(self, monkeypatch):
        monkeypatch.delenv("RASA_DEFAULT_MODEL", raising=False)
        from rasa.llm_gateway.router import _resolve_env
        result = _resolve_env("${RASA_DEFAULT_MODEL:-deepseek-v4-pro:cloud}")
        assert result == "deepseek-v4-pro:cloud"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("RASA_DEFAULT_MODEL", "custom-model:cloud")
        from rasa.llm_gateway.router import _resolve_env
        result = _resolve_env("${RASA_DEFAULT_MODEL:-deepseek-v4-pro:cloud}")
        assert result == "custom-model:cloud"

    def test_bare_env_var_no_default(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "value123")
        from rasa.llm_gateway.router import _resolve_env
        result = _resolve_env("${SOME_VAR}")
        assert result == "value123"

    def test_bare_env_var_unset_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        from rasa.llm_gateway.router import _resolve_env
        result = _resolve_env("${MISSING_VAR}")
        assert result == ""

    def test_tier_router_respects_env_override(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RASA_DEFAULT_MODEL", "env-model:cloud")
        cfg = tmp_path / "gw.yaml"
        cfg.write_text(yaml.dump({
            "tiers": {"standard": {"provider": "ollama", "model": "${RASA_DEFAULT_MODEL:-fallback}"}},
            "providers": {}, "cache": {}, "fallback": {},
        }))
        from rasa.llm_gateway.router import TierRouter
        r = TierRouter(str(cfg))
        prov, model = r.resolve("standard")
        assert model == "env-model:cloud"


class TestGatewayError:
    def test_is_exception(self):
        with pytest.raises(GatewayError):
            raise GatewayError("test")
