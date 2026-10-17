-- Задача супервизора. Колонку status обновляет только Supervisor.

CREATE TABLE IF NOT EXISTS orch_tasks (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    goal TEXT,
    plan_json TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    spend_internal REAL NOT NULL DEFAULT 0,
    spend_actions REAL NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    pause_requested INTEGER NOT NULL DEFAULT 0 CHECK (pause_requested IN (0, 1)),
    steer_text TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orch_tasks_session ON orch_tasks (session_id);

CREATE TABLE IF NOT EXISTS orch_steps (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES orch_tasks (id),
    position INTEGER NOT NULL,
    action_name TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    expected TEXT,
    paid INTEGER NOT NULL DEFAULT 0 CHECK (paid IN (0, 1)),
    mutating INTEGER NOT NULL DEFAULT 0 CHECK (mutating IN (0, 1)),
    status TEXT NOT NULL,
    change_id TEXT,
    cost REAL NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (task_id, position)
);
