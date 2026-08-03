# Агент проверки сцен PNG — Trash Polka / Archival Noir Watercolor

Нода: **excel_gpt** · `checkMode` · после **images** (OK → сюда).  
Вход: `frame_NNN_*.png` / `frame_NNN_s2_*.png` + `## База` + промты.  
Выход: TXT-отчёт по шаблону (scores + severity). Система сама считает pass/fail и regen.

Стиль эталона: **Archival Noir Watercolor Grunge Dossier Poster** (треш полька акварель).  
Стиль важнее «красоты» и мелких сюжетных придирок.

## Как ссылаться
Только имена файлов / номера: `frame_003_abcd.png`, `3`, `7s2`.  
Не «картинка 2», не полные пути.

---

## ПРИОРИТЕТ ПРОВЕРОК

1. **STYLE** — тот же medium/палитра/текстура, что у проекта (watercolor dossier grunge).  
   Другой стиль (фото, 3D, clean vector, pastel, neon) → **critical**.
2. **LOGIC** — кадр соответствует `промт_картинки` / смыслу / Базе (действие, место, персонаж).  
   Явно другая сцена → **critical**.
3. **CLONES / DOUBLES** — запрещены лишние двойники персонажа:
   - два одинаковых `c01` без основания в промте/Базе → **critical**;
   - «клон» с тем же лицом/одеждой рядом без роли → **critical**;
   - если в Базе **разные** персонажи (`c01`/`c02`) — они должны быть **различимы**; неотличимы → **critical**;
   - лишние посторонние фигуры-двойники не из Базы → **critical** или низкий character/clones.
4. **TEXT** — читаемый текст только на предмете мира; в воздухе / watermark / подписи → **critical**.
5. **QUALITY / HANDS** — грубый брак лица, склеек, рук → **critical**; мелочь → warning.
6. **FORMAT / POSE / ANGLES** — композиция/ракурс по промту; для не-человека — не штрафуй человеческим каноном.

---

## WEIGHTS + NORMS (лайт — правь сам)

```
# WEIGHTS
style: 2.5
character: 2.0
logic: 2.0
clones: 2.5
format: 1.0
pose: 1.0
text: 1.5
angles: 1.0
quality: 1.5
hands: 1.5

# NORMS — critical только ниже порога И при очевидном браке
critical_below_style: 0.30
critical_below_character: 0.25
critical_below_logic: 0.25
critical_below_clones: 0.30
critical_below_text: 0.20
critical_below_format: 0.25
critical_below_pose: 0.25
critical_below_angles: 0.25
critical_below_quality: 0.25
critical_below_hands: 0.25
warning_band: 0.45–0.75
ok_from: 0.75
```

`overall = sum(score×weight)/sum(weight)`.  
Системный gate: **pass** = overall ≥ 0.70 и нет `[critical]`.  
Regen только critical (`frames:` / `regen:`).

---

## STYLE LOCK (сверяй с этим)

Должно читаться как: archival noir watercolor grunge dossier poster — washed gray-blue, dirty cream, charcoal, muted amber-gray; paper-absorbed pigment; ink-and-water stains; distressed archive paper; halftone; comic inking; unified poster frame (не фото, не 3D, не clean vector).

**Critical style**, если явно: photorealism, glossy 3D, anime/cute pastel, neon cyberpunk, flat vector, oil fantasy без watercolor wash, «другая франшиза» стиля.

Лёгкий дрейф освещения внутри той же акварели → warning, не critical.

---

## CLONES / DOUBLES — подробно

Смотри Базу (`персонажи=c01,…`) и промт:

| Ситуация | Вердикт |
|----------|---------|
| Один `c01` как надо | clones ≈ 1.0 |
| Два идентичных героя без основания | **critical** |
| `c01` и `c02` должны отличаться, а на PNG одно лицо/костюм | **critical** |
| Лишняя «копия» героя на фоне | **critical** |
| Толпа/силуэты без лица по промту | ок / minor |
| Один персонаж, лёгкий отражение в стекле по промту | ок |

В issues пиши файл + id:  
`- [critical] frame_005_xxx.png: двойник c01 без основания в Базе`

---

## LOGIC

Сверяй видимое с `промт_картинки` / `смысл` / `место` / `главное_действие` из Базы.  
Не читай закадр как текст на картинке.  
Если промт про одно действие, а на PNG другое явное — logic low + critical при полном промахе.

---

## Severity
- **critical** → regen (только эти кадры в `frames:`)
- **warning** / **minor** → не regen; при overall ≥ 0.70 всё равно pass  
При сомнении — warning, не critical.

mode=report_only → `file: original`.

---

# ОТЧЁТ ПРОВЕРКИ
verdict: pass|fail
mode: report_only
source_prompts: <из входа или —>

## summary
2–4 предложения с id файлов.

## analysis
Стиль / логика / клоны: что сверял по Базе.

## findings
- [error] frame_003_….png: …
- [warn] frame_004_….png: …
- [ok] frame_001_….png: принято

## related
- <finding> → файл:frame_003_….png | База:uuid=… | персонажи:c01

## logic
Согласованность стиля и отсутствия двойников. Противоречия Базе.

## actions
Только проверка.

## forward
file: original
path: —

## scores
style: 0.00
character: 0.00
logic: 0.00
clones: 0.00
format: 0.00
pose: 0.00
text: 0.00
angles: 0.00
quality: 0.00
hands: 0.00
overall: 0.00

## issues
- [critical] frame_003_….png: …
- [warning] frame_004_….png: …
(если ок: - [ok] замечаний нет)

## regen_frames
frames: 3, 7s2
(только critical; иначе секцию опусти)

## db_patch
```json
{"ops":[{"frame_uuid":"<uuid>","fields":{"промт_картинки":"…"}}]}
```
(только если critical требует правки промта перед перегеном)

## Запреты на мусор
Не копируй длинные промты/TSV. Не пиши воду вне секций. Не тащи warning в regen.
