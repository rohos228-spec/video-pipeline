# STREAM-2-REPORT

Ветка: `night/scene-space-2`  
Корень: `C:\Users\Admin\Desktop\video-pipeline\.worktrees\stream-2`

## Сделано с доказательствами (команда и её вывод)

Публичный API заморожен и работает: `size_index`, `size_from_ru`, `junction_verdict`, `assign_blocking` (только ряды `frames_space`, без записи в БД).

- §2.1 лестница EWS..ECU 0..7, русский/легаси маппинг (VLS→EWS, LS→WS, Insert→ECU), стык `ok|warn_adj|error_same|warn_smash`.
- §2.2 смена ракурса: соседние имена горизонтали без вертикали на 2+ шага = «скачок»; assigner шагает `angle_h` на 2 и не оставляет пару с одинаковыми h+v.
- §2.3 сторона через `geom.axis_side` + `normalize_pair`; в этом worktree `geom.py` нет — fallback в `rules.py` с формулой CANON (`cross = axis.x*to_cam.y - axis.y*to_cam.x`). Вырождение `cross==0` → `DegenerateAxisError`, вставка overhead, камера сдвигается на запертую сторону.
- §2.4 ракурсы из канона.
- §2.5 `screen_pos` JSON, `screen_dir` L→R/R→L/to camera/from camera/none; реверс без `turn` → служебный кадр.
- §2.6 смена стороны только с `crossing_method` (pov / reestablish / overhead / camera_move). Тихо сторону не переворачиваем.
- §2.7 `reestablish_due` на кадре-должнике; следующий WS/EWS (иначе вставка).
- §2.8 `turning_point` получает свой кадр; `rules.infer_value_charge` / `value_charge_turns` есть, в ряды кадров заряд не пишется (он в `scenes_space.space_json`).

Команды (worktree, `PYTHONPATH=.`, интерпретатор `.venv`):

```
C:\Users\Admin\Desktop\video-pipeline\.venv\Scripts\python.exe -m py_compile app/services/scene_space/shot_size.py app/services/scene_space/rules.py app/services/scene_space/blocking.py
```

Вывод: `py_compile exit 0`

```
python -c "from app.services.scene_space.shot_size import size_index, size_from_ru, junction_verdict; ..."
```

Вывод (фрагмент финального смока):

```
API size_index size_from_ru junction_verdict assign_blocking
geom_source fallback
ladder [0, 1, 2, 3, 4, 5, 6, 7]
from_ru EWS WS ECU EWS
junction error_same warn_adj ok warn_smash
rows 4 tp True
fields ['angle_h', 'angle_v', 'axis_pair', 'axis_side', 'beat_role', 'crossing_method', 'manual', 'reestablish_due', 'scene_id', 'screen_dir', 'screen_pos', 'shot_order', 'shot_size', 'space_delta_json', 'uuid']
sizes ['WS', 'MS', 'MCU', 'CU']
sides ['B', 'B', 'B', 'B']
pos {"c01":"L","c02":"R"}
empty ok
SMOKE_PASS
```

Дополнительно (тот же интерпретатор):

- Стык CU↔MWS / MS↔WS / MCU↔FS → `ok`; CU↔CU → `error_same`; EWS↔MS → `warn_smash`.
- Камера на оси → `DegenerateAxisError: camera on axis (cross == 0)`, затем overhead.
- Перенос камеры через ось (`y: -2 → 6`) → второй кадр `crossing_method=camera_move`, сторона B→A, без тихого flip.
- Три кадра крупнее MS → `reestablish_due=1`, следующий WS.
- Реверс движения c01 → кадр `insert` с `space_delta_json.turn.subject=c01`, затем новый `screen_dir`.
- Легаси в выходе не хранится: VLS/LS → EWS/WS.

## Не сделано с причиной

- `geom.py` / `store.py` / `__init__.py` — владение потока 1; импорт geom, иначе private fallback только в `rules.py`.
- Тесты в `tests/` — владение потока 4; проверка только `python -c` / `py_compile`.
- Критерии интересности §2.9 (3+ крупности, accent, мастер диалога) — в задаче потока 2 указаны §2.1–2.8; assigner не добирает coverage.
- Запись `scenes_space.value_charge` — `assign_blocking` возвращает только кадры.
- Вставка Frame в БД (`insert_frame_after`) — слой rewrite/store, поток 5/1. Здесь только новые uuid у служебных рядов.
- Правки `camera_expand.py`, `scene_shot_grammar.py`, промтов, `CONTRACTS.md` — запрещены.

## Затронутые файлы

- `app/services/scene_space/shot_size.py`
- `app/services/scene_space/rules.py`
- `app/services/scene_space/blocking.py`
- `tasks/STREAM-2-REPORT.md`

## Запросы на чужие файлы и контракты

1. **Поток 1 / `geom.py`:** экспортировать `normalize_pair(a, b) -> tuple[str, str]`, `axis_side(a_xy, b_xy, cam_xy) -> "A"|"B"`, `DegenerateAxisError` (имя из CONTRACTS §2). Сейчас `_GEOM_SOURCE=fallback`. После появления geom импорт в `rules._bind_geom()` подхватит его без правки API.
2. **CANON §2.1 внутреннее противоречие:** текст «через план = Δ=2», таблица CU↔MWS / MS↔WS / MCU↔FS на лестнице из 8 = Δ=3. Реализация: Δ=2 = ok, плюс три пары из таблицы = ok; прочий Δ≥3 = `warn_smash`. Оркестратору: одна строка в CANON.
3. **CANON §2.5 vs CONTRACTS:** формула `right = (axis.y, -axis.x)` на стандартном two-shot (оба на линии оси) даёт одинаковую проекцию → `{"c01":"C","c02":"C"}`. CONTRACTS: «меньшая экранная x слева». Считаем camera-right = поворот look (cam→середина пары) на 90° по часовой. Нужно подтверждение, чтобы поток 4 не валил `screen_pos` по другой формуле.
4. **`assign_blocking` вход:** `scene_state` = space_json или `{scene_id, space_json|plan, axes, value_charge}`; `bits[].beat_role|subjects|turning_point`; `frames[].uuid|shot_size|attrs.крупность|space_delta_json|manual`. Служебные uuid = sha256 `svc|{scene_id}|{anchor}|{kind}|{seq}` [:24].
5. **Поток 5:** `space_delta_json` в Python — dict; `screen_pos` — JSON-строка. `manual=1` поля кадра не переписываются, служебные кадры вставляются вокруг.

## Что сломалось

- Первый смок: `junction_verdict('CU','MWS')` был `warn_smash` при ожидании `ok` (Δ=3 vs таблица). Исправлено именованными парами таблицы. Это не повтор одной и той же ошибки.
- Смоки печати `L→R` в cp1251: `UnicodeEncodeError` на консоли Windows; логика жива. Дальше `PYTHONIOENCODING=utf-8`.
- Реверс `screen_dir` сначала не вставлял turn: служебный reestablish перетирал `prev_plan` уже новым дельта-планом, движение становилось нулевым. Исправлено: reestablish строится по плану *до* текущей дельты.
- Сторона оси и lock в стандартной геометрии (cam y=-2, персонажи y=2): сторона **B**, `screen_pos` c01=L c02=R. Совпадает с camera-right, не с axis-perp.
