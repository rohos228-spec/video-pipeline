# Безопасные умолчания (фаза B)

Все умолчания сохраняют текущую локальную работу владельца. Настройки читаются из `.env` / окружения.

## Доступ к изменяющим ручкам (B1)
- Веб-авторизация включена (`WEB_AUTH_USER` + `WEB_AUTH_PASSWORD`): ничего не меняется, работает `AuthDep`.
- Веб-авторизация выключена (умолчание): POST/DELETE надзорного слоя (`/api/projects/{id}/observations/*`: запуск, kill switch, решения, профили, действия, сброс неопределённости коммита, pin) и слоя действий (`/api/action-layer/*`: preview, from-text, from-macro, макросы, approve, run, stop) принимаются **только с loopback** (127.0.0.1, ::1). Остальным клиентам: `403 {"code": "local_only"}`.
- `ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH=true` снимает это ограничение (не рекомендуется без авторизации).
- Чтение журнала (`GET .../observations/*`, включая stream/telemetry/kill-switch) требует вход, когда авторизация включена.
- Реализация: одна зависимость `app/services/access_guard.py` (`LocalOnlyDep`).
- CORS остаётся `*`. При старте с выключенной авторизацией и `WEB_HOST`, не равным loopback, в лог пишется предупреждение `SECURITY: ...`. Умолчание `WEB_HOST=127.0.0.1`.

## Редакция и облачный трекер (B2)
- `error_message`, `project_source`, `metadata` перед записью в трекер (локальный SQLite и облако) проходят через `redact` (`api_tracker_hook.record_api_call`, `observation._track_api_cost`). Тексты исключений в журнале решений (`escalate`, отказ повтора) и SSE-ошибка также редактируются.
- `API_TRACKER_CLOUD_ENABLED` (умолчание `true`): `false` отключает отправку строк вызовов в облако; локальная запись остаётся, строка помечается `synced=1`, чтобы фоновая досылка её не отправила. Переключение аварийного рубильника (`toggle_killswitch`) и чтение его облачного состояния флагом не затрагиваются.

## Чат-оркестратор (B3)
- Каждое выполненное, отказанное и подтверждённое действие чата (`/db/projects/{id}/orchestrator/chat`, `confirm-git-push`, `confirm-delete-projects`) пишется в append-only журнал `action_layer_history` с событием `chat_action`: кто (`web_operator:<хеш токена>` или `local_web_operator`, плюс адрес клиента), когда, имя действия, исход (`ok`, `error`, `refused`, `pending_confirm`), `params_digest` (sha256 от аргументов после редакции, значений нет).
- `CHAT_CODE_ACTIONS_ENABLED` (умолчание `true`): `false` — чат отказывает в `edit_files` и `git_commit_push` (и в `confirm-git-push`) с понятным сообщением; остальные действия работают.

## Остаточные риски
- Проверка loopback опирается на адрес сокета; обратный прокси на той же машине делает все запросы «локальными».
- Страницы в браузере оператора по-прежнему могут слать запросы на localhost (CORS `*`) — защита от этого только через включённую авторизацию.
- Чат без `CHAT_CODE_ACTIONS_ENABLED=false` по-прежнему может править `app/`, `tests/`, `prompts/` и делать push.
