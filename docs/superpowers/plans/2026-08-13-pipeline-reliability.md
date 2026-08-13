# Pipeline Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Нода не может стать `done`, если записала не то, не всё, не своим промтом, или после wipe оставила хвосты; живой прогон в Studio подтверждается кликами, не скриптами.

**Architecture:** Один контур поверх уже существующих кусков: slim `db_frames` (есть для img_pr, нет для excel_gpt), `apply_ops` fail-closed, `agent_harness` (сейчас N/N только на `assembled`), wipe кадров (не гасит `prompt_versions`). Добавляем контракт ноды (поля + покрытие + промт) и проводим его через apply → harness → wipe → медиа. Не тащим Temporal.

**Tech Stack:** Python 3.11, FastAPI Studio `:8765`, SQLite `data/state.db`, pytest, Playwright MCP (клики в UI).

## Global Constraints

- Ветка этой машины: `ORCHESTRATOR_GIT_BRANCH` из `.env` (сейчас `main`). После кода: commit + `git push origin <ветка>`.
- Закадр не трогать, если оператор не сказал. Audio/music не запускать (`HARNESS_FORBIDDEN_STEPS`).
- Писатель в БД один: `db_apply.apply_ops`. Excel — экспорт.
- Промт-нода (`img_pr`/`anim_pr`) ≠ excel_gpt: модель img_pr пишет только `промт_картинки` (+ персонажи); excel_gpt не пишет `image_prompt`/`animation_prompt`, если на графе есть dedicated-ноды.
- Live-проверка **только через Studio UI** (кнопки Run / Wipe / База / Монтаж). На этом ПК проект **#14 = `syostry-gonsales` `script_ready`**, это не PSYCO-110 с другого компа. Не трогать **#61** (`videos_ready`). Для кликов: проект с кадрами, не финальный фильм — **#8** (`image_prompts_ready`) или **#13** (`frames_ready`).
- После Python-правок: `stop-backend.cmd` + `STUDIO.cmd` [1], иначе UI гоняет старый PID.
- Тесты без внешних GPT/Outsee. Live GPT — только кликом в Studio.
- Не хардкодить стиль нуар: STYLE-lock берётся из проекта (style-entity / `meta.img_pr_style_id`), иначе явный fail, не молчаливый Archival Noir.

---

## Проблема (as-is, 13.08, два ПК)

Пять дыр, не 15 багов:

1. **Контракт ноды** — чужой промт, жирный `attrs` в `db_frames.json` (`enrich_xlsx.py` else-ветка `row["attrs"] = fr.attrs`), excel_gpt пишет `промт_картинки`/`промт_видео`, STYLE-lock = нуар в `img_pr_style.py`.
2. **Частичная запись = успех** — 6/N сцен, 1/25 ops, continue по единице; harness N/N только если `status == assembled`.
3. **Wipe врёт** — `_wipe_img_pr` / `_wipe_anim_pr` чистят `Frame.*`, не `PromptVersion.is_active`; монтаж `_overlay_active_prompt_versions` поднимает stale.
4. **Процесс** — STOP + рестарт рвёт шаг; фикс не на обоих ПК.
5. **Медиа** — `get_img_streams` default 1 → картинки по одной; видео: новый `clip_NNN_<hash>.mp4` рядом со старым.

Уже есть и **нельзя ломать:** `build_img_pr_db_context`, `repair_near_miss_frame_uuids`, img/video `INFLIGHT`, skip-if-exists клипа, `harness_gate_or_raise` на scene_d/anim_pr.

## Locked from code map (13.08)

Разведка: [harness](37834fa3-e258-4b4e-86d4-c444944bcfe9), [wipe](868ad7d4-6bcf-461f-bec2-195ca5d7619d), [excel_gpt](b34aa819-ba81-4ee0-ab9d-00c090f8f2b3), [img/video](c30ce9a8-5e02-4263-9017-94a0feac275d).

