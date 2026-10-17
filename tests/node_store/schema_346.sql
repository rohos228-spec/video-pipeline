-- Скопировано из PR #346, коммит fab1b126: 001_node_defs.sql и 002_node_prompt_versions.sql.
-- Миграции в рантайме пишет только WS3. Здесь SQL нужен тестам node_store, пока #346 не в main.

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
    CHECK (node_key REGEXP '^n_[a-z0-9_]+$')
);

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
