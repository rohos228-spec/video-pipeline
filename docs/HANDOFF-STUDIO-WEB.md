# Studio Web — handoff (синхронизация сессий)

Репозиторий на машине разработчика: `C:\Users\AiCreator\Desktop\video-pipeline`  
Копия на другой машине: `C:\Users\Love Space\video-pipeline`  
Ветка: `devin/windows-installer`

## Красная линия

Не трогать без явного запроса:

- `app/bots/outsee.py`, `elevenlabs.py`
- `app/orchestrator/steps/generate_*.py`

Зона UI: `web/src/**`, `app/web/**`

## Уже внесено (Love Space, перенести cherry-pick / merge)

| Изменение | Файлы |
|-----------|--------|
| Skip видео если клип на диске | `generate_videos.py`, `artifact_recovery.py` |
| `effectiveNodeKey` для Run/студии | `studio-workspace.tsx` |
| Статусы нод без node_run | `node-run-status.ts` → `inferNodeStatusFromProject`, `flow-canvas.tsx` |
| Toast с текстом API | `web/src/lib/error-message.ts`, `flow-canvas.tsx` |
| dry_run шага | `app/web/studio_dry_run.py`, `projects.py`, `api.ts`, `tests/test_web_dry_run_step.py` |
| steps/catalog 500 (`label`→`title`) | `app/services/project_steps.py`, `test_web_api_integration.py` |
| Guardian + e2e (6 tests) | `scripts/guardian/run-studio-audit.ps1`, `web/e2e/*.spec.ts` |
| Полный отчёт аудита | `docs/AUTONOMOUS-AUDIT-REPORT.md` |

## Из handoff другой сессии (сделать на AiCreator)

```powershell
cd "C:\Users\AiCreator\Desktop\video-pipeline"
git fetch --all --prune
git checkout devin/windows-installer
git pull --ff-only

# Guardian (после merge коммитов с web)
powershell -ExecutionPolicy Bypass -File scripts\guardian\run-web-checks.ps1
cd web
pnpm install
pnpm exec playwright install chromium
pnpm run test:e2e
```

Branch janitor (только локально пользователь):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\guardian\branch-janitor.ps1 -Apply
```

## Баги UI — нужен чеклист от пользователя

Без списка «кнопка / экран / текст ошибки» правки вслепую. Шаблон:

1. **Экран:** канвас / студия ноды / sidebar / HITL  
2. **Действие:** что нажали  
3. **Ожидание vs факт**  
4. **Скрин или строка из Network** (`/api/...` status + `detail`)

## Типичные классы багов (проверять первыми)

- Запуск шага не той ноды (`runStepNodeKey` ≠ выделение на канвасе)
- Мигание статуса нод при refetch run
- Toast `ApiError: [object Object]`
- Старый `studioTarget` после клика другой ноды
- `dry_run` на bot-шагах (должен 400)

## Перезапуск после правок web

```powershell
cd "<путь-к-video-pipeline>"
powershell -ExecutionPolicy Bypass -File .\apply-local.ps1 -SkipBuild -NoBrowser
```

Студия: http://127.0.0.1:8765 — Ctrl+F5.
