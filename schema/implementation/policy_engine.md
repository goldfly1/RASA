# Policy Engine

> **Architectural Reference:** `architectural_schema_v2.1.md` §6.2  
> **Implementation Reference:** [`agent_configuration.md`](agent_configuration.md) — tool_policy enforcement, denied_tools validation   
> **Status:** Pilot functional — core loop + hardening complete; see schema-vs-implementation report  
> **Owner:** TBD  
> **Last Updated:** 2026-05-13

---

## 1. Purpose

Evaluates Agent + requested action against the permission matrix at runtime. Emits Allow / Deny / Require-Review, logs every decision for audit, and supports hot-reload of rules without restarting dependent components. Extends the soul sheet `behavior.tool_policy` with organization-wide guardrails.

---

## 2. Internal Design

### 2.1 Permission Matrix

The Policy Engine evaluates requests against a layered rule stack:

| Layer | Source | Override Behavior |
|-------|--------|-------------------|
| **Organization Guardrails** | PostgreSQL `policy_rules` table | Cannot be overridden by soul sheets |
| **Soul Sheet Policy** | `behavior.tool_policy` in agent soul sheet | Overrides defaults; subject to organization guardrails |
| **Task Override** | Orchestrator `Task` envelope | Emergency denial (e.g., `--no-shell` flag) |
| **Human Review Queue** | CLI stdin prompt (pilot); REST API (upgrade) | Bypasses all automated rules |

### 2.2 Rule Evaluation Flow

```
Tool Request from Agent Runtime
        |
        |
  +---------------------+
  | Parse tool + args   |
  +---------------------+
        |
        |
  +---------------------+
  | Check org guardrails|-- Denied --> AUDIT_LOG + REJECT
  | (protected paths,   |              (e.g., /etc/*, sudo)
  |  host-level bans)   |
  +---------------------+
        | Allowed
        |
  +---------------------+
  | Check soul sheet    |-- Denied --> AUDIT_LOG + REJECT
  | tool_policy         |              (denied_tools, require_human_confirm)
  | (allowed_tools,     |
  |  denied_tools)      |
  +---------------------+
        | Allowed
        |
  +---------------------+
  | Check task override |-- Denied --> AUDIT_LOG + REJECT
  | (emergency denial)  |
  +---------------------+
        | Allowed
        |
  +---------------------+
  | Check human confirm |-- Require-Review --> PROMPT + BLOCK
  | (require_human_     |                     (CLI prompt in pilot;
  |  confirm matches)   |                      REST queue in upgrade)
  +---------------------+
        | Allowed
        |
   ALLOW + AUDIT_LOG
```

### 2.3 Soul Sheet Integration

The Policy Engine loads `behavior.tool_policy` from the soul sheet at agent session start and caches it in-memory for the session duration. Key fields:

| Field | Enforcement |
|-------|-------------|
| `auto_invoke` | If `false`, all tool calls must be pre-approved by Orchestrator before execution |
| `allowed_tools` | Whitelist; any tool not listed is denied |
| `denied_tools` | Blacklist; evaluated before whitelist; supports glob patterns (e.g., `file_write:/etc/*`) |
| `require_human_confirm` | Matches tool + argument pattern; blocks until human approval |

### 2.4 Hot Reload

Organization guardrails support two reload paths (pilot uses the first):

1. **PostgreSQL polling** — Policy Engine checks `policy_rules` table every 30 seconds for changes. Simple, no additional infrastructure.
2. **Redis `policy.update` channel** — Push-based updates for near-instant propagation. Topic defined in `message_bus.md` §2.2.

Soul sheet policy changes require agent restart (soul sheets are loaded at session start). The Policy Engine rejects tasks whose `soul_id` version does not match the current deployment.

---

## 3. Tech Stack Choices

| Concern | Selection | Rationale |
|---------|-----------|-----------|
| **Language** | **Go 1.24+** | Matches control-plane language. Single static binary. |
| **Rule store** | **PostgreSQL** (`policy_rules` table) | Co-located with primary durable store. JSONB for flexible rule definitions. |
| **Live update transport** | **Redis Pub/Sub** (`policy.update` channel) + **PostgreSQL polling** (30s fallback) | Push for speed; poll for reliability. |
| **Human review channel** | **CLI stdin prompt** (pilot); **REST API** (upgrade) | Simplest possible interface for a single-machine pilot. REST API can be added when human reviewers need a dashboard. |
| **Audit log** | **PostgreSQL** (`policy_audit_log` table) | Append-only table with tool, args, decision, soul_id, timestamp. |

---

## 4. Deployment Topology

- **Process:** Native Go binary, started via Procfile:
  ```
  policy: policy-engine --db postgres://localhost/rasa_policy 
  ```
- **Dependencies:** Local PostgreSQL, local Redis.
- **Auth:** None on localhost (pilot). Add mTLS when deploying across hosts.
- **Lifecycle:** Starts before Agent Runtime, runs alongside Orchestrator. No warm-up required.

---

## 5. Operational Concerns

| Metric | Pilot Action | Alert Threshold |
|--------|--------------|-----------------|
| Policy check latency (p99) | Logged per request; flag if > 10ms | > 50ms — review rule complexity |
| PostgreSQL polling interval | 30s default | Adjust to 10s if rules change frequently |
| Human review queue depth | Manual terminal monitoring | > 5 pending — escalate to operator |
| Audit log growth | 1 row per tool call | > 10K rows/day — consider log rotation |

---

## 6. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | How is the `.agent_protected_paths` list updated and propagated? | **Open:** Seed with a hardcoded list for pilot; make configurable via PostgreSQL table as upgrade. |
| 2 | What is the latency budget per policy check? | **Resolved (tentative):** 10ms p99 for pilot. Revisit if agents issue high-frequency calls (e.g., `file_read` hot loop). |
| 3 | Should `require_human_confirm` support time-windowed auto-approval? | **Open:** Useful pattern but out of scope for pilot. Can be added as a `behavior.tool_policy` extension field later. |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Pilot provisioning: human review channel → CLI stdin, added PostgreSQL polling as hot-reload path, filled Tech Stack / Deployment / Operational sections. | Codex |
| 2026-04-25 | Added soul sheet tool_policy integration, layered rule stack, and evaluation flow | ? |

---

*This document implements the permission contract defined in `architectural_schema_v2.1.md` §6.2. Tool policy fields align with `agent_configuration.md` §2.2 behavior block.*