- **`volume_batches` / `MIN_CONTINUE_SIZE` на этом ПК нет** — это незапушенные патчи другого компа. Здесь `_FRAMES_PER_BATCH = 25`, частичный батч принимается (`xlsx_step_runners.py` L905–914). Task 3 пишет это с нуля.
- **excel_gpt не зовёт `harness_gate_or_raise`.** Частичные ops = `enrich_N_ready` + NodeRun `done`. Гейт вешать в `enrich_xlsx.run` **до** `_apply_enrich_ready_status` (~L1127 / L1451).
- **Порядок img_pr/anim_pr сломан:** статус `*_ready` + commit, потом harness. На fail статус уже ready. Гейт **до** смены статуса. `anim_pr` early-exit L216–230 без harness.
- **Wipe:** `clear_plan_animation_prompts` уже есть (`plan_sheet_v8.py` L822) — не импортирован. Для картинок хелпера нет — добавить `clear_plan_image_prompts` (R45+R46). Стереть shot2 attrs. Soft ▶ в проде всегда `force_wipe=False`. База: `baza-timeline.tsx` `activePrompt()` = PV first.
- **Видео skip слабее картинок:** `_scene_video_file_on_disk` смотрит Artifact, не glob. Дубли: UUID на каждую попытку + prune только artifact path. Claim: `_claim_shot1_video_batch` L254–298 — skip по `newest_disk_video()`.
- **Параллель картинок:** default `IMG_MAX_STREAMS=1` → serial. Не путать с `WORKER_MAX_PARALLEL` (проекты) и `CREATE_MAX_PARALLEL_OUTSEE=4` (глобальный слот).

## File map

| Файл | Роль |
|------|------|
| `app/services/db_frames_context.py` | Slim snapshot: img_pr **и** excel_gpt |
| `app/orchestrator/steps/enrich_xlsx.py` | Писать slim, не `attrs` целиком |
| `app/services/node_write_contract.py` | Allowlist полей + покрытие N/N (новый) |
| `app/services/db_apply.py` | Drop forbidden fields; coverage после apply |
| `app/services/gpt_operator_client.py` | Не done при пустых/частичных ops |
| `app/services/img_pr_batches.py` | `plan_batch_size` → цель 3 батча; не onesie |
| `app/services/xlsx_step_runners.py` | Не сжимать очередь до 1 |
| `app/services/reset_step.py` | Wipe → deactivate PV + clear R48 |
| `app/services/db_v2.py` | `deactivate_prompt_versions` |
| `app/services/montage_board.py` | Overlay не воскрешает после wipe |
| `app/services/agent_harness.py` | N/N на `image_prompts_ready` / `animation_prompts_ready` / после excel_gpt |
| `app/services/img_pr_style.py` | Стиль из проекта, не hardcoded noir |
| `app/orchestrator/steps/generate_videos.py` + `montage_board_assets.py` | Один клип на кадр |
| `app/services/img_streams.py` / UI | Default ≥2, видно в Studio |
| `tests/test_db_frames_context_img_pr.py` | Расширить excel_gpt slim |
| `tests/test_node_write_contract.py` | Новый |
| `tests/test_img_pr_batches.py` | 110 → 3 батча; onesie запрещён |
| `tests/test_reset_step.py` | PV deactivated |
| `tests/test_img_pr_style.py` | knitted wrap если проект knitted |

---

### Task 1: Slim-контекст excel_gpt

**Files:**
- Modify: `app/services/db_frames_context.py`
- Modify: `app/orchestrator/steps/enrich_xlsx.py` (~848–890)
- Modify: `tests/test_db_frames_context_img_pr.py`

**Interfaces:**
- Consumes: `build_img_pr_db_context` whitelist `_IMG_PR_ATTR_KEYS`
- Produces: `build_excel_gpt_db_context(...)` — те же ключи attrs + короткий `voiceover_text` (≤400 символов) + uuid/number; **запрет** сырого `attrs` dict. Truncate каждого attr до 500 символов.

- [ ] **Step 1: Failing test**

```python
def test_excel_gpt_context_is_slim_and_keeps_vo_snippet() -> None:
    fr = SimpleNamespace(
        number=1, uuid="ab" * 12, voiceover_text="слово " * 200,
        meaning="m", attrs={"place": "кухня", "noise": "X" * 5000, "shot01_bg": "стол"},
    )
    from app.services.db_frames_context import build_excel_gpt_db_context
    ctx = build_excel_gpt_db_context(project_id=14, slug="x", frames=[fr], characters=[])
    row = ctx["frames"][0]
    assert "noise" not in row
    assert "attrs" not in row
    assert row["place"] == "кухня"
    assert len(row["voiceover_text"]) <= 400
    blob = json.dumps(ctx)
    assert len(blob) < 20_000
```

