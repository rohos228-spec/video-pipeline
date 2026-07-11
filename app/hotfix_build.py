"""Идентификатор пакета hotfix — виден в логах и /api/studio-version."""

PIPELINE_HOTFIX_ID = "hotfix-20260711-dequeue-no-autoadvance-v10"

# Маркеры для scripts/Update-Hotfix-FromGitHub.ps1 (проверка после скачивания).
HOTFIX_MARKERS: dict[str, str] = {
    "app/services/project_control.py": "_set_user_stop_gate",
    "app/services/sidebar_layout.py": "_normalize_gen_queue",
    "app/services/xlsx_versioning.py": "normalize_xlsx_to_reference_layout",
    "app/bots/chatgpt.py": "attach-guard-v85-iron-stop",
    "app/services/gen_queue.py": "project_gated_by_gen_queue",
}
