-- Укрупнённые события сессии. token-события сюда не пишутся.
-- orch_event_seq — верхняя отметка seq на сессию. Токены берут номера
-- из памяти пачками, в таблицу пишется резерв, а не каждый токен.

CREATE TABLE IF NOT EXISTS orch_events (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id INTEGER,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_orch_events_session_created
    ON orch_events (session_id, created_at);

CREATE TABLE IF NOT EXISTS orch_event_seq (
    session_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL
);
