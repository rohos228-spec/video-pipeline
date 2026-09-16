# STREAM-5-REPORT

Ветка: `night/scene-space-5`  
Worktree: `.worktrees/stream-5`  
Push: нет. `main` не трогали. `data/state.db` не использовали.

## 1. Сделано

- `rewrite(meaning)` пересобирает bits + desired shots, пишет `scenes_space.space_json.input_hash` (sha256 канонического JSON: meaning, bits, non-manual shots). Повтор с тем же хешем → `changed` пустой (inserted/updated/skipped = []).
- `rebuild()` вызывает `rewrite` с текущим `Scene.meaning`. Тот же хеш.
- Кадр `manual=1` (в `fix:dialogue` это uuid `d1a100000000000000000003`) не меняет полей `frames_space`. В логе — `skipped`.
- Служебные кадры, если blocking вернёт лишний ряд: `db_v2.insert_frame_after` (импорт, файл не правили).
- Три фикстуры по **10 кадров**, geometry по CANON §2.3–2.5 / §2.9, промты `PROMPT:` + `NEGATIVE PROMPT:` без штампов из `ai_cliches_ru.md`.
- Store/blocking/validate в этом worktree нет: seed/CRUD через raw SQL (`CREATE TABLE` как в CONTRACTS) внутри `rewrite.py`. `store.py` не создавали.
- E2e сидит в изолированном sqlite `tasks/out/scene_space_e2e.db` (gitignore), не в `data/state.db`.

Счётчики кадров:

| scene_id | кадров | покрытие |
|----------|--------|----------|
| `fix:dialogue` | 10 | master EWS/FS + single c01 + single c02 + reaction + turning_point + 3+ sizes + unique accent + value +→−; один `manual=1` |
| `fix:cross` | 10 | L→R walk, `reestablish_due` затем EWS, `crossing_method=reestablish`, сторона B→A |
| `fix:turn` | 10 | `space_delta_json.turn`, smash insert MS→ECU (`beat_role=insert`, Δsize=3), screen_dir L→R затем R→L |

## 2. Файлы

Только владение потока 5 + этот отчёт:

```
app/services/scene_space/rewrite.py
scripts/scene_space_rewrite.py
scripts/scene_space_rebuild.py
scripts/scene_space_e2e.py
tests/test_scene_space_rewrite.py
tests/test_scene_space_e2e.py
tests/fixtures/scene_space/dialogue.json
tests/fixtures/scene_space/cross.json
tests/fixtures/scene_space/turn.json
tasks/STREAM-5-REPORT.md
```

Не создавали: `store.py`, `__init__.py`, `models.py`, `validate.py`, `blocking.py`, `web/`, `prompts/`, `FIELD_ALIASES`.

## 3. Проверки

Интерпретатор: `C:\Users\Admin\Desktop\video-pipeline\.venv\Scripts\python.exe`  
`cd` worktree; `$env:PYTHONPATH = (Get-Location).Path`. pip install -e не вызывали.

```
python -m pytest tests/test_scene_space_rewrite.py tests/test_scene_space_e2e.py -q --tb=short
```

```
...........                                                              [100%]
11 passed in 2.27s
```

```
python scripts/scene_space_rewrite.py --scene fix:dialogue --meaning "A wins then loses the folder"
```

Первый прогон (после seed 10 кадров):

```
{"scene_id": "fix:dialogue", "changed": {"inserted": [], "updated": ["d1a100000000000000000001", "d1a100000000000000000002", "d1a100000000000000000004", "d1a100000000000000000005", "d1a100000000000000000006", "d1a100000000000000000007", "d1a100000000000000000008", "d1a100000000000000000009", "d1a10000000000000000000a"], "skipped": ["d1a100000000000000000003"]}}
```

Второй прогон:

```
{"scene_id": "fix:dialogue", "changed": {"inserted": [], "updated": [], "skipped": []}}
```

```
python scripts/scene_space_rebuild.py --scene fix:dialogue
python scripts/scene_space_rebuild.py --scene fix:dialogue
```

Оба:

```
{"scene_id": "fix:dialogue", "changed": {"inserted": [], "updated": [], "skipped": []}}
```

```
python scripts/scene_space_e2e.py
```

Код выхода 0. Фрагмент JSON:

```
"frame_counts": {"fix:dialogue": 10, "fix:cross": 10, "fix:turn": 10}
"coverage": {"fix:dialogue": [], "fix:cross": [], "fix:turn": []}
"rewrite_second.changed": empty
"rebuild_second.changed": empty
"db": "isolated (not data/state.db)"
```

`scene_space_validate.py` / plan / board в worktree нет — e2e записал request, не падал.

## 4. Запросы оркестратору

После merge потоков 1–4 в эту ветку:

1. **stream 1** — `app.services.scene_space.store` (+ migrate, `normalize_pair`, `axis_side`, `apply_deltas`, `DegenerateAxisError`). Сейчас fallback raw SQL в `rewrite.py` с той же схемой CONTRACTS. Подменить импортами; сигнатуры уже совпадают.
2. **stream 2** — `blocking.assign_blocking`. Сейчас desired shots = текущие ряды (фикстуры уже зелёные по 2.9). Когда появится blocking, rewrite начнёт вставлять служебные кадры через `insert_frame_after`.
3. **stream 4** — `validate.py` + `scripts/scene_space_validate.py --fixtures`. Фикстуры считались так:
   - `axis_side`: `cross = axis.x * to_cam.y - axis.y * to_cam.x`, пара нормализована сортировкой id.
   - `screen_pos`: проекция `(pos - cam)` на camera-right (`facing 0 → +Y`, right = facing+90° = +X). Не `pos · (axis.y, -axis.x)` — у концов оси это всегда одно число, L/R схлопнется.
   Если валидатор возьмёт буквальный `right = (axis.y, -axis.x)` без camera-right, `fix:*` станут красными. Просьба зафиксировать camera-right в CONTRACTS/geom.
4. **stream 3** — `scene_space_plan.py` / `scene_space_board.py`. E2e только документирует запрос.
5. `__init__.py` пакета `scene_space` — владение потока 1. Импорт `app.services.scene_space.rewrite` идёт как namespace package.

## 5. Вне скоупа / не делали

- Studio UI, `web/`, нода канваса, Excel R*, `camera_expand` / grammar
- `prompts/**`, `db_apply.FIELD_ALIASES`
- hypothesis, push, merge в `main` / `housepc`
- продакшен `data/state.db`
- создание `store.py` / `validate.py` / `blocking.py`
