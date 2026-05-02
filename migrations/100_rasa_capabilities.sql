-- 100_rasa_capabilities.sql
-- Capability registry: what each specialist agent can do.
-- The orchestrator queries this table to make informed delegation decisions.

\echo '=== Creating agent_capabilities table ==='

\c rasa_orch

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agent_capabilities (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    soul_id         TEXT NOT NULL,
    agent_role      TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL,
    capabilities    JSONB NOT NULL DEFAULT '[]'::jsonb,
    access_level    TEXT NOT NULL DEFAULT 'read-only'
                        CHECK (access_level IN ('read-only', 'read-write', 'read-write-exec')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(soul_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_cap_soul_id ON agent_capabilities(soul_id);
CREATE INDEX IF NOT EXISTS idx_agent_cap_role ON agent_capabilities(agent_role);

\echo '=== Seeding initial agent capabilities ==='

INSERT INTO agent_capabilities (soul_id, agent_role, display_name, description, capabilities, access_level)
VALUES
('planner-v1',    'PLANNER',    'Technical Planner',
 'Decomposes work into ordered steps, designs implementation plans, documents tradeoffs.',
 '[{"category":"planning","name":"Work decomposition","description":"Break high-level goals into ordered, verifiable tasks","strength":0.9},{"category":"planning","name":"Implementation planning","description":"Design step-by-step implementation plans with dependencies","strength":0.85},{"category":"documentation","name":"Tradeoff documentation","description":"Document architectural tradeoffs and rejected alternatives","strength":0.8},{"category":"analysis","name":"Codebase exploration","description":"Read and analyze codebase structure for planning","strength":0.7}]'::jsonb,
 'read-only'),

('architect-v1',  'ARCHITECT',  'System Architect',
 'Makes cross-module design decisions, defines interfaces, considers system holistically.',
 '[{"category":"design","name":"Cross-module design","description":"Design interfaces and interactions between system modules","strength":0.95},{"category":"design","name":"System architecture","description":"Define overall system structure, patterns, and conventions","strength":0.9},{"category":"analysis","name":"Impact analysis","description":"Analyze cross-cutting concerns and change impact across modules","strength":0.85},{"category":"documentation","name":"Architecture documentation","description":"Document architectural decisions and system design","strength":0.8}]'::jsonb,
 'read-write'),

('coder-v2-dev',  'CODER',      'Senior Coder',
 'Implements features, refactors code, writes tests.',
 '[{"category":"implementation","name":"Feature implementation","description":"Implement new features across backend (Python, Go, TypeScript)","strength":0.9},{"category":"implementation","name":"Refactoring","description":"Refactor existing code for maintainability and performance","strength":0.85},{"category":"testing","name":"Test writing","description":"Write unit tests, integration tests, and end-to-end tests","strength":0.8},{"category":"implementation","name":"Bug fixing","description":"Debug and fix issues across the codebase","strength":0.85}]'::jsonb,
 'read-write-exec'),

('reviewer-v1',   'REVIEWER',   'Code Reviewer',
 'Reviews code changes for correctness, security, style, and test coverage.',
 '[{"category":"review","name":"Correctness review","description":"Verify code logic, edge cases, and correctness","strength":0.9},{"category":"review","name":"Security review","description":"Identify security vulnerabilities and unsafe patterns","strength":0.85},{"category":"review","name":"Style review","description":"Check code style, conventions, and readability","strength":0.8},{"category":"review","name":"Test coverage review","description":"Assess test coverage and test quality","strength":0.75}]'::jsonb,
 'read-only')
ON CONFLICT (soul_id) DO UPDATE SET
    display_name  = EXCLUDED.display_name,
    description   = EXCLUDED.description,
    capabilities  = EXCLUDED.capabilities,
    access_level  = EXCLUDED.access_level,
    updated_at    = NOW();

\echo '=== Done ==='