- [ ] **Step 2:** `pytest tests/test_db_frames_context_img_pr.py::test_excel_gpt_context_is_slim_and_keeps_vo_snippet -q` → FAIL (нет функции).

- [ ] **Step 3: Implement**

В `db_frames_context.py`:

```python
_EXCEL_GPT_VO_MAX = 400
_ATTR_MAX = 500

def _clip(text: str, n: int) -> str:
    t = text.strip()
    return t if len(t) <= n else t[: n - 1] + "…"

def slim_attrs_for_excel_gpt(attrs: dict[str, Any] | None) -> dict[str, str]:
    picked = _pick_attrs(attrs)
    return {k: _clip(v, _ATTR_MAX) for k, v in picked.items()}

def build_excel_gpt_db_context(*, project_id: int, slug: str, frames: list[Any],
                               characters: list[dict[str, str]]) -> dict[str, Any]:
    rows = []
    for fr in frames:
        uuid = str(getattr(fr, "uuid", None) or "").strip()
        if not uuid:
            continue
        row = {"number": getattr(fr, "number", None), "uuid": uuid}
        vo = str(getattr(fr, "voiceover_text", None) or "")
        if vo.strip():
            row["voiceover_text"] = _clip(vo, _EXCEL_GPT_VO_MAX)
        meaning = str(getattr(fr, "meaning", None) or "").strip()
        if meaning:
            row["meaning"] = _clip(meaning, _ATTR_MAX)
        row.update(slim_attrs_for_excel_gpt(getattr(fr, "attrs", None)))
        rows.append(row)
    return {"source": "db_v2", "project_id": project_id, "slug": slug,
            "frames": rows, "characters": list(characters or [])}
```

В `enrich_xlsx.py` else-ветке (не scene_grammar, не character_registry) заменить `row["attrs"] = fr.attrs` на вызов `build_excel_gpt_db_context` целиком вместо ручной сборки `frame_rows`.

- [ ] **Step 4:** pytest тот же файл `-q` → PASS.

- [ ] **Step 5:** Commit `fix(excel_gpt): slim db_frames context so the model is not crushed by attrs`

---

### Task 2: Контракт записи ноды (поля + покрытие)

**Files:**
- Create: `app/services/node_write_contract.py`
- Modify: `app/services/db_apply.py` (`apply_ops`)
- Modify: `app/services/gpt_operator_client.py` (после extract ops)
- Create: `tests/test_node_write_contract.py`

**Interfaces:**
- Produces:
  - `PROMPT_FIELDS = frozenset({"image_prompt", "animation_prompt", "image_prompt_shot2", "animation_prompt_shot2"})`
  - `filter_ops_for_node(ops, *, node_kind: str) -> list[dict]`
  - `coverage_report(ops, expected_uuids: list[str]) -> Coverage` dataclass `matched, missing, extra, ok`
  - `node_kind`: `"img_pr"` | `"anim_pr"` | `"excel_gpt"` | `"excel_gpt_no_prompts"`
- excel_gpt на графе с нодами img_pr/anim_pr → `excel_gpt_no_prompts`: выкинуть prompt fields (не падать), логировать count.
- Пустые ops при `expected_uuids` → ошибка, не done.
- Неизвестный uuid по-прежнему `ApplyOpsError` (fail-closed). Не salvage в «успех».
- Покрытие: после apply для img_pr/anim_pr `ok` только если `matched == expected` (все uuid батча/шага). Частичный батч — checkpoint, не `*_ready`.

- [ ] **Step 1: Tests**

```python
from app.services.node_write_contract import filter_ops_for_node, coverage_report

def test_excel_gpt_strips_prompt_fields():
    ops = [{"frame_uuid": "aa", "fields": {"место": "кухня", "промт_картинки": "NO", "промт_видео": "NO"}}]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert "image_prompt" not in out[0]["fields"] and "промт_картинки" not in out[0]["fields"]
    assert out[0]["fields"]["место"] == "кухня"

def test_coverage_fails_if_one_missing():
    r = coverage_report(
        [{"frame_uuid": "a", "fields": {"x": "1"}}],
        expected_uuids=["a", "b"],
    )
    assert r.ok is False and r.missing == ["b"]

def test_empty_ops_not_ok():
    r = coverage_report([], expected_uuids=["a"])
    assert r.ok is False
```

- [ ] **Step 2:** pytest FAIL.

