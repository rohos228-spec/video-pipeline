-- Спецификация WS5. Одна строка на попытку вызова модели.
-- Текст промптов и ответов не хранится. Время — ISO-строка UTC.
-- task_id / run_id — TEXT (не FK на orch_tasks.id).

CREATE TABLE IF NOT EXISTS orch_llm_calls (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    run_id TEXT,
    agent_role TEXT NOT NULL CHECK (
        agent_role IN ('supervisor', 'planner', 'critic', 'executor', 'classifier', 'summarizer')
    ),
    requested_tier TEXT NOT NULL CHECK (requested_tier IN ('fast', 'strong', 'auto')),
    tier TEXT NOT NULL CHECK (tier IN ('fast', 'strong', 'auto')),
    model TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('52pay', 'kie')),
    reasoning_effort TEXT,
    fallback_index INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('ok', 'timeout', 'error', 'fixture_miss')),
    error TEXT,
    deterministic_flag INTEGER NOT NULL CHECK (deterministic_flag IN (0, 1)),
    classifier_confidence REAL,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    budget_scope TEXT NOT NULL CHECK (budget_scope IN ('internal', 'pipeline')),
    router_impl TEXT NOT NULL CHECK (router_impl IN ('live', 'replay', 'scripted')),
    messages_sha256 TEXT,
    fixture_key TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_task_created
    ON orch_llm_calls (task_id, created_at);

CREATE INDEX IF NOT EXISTS ix_orch_llm_calls_budget_scope
    ON orch_llm_calls (budget_scope);
