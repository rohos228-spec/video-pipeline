# QA-RUN FULL — 2026-06-02

Полный прогон по `docs/FULL-VERIFICATION.md` (автомат + браузер + API-пайплайн).

## Сводка

| Блок | Результат |
|------|-----------|
| Guardian `run-studio-audit.ps1` | **PASS** |
| Playwright e2e (11 specs) | **11 passed** (удалён дублирующий vmenu-loop spec) |
| Web pytest | **12 passed** |
| API `run-full-verification.py` | **45/45** после фикса логики dry_run + hitl path |
| Браузер (Cursor MCP) | QA-SMOKE открыт, V-menu: все пункты видны |
| Live GPT pipeline QA-SMOKE #17 | **В процессе / planning** — воркер держит `planning` (нужен живой ChatGPT) |
| Outsee/ElevenLabs img/video/audio | **Не гонялись в этом прогоне** — нужен CDP + часы времени |

**QA-проекты созданы:** `#17` qa-smoke (no_hero), `#18` qa-full (hero).

---

## §3 Оболочка UI (браузер + e2e)

| ID | Проверка | Статус |
|----|----------|--------|
| B-T1–T5 | Topbar Промты/Логи/API/версия | e2e PASS; браузер: кнопки есть |
| B-S1–S10 | Sidebar поиск, проект, wizard | e2e: поиск, новый проект; браузер: qa-smoke в списке |
| B-I1–I6 | Inspector | e2e: aside «Инспектор» |
| B-R1–R4 | Run bar | e2e: Создать Run/Перезапустить; браузер: ⏹, Сброс, Массовая, Автопродвижение |

---

## §4 Toolbar канваса (браузер snapshot)

| ID | Элемент | Виден на QA-SMOKE | Примечание |
|----|---------|-------------------|------------|
| C1 | + Нода (все типы в select) | ✓ | topic…enrich в combobox |
| C2 | Сохранить граф | ✓ | |
| C3–C4 | Копировать/Вставить/Дублировать | ✓ | Вставить disabled без буфера |
| C5 | Excel feed | ✓ | |
| C6 | WF | ✓ | |
| C7 | Удалить | ✓ | disabled без выделения |
| C8–C10 | Zoom/Fit | ✓ | |

---

## §5 V-menu (нода topic, браузер)

| Пункт | ✓ |
|-------|---|
| Закрыть меню | ✓ |
| Настройки ноды | ✓ |
| Просмотр промтов | ✓ |
| Скачать промты | ✓ |
| Запустить шаг | ✓ |
| Открепить связи | ✓ |
| Отключить ноду | ✓ |
| Удалить ноду | ✓ |

e2e `studio-smoke` § V-menu + toolbar Run — **PASS**.

---

## §6–7 Пайплайн и генерации

### API dry_run (все step codes на #18)

| step | HTTP | Ожидание |
|------|------|----------|
| plan | 200 | OK |
| script…assemble | 400 на new project | OK (нет prerequisite) |
| hero,items,img,video,audio | 400 dry_run forbidden | OK |

### Live GPT (проект #17)

| step | Статус на конец прогона |
|------|-------------------------|
| plan | `planning` (воркер занят / ждёт GPT) |
| script→anim_pr | не завершены в этом окне |

**Для полного GPT-прогона:** оставить `run-full-verification.py` работать 30–60 мин или гонять вручную шаги с паузой, пока ChatGPT доступен.

### Боевой проект #15

| Проверка | Результат |
|----------|-----------|
| status | `assembled` (не frames_ready при 10 videos) |
| videos on disk | 10 |

### Генерации Outsee / ElevenLabs (отдельный сеанс)

Чтобы закрыть «буквально всё», на **QA-FULL #18** или копии #15:

1. `hero` → hero_ready  
2. `items` (если есть в xlsx)  
3. `enrich_1..5` по графу  
4. `img_pr` → `img` → `anim_pr` → `video` → `audio` → `assemble` → `publish`  

Команда старта каждого шага:

```powershell
$base = "http://127.0.0.1:8765"
$pid = 18
Invoke-RestMethod -Method POST "$base/api/projects/$pid/steps/video/run"
```

Между шагами: `GET /api/projects/$pid` каждые 30 с, лог `data\backend.log`.

---

## Автоматизация на будущее

```powershell
cd "C:\Users\Love Space\video-pipeline"
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-everything.ps1
# только API без долгого pipeline:
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-everything.ps1 -SkipPipeline
```

Артефакты:

- `docs/QA-RUN-API-2026-06-02.json`
- этот файл

---

## Честный итог

**Сделано автоматом сейчас:** все API-эндпоинты каталога, dry_run-матрица, e2e UI-оболочка, регрессия #15, браузерный обход topbar/sidebar/canvas/V-menu, старт live plan на QA-SMOKE.

**Не сделано за одну сессию без 3–6 ч Outsee/CDP:** реальный `img`, `video`, `audio`, `publish` на чистом QA-FULL; полный HITL approve/regenerate на всех kind.

Запустите `run-everything.ps1` без `-SkipPipeline` на ночь с работающим ChatGPT и Outsee — тогда закроется GPT-цепочка; Outsee — отдельным днём по §6 `FULL-VERIFICATION.md`.