- [ ] **Step 3:** Реализовать модуль. В `apply_ops` опциональный `node_kind`. В operator client: если `outputMode=project_file` и ops пустые после extract — raise; если coverage не ok — не ставить node done, оставить running + checkpoint.

- [ ] **Step 4:** pytest PASS + `tests/test_gpt_operator_apply_ops.py` не сломать.

- [ ] **Step 5:** Commit `fix(db): node write contract — strip foreign prompt fields, require N/N coverage`

---

### Task 3: img_pr батчи — 3 чанка, запрет onesie

**Files:**
- Modify: `app/services/img_pr_batches.py`
- Modify: `app/services/xlsx_step_runners.py` (цикл батчей ~733+)
- Modify: `tests/test_img_pr_batches.py`

**Interfaces:**
- Produces: `plan_batch_size(n_frames: int, *, target_batches: int = 3, min_size: int = 8, max_size: int = 40) -> int`
- 110 кадров → size 37 → 3 батча. 5 кадров → 1 батч (не резать < min_size).
- Если модель вернула 1 op из батча ≥8: **не** принимать delivered=1 как ёмкость. Остаток — чанки `max(min_size, remaining // 2)`, минимум 8, пока remaining ≥ 8; хвост < 8 одним чанком.
- Удалить/заменить `test_199_frames_batch_count_under_ten` (сейчас 8×25). Новый: `test_110_frames_three_batches`.

```python
def test_plan_batch_size_110_is_three():
    from app.services.img_pr_batches import plan_batch_size, chunk_frames
    size = plan_batch_size(110)
    chunks = chunk_frames(list(range(110)), size=size)
    assert len(chunks) == 3
    assert all(len(c) >= 36 for c in chunks)

def test_tiny_delivery_does_not_become_onesie():
    from app.services.img_pr_batches import repartition_remaining
    rest = list(range(24))
    chunks = repartition_remaining(rest, delivered=1, min_size=8)
    assert all(len(c) >= 8 for c in chunks)
    assert len(chunks) <= 3
```

- [ ] **Step 5:** Commit `fix(img_pr): three batches for ~110 frames, never continue by onesie`

---

### Task 4: Wipe гасит prompt_versions и R48

**Files:**
- Modify: `app/services/db_v2.py` — `deactivate_prompt_versions(session, project_id, kind: str)`
- Modify: `app/services/reset_step.py` — `_wipe_img_pr`, `_wipe_anim_pr`
- Modify: `app/services/montage_board.py` — overlay: если `Frame.image_prompt` пуст **и** шаг стёрт, не подставлять PV (или PV уже inactive — тогда overlay сам ноль)
- Modify: `tests/test_reset_step.py`

**Interfaces:**
- `deactivate_prompt_versions(session, project_id, *, kind: str) -> int` — все active PV этого kind → `is_active=False`. Не удалять историю.
- `_wipe_anim_pr`: вызвать существующий `clear_plan_animation_prompts` (`app/storage/plan_sheet_v8.py` L822). Плюс `SHOT2_VIDEO_PROMPT_ATTR`.
- `_wipe_img_pr`: добавить `clear_plan_image_prompts` (R45+R46) рядом с R48-хелпером. Плюс `SHOT2_PROMPT_ATTR`. Overlay в монтаже после deactivation сам станет пустым; База `activePrompt()` тоже.
- Soft ▶ (`force_wipe=False`) **не** деактивирует PV. `force_wipe=True` в проде сейчас нигде не ставится — полный wipe = только `reset_step()`.

```python
@pytest.mark.asyncio
async def test_wipe_img_pr_deactivates_prompt_versions(session):
    p = await _mkproject(session)
    # frame + active PV kind=img + Frame.image_prompt
    await reset_step(session, p, "img_pr")
    # Frame.image_prompt is None; PV.is_active is False
```

- [ ] **Step 5:** Commit `fix(wipe): deactivate img/video prompt_versions so UI cannot resurrect stale texts`

---

### Task 5: Harness N/N на ready шагов, не только assembled

**Files:**
- Modify: `app/services/agent_harness.py` — `harness_gate_or_raise` (L632) + step checks
- Modify: `app/orchestrator/steps/enrich_xlsx.py` — вызов **до** `_apply_enrich_ready_status`
- Modify: `app/orchestrator/steps/generate_image_prompts.py` — гейт **до** L60 status set
- Modify: `app/orchestrator/steps/make_animation_prompts.py` — гейт до L434 **и** на early-return L219–230
- Tests: `tests/test_agent_harness.py` (создать/расширить)

