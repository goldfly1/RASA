# LLM Gateway

> **Architectural Reference:** `architectural_schema_v2.1.md` §9  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — prompt hash caching, model parameter routing   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Unified inference interface: routes prompts to configured models, handles fallback chains, caches completions, and enforces budget tiers. Receives `ModelRequest` envelopes that include soul-derived parameters (temperature, max_tokens, model preference) and a content-addressed prompt hash for cache lookup.

---

## 2. Internal Design

### 2.1 Prompt Cache

The gateway maintains a **prompt hash cache** (Redis) keyed by `SHA-256(final_assembled_prompt + model_id + temperature + max_tokens)`.

- **Hit:** Return cached completion; skip model call; emit `CACHE_HIT` metric tagged with `soul_id`.
- **Miss:** Forward to model; store result with TTL (default 1h, configurable per soul sheet).
- **Invalidation triggers:**
  - TTL expiry
  - Soul sheet filesystem change (filesystem watcher detects `soul_id` version bump)
  - Manual flush via Gateway admin endpoint
  - (`souls.update` is the documented upgrade path)

### 2.2 Model Parameter Routing

The `ModelRequest` envelope carries parameters resolved from the soul sheet (see [`agent_configuration.md`](agent_configuration.md) §2.2 `model` block):

| Parameter | Soul Sheet Field | Gateway Behavior |
|-----------|-----------------|------------------|
| `model_id` | `model.default_tier` | Maps tier to hard-coded model name (see §2.3). `model.preferred_model` is ignored in pilot; can be used for on-the-fly overrides later. |
| `temperature` | `model.temperature` | Passed through to model API. |
| `max_tokens` | `model.max_tokens` | Passed through; truncated if it exceeds tier ceiling. |
| `top_p` | `model.top_p` | Passed through. |
| `budget_tier` | `model.default_tier` | Enforced before routing; rejected if tier quota exhausted. |

### 2.3 Model Tier Mapping (Hard-Coded)

The Gateway maintains a static mapping in its config file. Soul sheets reference tiers by name; the Gateway resolves to the actual model.

| Tier | Model | Agent Roles | Token Limit |
|------|-------|-------------|-------------|
| `standard` | `deepseek-v4-flash:cloud` | CODER, REVIEWER | 16K output |
| `premium` | `deepseek-v4-pro:cloud` | PLANNER, ARCHITECT | 32K output |

> **Upgrade path:** To switch to on-the-fly model selection, replace the tier-mapping lookup with the soul sheet's `model.preferred_model` field. The Gateway already accepts it in the `ModelRequest` — it's just not used in pilot mode.

### 2.4 Fallback Chain

If the tier's model is unavailable (Ollama Cloud returns 5xx or timeout):

1. **Same tier, different model** — Not applicable in pilot (one model per tier). Skipped.
2. **Degrade to next tier** — `premium` falls back to `standard` (deepseek-v4-flash). `standard` has no lower tier to fall back to.
3. **Alternate API key** — If all Ollama routes fail, try the configured fallback provider (e.g., OpenAI `gpt-4o-mini`) using the key from `.env`. This is optional — only enabled if `FALLBACK_API_KEY` is set.
4. **All routes exhausted** — Return `BUDGET_EXHAUSTED` to Orchestrator for escalation.

Every fallback step emits an alert to the Observability Stack (structured JSON log).

### 2.5 Deterministic Sampling

For replay and regression testing, the Gateway supports a `seed` parameter in the `ModelRequest`. When `seed` is present:
- Sets `seed` on the Ollama/OpenAI-compatible API call.
- Bypasses the cache (seeded calls are never cached).
- Emits a `REPLAY_TRACE` event to the Observability Stack.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Python 3.12+** | Matches agent runtime language. OpenAI-compatible SDK (httpx + openai-python) for Ollama and fallback providers. |
| **Cache store** | **Redis** (single-node) | Already provisioned. Sub-ms key lookup for cache hits. |
| **Primary provider** | **Ollama Cloud** via local desktop app (`http://localhost:11434/v1`) | Desktop app handles auth. OpenAI-compatible API — same client code as any OpenAI endpoint. |
| **Fallback provider** | **OpenAI** (optional, via `FALLBACK_API_KEY` in `.env`) | Only used when Ollama Cloud is unreachable. Disabled by default. |
| **Client library** | `openai` Python SDK | Mature, async-capable, works with any OpenAI-compatible endpoint (Ollama, Together, Groq, etc.). |

---

## 4. Deployment Topology

- **Process:** Python process, started via Procfile:
  ```
  llm-gateway: python -m rasa.llm_gateway --config config/gateway.yaml
  ```
- **Dependencies:** Local Redis, , Ollama Cloud desktop app (must be running).
- **Configuration file:** `config/gateway.yaml` — contains tier-to-model mapping, fallback chain, and cache TTLs.
- **Auth:** Ollama desktop app handles its own auth. The Gateway does not store an Ollama API key. The optional OpenAI fallback key lives in `.env`.
- **Startup order:** Redis → → Ollama desktop app → LLM Gateway → Agent Runtime.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Cache hit rate | Logged per request | < 30% — review TTL or prompt diversity |
| Ollama endpoint latency (p99) | Tracked per request | > 10s — consider fallback or tier degrade |
| Fallback activation count | Logged per event | > 5 in 1 hour — Ollama Cloud may be degraded |
| Cache memory usage (Redis) | Tracked via Redis INFO | > 500 MB — reduce TTL or evict stale keys |
| Budget exhaustion count | Logged per event | > 0 in normal operation — review tier capacity |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Does the Gateway support deterministic sampling for replays? | **Resolved:** Yes — `seed` parameter passed through to Ollama's OpenAI-compatible API. Cache bypassed for seeded calls. |
| 2 | What is the fallback chain depth before escalation? | **Resolved:** 2 steps for pilot (same-tier skip → next tier → optional API key). 3rd step returns BUDGET_EXHAUSTED. |
| 3 | Should cache TTL vary by `agent_role`? | **Open:** Recommend uniform 1h TTL for pilot. Can add per-role TTL as a Gateway config extension later. |
| 4 | Does Ollama Cloud's OpenAI-compatible API support the `seed` parameter for deterministic sampling? | **Open:** Needs verification. If not, seeded calls will fall through uncached (correct behavior) but non-deterministic. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: added Ollama Cloud provider (localhost:11434/v1), hard-coded tier mapping (deepseek-v4-flash / kimi-k2.6), optional OpenAI fallback, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added prompt hash cache, soul-derived parameter routing, and deterministic sampling | ? |

---

*This document implements the inference interface contract defined in `architectural_schema_v2.1.md` §9. Tier mapping aligns with `agent_configuration.md` §2.2 model block.*

