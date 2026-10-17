-- Спецификация WS2 (node_store). Одна строка на ноду оркестратора.
-- Время — ISO-строка UTC. active_version логически ссылается на
-- node_prompt_versions.version этой ноды (циклический FK не ставим).

CREATE TABLE IF NOT EXISTS node_defs (
    id INTEGER PRIMARY KEY,
    node_key TEXT NOT NULL UNIQUE,
    group_key TEXT NOT NULL,
    title TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('db', 'git_override')),
    is_new_group INTEGER NOT NULL DEFAULT 0 CHECK (is_new_group IN (0, 1)),
    after_node_key TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    active_version INTEGER,
    created_change_id TEXT,
    updated_change_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    CHECK (title IS NULL OR length(title) <= 120),
    -- Без REGEXP: функция regexp есть только в connect() этого модуля,
    -- голое sqlite3 её не знает. GLOB — встроенный оператор SQLite.
    CHECK (
        length(node_key) >= 3
        AND node_key GLOB 'n_[a-z0-9_]*'
        AND node_key NOT GLOB '*[^a-z0-9_]*'
    )
);
