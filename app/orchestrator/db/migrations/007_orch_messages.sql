-- Реплики чата. Индексирует WS6 (RAG); очередь WS3 пишет при приёме сообщения.

CREATE TABLE IF NOT EXISTS orch_messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orch_messages_session ON orch_messages (session_id, id);
