-- Спецификация WS2. Отчёты и снапшоты нод. Раздача файлов — роутер WS2, не статика.

CREATE TABLE IF NOT EXISTS orch_reports (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    slug TEXT,
    title TEXT,
    file_path TEXT,
    editable INTEGER NOT NULL DEFAULT 1 CHECK (editable IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL CHECK (kind IN ('report', 'nodes_snapshot')),
    created_change_id TEXT,
    updated_change_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, slug)
);

CREATE TABLE IF NOT EXISTS orch_report_versions (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES orch_reports (id),
    version INTEGER NOT NULL,
    sections_json TEXT,
    html_sha256 TEXT,
    change_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (report_id, version)
);
