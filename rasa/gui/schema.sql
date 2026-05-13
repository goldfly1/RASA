CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    goal        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    phase       TEXT NOT NULL DEFAULT 'planning'
        CHECK (phase IN ('planning','in_progress','review','blocked','done')),
    priority    INTEGER NOT NULL DEFAULT 3
        CHECK (priority BETWEEN 1 AND 5),
    notes       TEXT NOT NULL DEFAULT '',
    orchestrator_task_id TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    soul_id     TEXT NOT NULL DEFAULT 'coder-v2-dev',
    status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','failed')),
    orch_task_id TEXT DEFAULT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT DEFAULT NULL,
    message     TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'info'
        CHECK (level IN ('info','warn','error','success')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
