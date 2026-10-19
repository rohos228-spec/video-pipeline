-- Чекпоинт после мутирующего шага и перед платным: план, позиция, change_id, spend, контекст.

CREATE TABLE IF NOT EXISTS orch_checkpoints (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES orch_tasks (id),
    session_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    position INTEGER NOT NULL,
    change_ids_json TEXT NOT NULL DEFAULT '[]',
    spend_json TEXT NOT NULL DEFAULT '{}',
    context TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orch_checkpoints_task ON orch_checkpoints (task_id, id);
