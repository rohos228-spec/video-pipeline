-- Спецификация WS2. Неизменяемая история промптов и параметров.
-- Откат = новая строка kind='rollback'. UPDATE/DELETE запрещены триггером.
-- ≤1 активной версии на ноду — частичный уникальный индекс.

CREATE TABLE IF NOT EXISTS node_prompt_versions (
    id INTEGER PRIMARY KEY,
    node_key TEXT NOT NULL REFERENCES node_defs (node_key),
    version INTEGER NOT NULL,
    prompt_template TEXT NOT NULL,
    params_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('create', 'edit_prompt', 'edit_params', 'rollback')),
    patch_mode TEXT,
    section_anchor TEXT,
    reason TEXT,
    parent_version INTEGER,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    change_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (node_key, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_node_prompt_versions_one_active
    ON node_prompt_versions (node_key)
    WHERE is_active = 1;

CREATE TRIGGER IF NOT EXISTS node_prompt_versions_no_update
BEFORE UPDATE ON node_prompt_versions
BEGIN
    SELECT RAISE(ABORT, 'node_prompt_versions is immutable');
END;

CREATE TRIGGER IF NOT EXISTS node_prompt_versions_no_delete
BEFORE DELETE ON node_prompt_versions
BEGIN
    SELECT RAISE(ABORT, 'node_prompt_versions is immutable');
END;
