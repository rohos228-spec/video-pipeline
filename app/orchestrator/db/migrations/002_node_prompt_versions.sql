-- Спецификация WS2. Неизменяемая история промптов и параметров.
-- Откат = новая строка kind='rollback'.
-- revert_node_to_git = новая строка kind='revert_to_git' и
-- node_defs.active_version=NULL (статус 'reverted' не вводим).
-- Активная версия живёт только в node_defs.active_version.
-- Колонки is_active и частичного уникального индекса нет.
-- base_git_sha256 — sha256 git-файла на момент версии (для stale_base).
-- Правка из UI студии — created_via='studio_ui', не отдельный kind.
-- UPDATE/DELETE запрещены триггером.
-- Миграция не влита в main: колонки в CREATE, без отдельного 009.
-- Порядок записи: node_defs (active_version NULL) → версия → UPDATE active_version.

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
    change_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    created_via TEXT NOT NULL DEFAULT 'orchestrator' CHECK (created_via IN ('orchestrator', 'studio_ui')),
    UNIQUE (node_key, version)
);

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

-- active_version, если задан, указывает на версию этой же ноды
-- и не на строку kind='revert_to_git'. Циклический FK не ставим.
CREATE TRIGGER IF NOT EXISTS node_defs_active_version_ref_insert
BEFORE INSERT ON node_defs
WHEN NEW.active_version IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_version must reference a non-revert version of this node')
    WHERE NOT EXISTS (
        SELECT 1
        FROM node_prompt_versions
        WHERE node_key = NEW.node_key
          AND version = NEW.active_version
          AND kind <> 'revert_to_git'
    );
END;

CREATE TRIGGER IF NOT EXISTS node_defs_active_version_ref_update
BEFORE UPDATE ON node_defs
WHEN NEW.active_version IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_version must reference a non-revert version of this node')
    WHERE NOT EXISTS (
        SELECT 1
        FROM node_prompt_versions
        WHERE node_key = NEW.node_key
          AND version = NEW.active_version
          AND kind <> 'revert_to_git'
    );
END;
