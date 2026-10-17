-- Спецификация WS2. Неизменяемая история промптов и параметров.
-- Откат = новая строка kind='rollback'.
-- revert_node_to_git = новая строка kind='revert_to_git' и
-- node_defs.active_version=NULL (статус 'reverted' не вводим).
-- base_git_sha256 — sha256 git-файла на момент версии (для stale_base).
-- Правка из UI студии — created_via='studio_ui', не отдельный kind.
-- UPDATE/DELETE запрещены триггером.
-- ≤1 активной версии на ноду — частичный уникальный индекс.
-- Миграция не влита в main: колонки в CREATE, без отдельного 009.

CREATE TABLE IF NOT EXISTS node_prompt_versions (
    id INTEGER PRIMARY KEY,
    node_key TEXT NOT NULL REFERENCES node_defs (node_key),
    version INTEGER NOT NULL,
    prompt_template TEXT NOT NULL,
    params_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('create', 'edit_prompt', 'edit_params', 'rollback', 'revert_to_git')
    ),
    patch_mode TEXT,
    section_anchor TEXT,
    reason TEXT,
    parent_version INTEGER,
    base_git_sha256 TEXT,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    change_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    created_via TEXT NOT NULL DEFAULT 'orchestrator' CHECK (created_via IN ('orchestrator', 'studio_ui')),
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