**Правило:**
- Единая точка: `harness_gate_or_raise(session, project, step=...)`. `auto_advance._harness_gate` не дублировать логику — второй рубеж.
- `img_pr`: все VO-кадры с `image_prompt`; гейт до `image_prompts_ready`.
- `anim_pr`: кадры с usable image_prompt имеют anim; гейт на early-exit тоже.
- `excel_gpt` / `enrich_N`: coverage ops vs expected uuids; без `harness_gate_or_raise` сейчас — добавить.
- `assembled` parity как сейчас.

- [ ] **Step 5:** Commit `fix(harness): block ready→next unless DB coverage is N/N`

---

### Task 6: STYLE-lock из проекта, не hardcoded noir

**Files:**
- Modify: `app/services/img_pr_style.py`
- Tests: `tests/test_img_pr_style.py` + поправить `test_img_pr_batches.py::test_parse_img_pr_ops_wraps_style` (не требовать Archival Noir всегда)

**Правило:** `wrap_ops_styles(ops, *, style_id: str | None, style_block: str | None)`. Если у проекта entity/style knitted (или `meta.img_style` / блок из `prompts`) — вшивать knitted. Если стиль не задан — **не** подставлять нуар; оставить сцену как есть и залогировать warning. Live #14 PSYCO ждал knitted.

- [ ] **Step 5:** Commit `fix(img_pr): style lock from project, not default archival noir`

---

### Task 7: Медиа — параллель картинок и один клип на кадр

**Files:**
- `app/services/img_streams.py` — fallback кода если meta/JSON unset: **2** (сейчас env `IMG_MAX_STREAMS=1` → serial). Не перетирать `runtime_streams.json` / meta проекта, если уже 3–4.
- `app/orchestrator/steps/generate_videos.py` — `_claim_shot1_video_batch` (L254–298): skip по диску (`newest_disk_video` / `_disk_has_frame_video_shot1`), не только Artifact.
- `app/services/post_step_validate.py` `mark_frames_for_video_regen` — glob-delete `clip_{NNN}_*` (shot1) / `_s2_` отдельно, не только `a.path`.
- После успешной записи нового клипа: старые sibling того же кадра → `old/videos/` (кроме другого shot).
- Tests: skip-if-disk-exists; prune orphans.

Не путать три ручки: `WORKER_MAX_PARALLEL` (проекты), `outsee_streams` (кадры шага), `CREATE_MAX_PARALLEL_OUTSEE` (глобальный API ≤4).

- [ ] **Step 5:** Commit `fix(media): default 2 outsee streams; one clip file per frame`

---

### Task 8: Live Studio (клики, не API-скрипты)

Целевой проект на **этом** ПК: **#8** (есть image prompts) для wipe/оверлея; **#13** если нужен excel_gpt на кадрах. Не #61, не чужой PSYCO-#14.

Протокол после каждого кода + рестарт бэкенда:

1. Studio → выбрать проект кнопкой в сайдбаре.
2. Открыть граф. Для Task 4: нода «Промты картинок» → Reset/Wipe (UI) → База и Монтаж: промты пустые, не 200-символьный stale.
3. Для Task 3/6: Run img_pr на проекте без промтов (или soft ▶ только missing) — в Логах 3 батча, не 24 onesie; в тексте промта нет Archival Noir, если стиль проекта не нуар.
4. Для Task 7: нода Картинки — в логах `outsee_streams=2` (или meta проекта); нода Видео — в папке `videos/` не два `clip_00N_*.mp4` на один кадр.
5. Стоп: если UI требует confirm wipe — нажать. Audio/music не нажимать.

Фиксировать скрин/snapshot после каждого клика.

---

## Порядок и агенты

Последовательные implementer-агенты (файлы пересекаются через `db_apply` / wipe / harness — не параллелить правки одного модуля). Explore уже снят.

1 → 2 → 3 → 4 → 5 → 6 → 7, после каждого: pytest задачи + Task 8 кусок в UI + commit/push `main`.

Критерий готово: тесты зелёные; UI #8 wipe не воскрешает PV; img_pr на живом проекте не onesie; `git HEAD` в `main`.
