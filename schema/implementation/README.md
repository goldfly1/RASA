# Implementation Schema

> **Reference:** `architectural_schema_v2.1.md`  
> **Status:** Updated 2026-05-13 ? component design complete per Gates 0?5  
> **Last Updated:** 2026-05-13

---

## Separation of Concerns

The **Architecture Schema** (`architectural_schema_v2.1.md`) defines the **what** and **who talks to whom**. It is the boundary document ? components, contracts, entities, and interface failure modes. It should stay stable even if you swap from Redis to RabbitMQ, or from containers to VMs.

This **Implementation Schema** defines the **how**, per component. It covers internal state machines, tech stack choices, deployment topology, performance budgets, and operational runbooks. This layer changes as you iterate.

### Why two documents?
- **Different audiences.** Architects read the boundary doc. Engineers read the component internals.
- **Different volatility.** The architecture should not churn because you decided to use Postgres instead of Neo4j for the graph store.
- **Different lifecycle.** Architecture is reviewed for correctness against strategy. Implementation is reviewed for feasibility against constraints.

### Organic link between them
- Architecture references implementation decisions as `{see impl: <component>}`.
- Implementation docs open with a link back to their architectural boundary and a statement: *This document implements the interface contract defined in `architectural_schema_v2.1.md` ?X.Y*.
- When architecture changes, implementation docs update. When implementation discovers a boundary problem, architecture docs get a revision.

---

## Directory Structure

| Document | Architectural Boundary | Scope |
|----------|-----------------------|-------|
| [`README.md`](README.md) | ? | This lead page |
| [`agent_configuration.md`](agent_configuration.md) | ?2.1 / ?3.2 ? Agent Identity & Prompting | Soul sheets, prompt templating, CLI invocation, prompt governance |
| [`bootstrap_ingestion.md`](bootstrap_ingestion.md) | ?8 ? Bootstrap & Ingestion | Extractor implementations, baseline freezing, cold-start |
| [`top_level_decisions.md`](top_level_decisions.md) | ? | Cross-cutting tech stack, deployment topology, standards |
| [`recovery_controller.md`](recovery_controller.md) | ?11 ? Recovery & Resumption | Internal states, checkpoint replay, idempotency ledger |
| [`pool_controller.md`](pool_controller.md) | ?12 ? Agent Pool & Backpressure | Concurrency ceilings, backpressure algorithms, scaling |
| [`evaluation_engine.md`](evaluation_engine.md) | ?13 ? Evaluation Engine | Aggregation pipelines, benchmark mechanics, alert rules |
| [`agent_runtime.md`](agent_runtime.md) | ?2.1 / ?3.2 ? Agent & Session | Process model, session manager, heartbeat, soul loading |
| [`orchestrator.md`](orchestrator.md) | ?1 / ?3 ? System Context & State Machines | Task scheduler, state machine engine, assignment logic |
| [`llm_gateway.md`](llm_gateway.md) | ?9 ? Interface Boundaries | Routing, fallback chains, caching, tier switching |
| [`memory_subsystem.md`](memory_subsystem.md) | ?5 ? Memory Architecture | Stores, embedding pipeline, eviction, canonical model |
| [`sandbox_pipeline.md`](sandbox_pipeline.md) | ?6.1 ? Sandbox Execution | Scanner chain, VM lifecycle, artifact promotion |
| [`message_bus.md`](message_bus.md) | ?4 ? Protocol Definitions | Transport, envelope validation, dead-letter |
| [`observability_stack.md`](observability_stack.md) | ?7 ? Observability | Tracing, metrics, alerting, replay bundles |
| [`policy_engine.md`](policy_engine.md) | ?6.2 ? Permission Matrix | Rule evaluation, audit logging, hot-reload of guardrails |
| [`versioning.md`](versioning.md) | ? | How implementation docs rev with architecture changes |

---

## Guides & Operator Documentation

| Document | Audience | Purpose |
|----------|----------|---------|
| [`../docs/prompt-and-cli-guide.md`](../docs/prompt-and-cli-guide.md) | Operators, CI/CD integrators, prompt engineers | Detailed walkthrough of prompt templating, variable resolution, and CLI invocation patterns |

---

## Document Template

Every component implementation doc should use this structure:

```markdown
# <Component Name>

> **Architectural Reference:** `architectural_schema_v2.1.md` ?X.Y  
> **Status:** Draft / In Review / Stable  
> **Owner:** TBD

## 1. Purpose
One-paragraph summary of what this component does.

## 2. Internal Design
(If this component is stateful, use `### 2.1 Internal State Machine`.)

### 2.1 Internal State Machine
(If applicable.) States, transitions, triggers.

## 3. Tech Stack Choices
Database, language, framework, and rationale.

## 4. Deployment Topology
Process model, replication, co-location with other components.

## 5. Operational Concerns
Health checks, metrics exposed, alert thresholds, runbook links.

## 6. Open Questions
Known gaps or decisions to make.

## 7. Change Log
| Date | Change | Author |
|------|--------|--------|
```

---

## Continuous Validation
A lightweight linter should enforce:
- Every component doc links back to a valid architectural ? in `architectural_schema_v2.1.md`.
- The directory structure table in this README matches actual files in the folder.
- Status fields use the allowed set: `Draft / In Review / Stable / Deprecated`.

---

*This lead page and all scaffolded docs are part of the implementation layer derived from `architectural_schema_v2.1.md`. Do not modify architecture here; propose changes upstream.*
