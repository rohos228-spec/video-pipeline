# Design: Excel С‚РѕР»СЊРєРѕ Import / Export (DB SoT)

**Р”Р°С‚Р°:** 2026-08-17  
**РЎС‚Р°С‚СѓСЃ:** approved (РґРёР°Р»РѕРі) в†’ spec  
**Р РµС€РµРЅРёСЏ:** A (Excel С‚РѕР»СЊРєРѕ РєРЅРѕРїРєРё) + A1 (РёРјРїРѕСЂС‚ С‚РѕР»СЊРєРѕ СЏРІРЅС‹Р№) + T1 (С‚Р°Р№РјРєРѕРґС‹ С‚РѕР»СЊРєРѕ РІ Р‘Р”) + РїРѕРґС…РѕРґ 2 (С„Р°СЃР°Рґ + С„Р°Р·С‹) + С„Р°Р·Р° 5 (С‡РµРєР»РёСЃС‚ 30 РѕРїР°СЃРЅС‹С… РјРµСЃС‚)

---

## 1. РљРѕРЅС‚СЂР°РєС‚

| РџСЂР°РІРёР»Рѕ | РЎРјС‹СЃР» |
|---------|--------|
| SoT | SQLite: `Frame`, `prompt_versions`, `frame_texts`, `start_ts`/`end_ts`, `duration_sec`, `entities`, `scenes`, `frame_edges`, `project.meta` |
| Excel | РџР°СЃСЃРёРІРЅС‹Р№ С„Р°Р№Р» `project.xlsx`. РњРµРЅСЏРµС‚СЃСЏ **С‚РѕР»СЊРєРѕ** СЏРІРЅС‹Рј Р­РєСЃРїРѕСЂС‚РѕРј РёР»Рё РРјРїРѕСЂС‚РѕРј |
| РџР°Р№РїР»Р°Р№РЅ / GPT / montage / ASR / soft-retry | **РќРµ РїРёС€СѓС‚** РІ Excel Рё **РЅРµ С‡РёС‚Р°СЋС‚** Excel РєР°Рє SoT |
| Write-through РїРѕСЃР»Рµ `apply_ops` | **Р’С‹РєР»СЋС‡РµРЅ** (СЂР°РЅСЊС€Рµ `export_xlsx=True` РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ) |

РЈСЃРїРµС…: РІРѕ РІСЂРµРјСЏ СЂР°Р±РѕС‚С‹ С€Р°РіРѕРІ РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂР° mtime/`project.xlsx` РЅРµ РјРµРЅСЏРµС‚СЃСЏ; РїРѕСЃР»Рµ РїСЂР°РІРѕРє РІ В«Р‘Р°Р·РµВ» С„Р°Р№Р» РЅРµ РѕР±РЅРѕРІР»СЏРµС‚СЃСЏ, РїРѕРєР° РЅРµ РЅР°Р¶Р°Р»Рё В«Р­РєСЃРїРѕСЂС‚В»; В«РРјРїРѕСЂС‚В» вЂ” РµРґРёРЅСЃС‚РІРµРЅРЅС‹Р№ Excelв†’DB РїСѓС‚СЊ РІ СЂР°РЅС‚Р°Р№РјРµ.

---

## 2. РђСЂС…РёС‚РµРєС‚СѓСЂР° (С„Р°СЃР°Рґ)

Р•РґРёРЅР°СЏ С‚РѕС‡РєР° I/O (РёРјРµРЅР° СЂР°Р±РѕС‡РёРµ, РјРѕР¶РЅРѕ РѕР±РµСЂРЅСѓС‚СЊ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёРµ):

| API | Р РѕР»СЊ |
|-----|------|
| `export_project_xlsx(session, project)` | **Р•РґРёРЅСЃС‚РІРµРЅРЅР°СЏ** Р·Р°РїРёСЃСЊ РІ `project.xlsx` (СѓР¶Рµ РµСЃС‚СЊ РІ `app/services/db_apply.py`) |
| `import_project_xlsx(session, project, path?)` | **Р•РґРёРЅСЃС‚РІРµРЅРЅС‹Р№** Excelв†’DB (РѕР±С‘СЂС‚РєР° РЅР°Рґ `sync_project_xlsx` / `import_v8_xlsx`) |

РџСЂР°РІРёР»Р° СЂРµР°Р»РёР·Р°С†РёРё:

1. `apply_ops(..., export_xlsx=False)` РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ; callers РЅРµ РїРµСЂРµРґР°СЋС‚ `True` (РєСЂРѕРјРµ СЏРІРЅРѕРіРѕ UI Export, РµСЃР»Рё РєРѕРіРґР°-С‚Рѕ СЃРѕРІРјРµС‰Р°Р»Рё вЂ” РЅРµ СЃРѕРІРјРµС‰Р°С‚СЊ).
2. UI В«Р‘Р°Р·Р°В»: СѓР±СЂР°С‚СЊ auto-export РїРѕСЃР»Рµ РїСЂР°РІРѕРє; РѕСЃС‚Р°РІРёС‚СЊ РєРЅРѕРїРєСѓ В«Р­РєСЃРїРѕСЂС‚ РІ ExcelВ».
3. Р›СЋР±С‹Рµ `write_plan_*`, mid-pipeline `ProjectSheet.write_*`, `writeback_project_xlsx`, R15/R50 writers вЂ” Р·Р°РїСЂРµС‰РµРЅС‹ РІ runtime РїР°Р№РїР»Р°Р№РЅР° (СЃРЅР°С‡Р°Р»Р° warning+no-op / raise РІ dev-assert, Р·Р°С‚РµРј СѓРґР°Р»РµРЅРёРµ call sites).
4. Auto `sync_project_xlsx`, `sync_animation_prompts_from_xlsx`, img `bootstrap_*` / `apply_image_prompts_from_xlsx*` РЅР° СЃС‚Р°СЂС‚Рµ С€Р°РіРѕРІ вЂ” РІС‹РєР»СЋС‡РёС‚СЊ.
5. Readers (audio, anim_pr, montage, img, assemble) вЂ” С‚РѕР»СЊРєРѕ Р‘Р”; Excel РЅРµ fallback.
6. GPT `project_file` / TSV writeback в†’ РѕС‚РєР°Р·: С‚РѕР»СЊРєРѕ apply-ops JSON РІ Р‘Р”.

REST (СѓР¶Рµ С‡Р°СЃС‚РёС‡РЅРѕ РµСЃС‚СЊ):

- `POST /api/db/projects/{id}/export-xlsx` вЂ” СЌРєСЃРїРѕСЂС‚  
- РЇРІРЅС‹Р№ РёРјРїРѕСЂС‚: СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёРµ В«РџРµСЂРµС‡РёС‚Р°С‚СЊ xlsxВ» / upload+sync РІ `project_ops` вЂ” РѕСЃС‚Р°РІРёС‚СЊ **С‚РѕР»СЊРєРѕ** РєР°Рє user-triggered Import; СѓР±СЂР°С‚СЊ СЃРєСЂС‹С‚С‹Р№ sync РёР· worker/start_step.

---

## 3. Р¤Р°Р·С‹ 1вЂ“4

### Р¤Р°Р·Р° 1 вЂ” Р·Р°РєСЂС‹С‚СЊ Р·Р°РїРёСЃСЊ РІ Excel
- Default `export_xlsx=False` РІ `apply_ops` + РІСЃРµ callers РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂР°.
- РЈР±СЂР°С‚СЊ: `write_asr_timestamps_to_r15`, `write_plan_durations` РёР· С€Р°РіРѕРІ; montage best-effort R45/R48/R64; `save_animation_prompt_shot2`в†’disk Excel; GPT `writeback_project_xlsx`.
- `write_sheet_cell`: Р»РёР±Рѕ deprecate, Р»РёР±Рѕ В«РїСЂР°РІРєР° = Import semanticsВ» С‚РѕР»СЊРєРѕ РёР· UI РРјРїРѕСЂС‚; РЅРµ mid-pipeline.
- `ProjectSheet.write_frame/write_general`: Р·Р°РєСЂРµРїРёС‚СЊ no-op РЅР° v8; callers РЅРµ РїРѕР»Р°РіР°СЋС‚СЃСЏ РЅР° Excel status.

### Р¤Р°Р·Р° 2 вЂ” Р·Р°РєСЂС‹С‚СЊ Р°РІС‚Рѕ-РёРјРїРѕСЂС‚
- РЈР±СЂР°С‚СЊ sync РёР· `project_steps.start_step`, soft-retry (`step_failure_policy`), `main` backfill, `reset_step` R48 sync, `generate_images` bootstrap/apply from xlsx.
- РРјРїРѕСЂС‚ С‚РѕР»СЊРєРѕ REST/РєРЅРѕРїРєР°.

### Р¤Р°Р·Р° 3 вЂ” readers РЅР° Р‘Р”
- audio / anim_pr: VO РёР· `Frame.voiceover_text` / `frame_texts`.
- РўР°Р№РјРєРѕРґС‹: РїРёСЃР°С‚СЊ Рё С‡РёС‚Р°С‚СЊ `Frame.start_ts`/`end_ts` (Рё duration РІ Frame); R15 С‚РѕР»СЊРєРѕ РїСЂРё Export.
- img: РЅРµС‚ РїСЂРѕРјС‚РѕРІ РІ Р‘Р” в†’ РѕС€РёР±РєР° СЃ СѓРєР°Р·Р°РЅРёРµРј В«split / РРјРїРѕСЂС‚В», РЅРµ bootstrap РёР· xlsx.
- montage: РїСЂРѕРјС‚С‹ РёР· DB/`prompt_versions`, Р±РµР· R45/R48 fallback.

### Р¤Р°Р·Р° 4 вЂ” СЃС‚СЂР°Р¶Рё Рё РґРѕРєРё
- РўРµСЃС‚С‹: orchestrator steps РЅРµ РІС‹Р·С‹РІР°СЋС‚ openpyxl save / `write_plan_*` / `writeback_*`.
- РўРµСЃС‚: `apply_ops` РЅРµ РјРµРЅСЏРµС‚ С„Р°Р№Р».
- РўРµСЃС‚: `start_step` РЅРµ РІС‹Р·С‹РІР°РµС‚ `sync_project_xlsx`.
- РћР±РЅРѕРІРёС‚СЊ `docs/DB_V2.md`, `docs/AGENT_MAP.md`, ops rule (R48 вЂ” Р·РµСЂРєР°Р»Рѕ С‚РѕР»СЊРєРѕ РїРѕСЃР»Рµ Export, РЅРµ SoT).

---

## 4. Р РёСЃРєРё Рё РєСЂРёС‚РµСЂРёР№ done

| Р РёСЃРє | РњРёС‚РёРіР°С†РёСЏ |
|------|-----------|
| РЎС‚Р°СЂС‹Рµ РїСЂРѕРµРєС‚С‹: РїСЂРѕРјС‚С‹ С‚РѕР»СЊРєРѕ РІ xlsx, Р‘Р” РїСѓСЃС‚Р°СЏ | РћРґРёРЅ СЏРІРЅС‹Р№ Import РїРµСЂРµРґ Р·Р°РїСѓСЃРєРѕРј; UI/РѕС€РёР±РєР° С€Р°РіР° РїРѕРґСЃРєР°Р·С‹РІР°РµС‚ |
| Audio Р±РµР· R15 РІ С„Р°Р№Р»Рµ | ASR РїРёС€РµС‚ РІ Frame; Export РІС‹РіСЂСѓР¶Р°РµС‚ R15 |
| РћРїРµСЂР°С‚РѕСЂС‹ РїСЂР°РІСЏС‚ Excel СЂСѓРєР°РјРё Рё Р¶РґСѓС‚ Р°РІС‚РѕРїРѕРґС…РІР°С‚Р° | Р”РѕРєСѓРјРµРЅС‚Р°С†РёСЏ + РєРЅРѕРїРєР° РРјРїРѕСЂС‚; auto-sync СѓР±СЂР°РЅ |
| GPT РІСЃС‘ РµС‰С‘ РѕС‚РґР°С‘С‚ TSV/xlsx | Reject writeback; format-retry РЅР° apply-ops |
| UI Р‘Р°Р·Р° В«РїСЂРѕРїР°Р»В» Excel sync | РЇРІРЅР°СЏ РєРЅРѕРїРєР° Р­РєСЃРїРѕСЂС‚; optional toast В«С„Р°Р№Р» РЅРµ РѕР±РЅРѕРІР»С‘РЅВ» |

**Done:** С‡РµРєР»РёСЃС‚ С„Р°Р·С‹ 5 РІСЃРµ `closed`; smoke: РїСЂР°РІРєР° Р‘Р°Р·Р° в†’ С„Р°Р№Р» РЅРµ РјРµРЅСЏРµС‚СЃСЏ в†’ Р­РєСЃРїРѕСЂС‚ РјРµРЅСЏРµС‚ в†’ РРјРїРѕСЂС‚ РїРѕРґРЅРёРјР°РµС‚ РІ Р‘Р”; С€Р°Рі img/anim_pr/audio Р±РµР· openpyxl save.

---

## 5. Р¤Р°Р·Р° 5 вЂ” С‡РµРєР»РёСЃС‚ РѕРїР°СЃРЅС‹С… РјРµСЃС‚ (РѕР±РѕР·РЅР°С‡РёС‚СЊ в†’ Р·Р°РєСЂС‹С‚СЊ)

РЎС‚Р°С‚СѓСЃС‹: `open` | `closed`. Р—Р°РєСЂС‹С‚РёРµ РїСЂРёРІСЏР·Р°РЅРѕ Рє С„Р°Р·Р°Рј 1вЂ“4.

### A. Р—Р°РїРёСЃСЊ РІ Excel (РґРѕР»Р¶РЅС‹ СѓРјРµСЂРµС‚СЊ РІ runtime)

| # | РњРµСЃС‚Рѕ | Р¤Р°Р№Р» / СЃРёРјРІРѕР» | Р¤Р°Р·Р° | РЎС‚Р°С‚СѓСЃ |
|---|--------|---------------|------|--------|
| 1 | `apply_ops` + `export_xlsx=True` | `db_apply.apply_ops` + callers | 1 | closed |
| 2 | Р‘Р°Р·Р° UI auto-export РїРѕСЃР»Рµ РїСЂР°РІРѕРє | `web/.../baza-workspace` `dbExportXlsx` / `handleChanged` | 1 | closed |
| 3 | ASR в†’ R15 | `plan_timestamps.write_asr_timestamps_to_r15` в†ђ `generate_audio` | 1 | closed |
| 4 | assemble в†’ R50 | `plan_sheet_v8.write_plan_durations` в†ђ `assemble` | 1 | closed |
| 5 | montage regen в†’ R45/46/48/64 | `montage_board_regen._persist_*` | 1 | closed |
| 6 | shot2 anim в†’ R64 | `animation_prompt_gpt.save_animation_prompt_shot2` | 1 | closed |
| 7 | Excel-first cell edit | `sheet_cell_edit.write_sheet_cell` | 1 | open |
| 8 | GPT TSV/xlsx writeback | `xlsx_text_writeback.writeback_project_xlsx` в†ђ `gpt_client` / `gpt_operator_client` | 1 | closed |
| 9 | `ProjectSheet.write_frame/write_general` callers | telegram, mass_factory, generate_images, make_* | 1 | open |
| 10 | `clear_plan_*` РІ reset | `reset_step` + `plan_sheet_v8.clear_plan_*` | 1 | open |
| 11 | GPT merge РІ project.xlsx | `plan_sheet_v8.merge_gpt_*_into_project` | 1 | open |
| 12 | status/sheet writes scene_design/telegram | callers `write_general` / `write_frame` | 1 | open |

### B. РђРІС‚Рѕ-РёРјРїРѕСЂС‚ Excelв†’DB (РґРѕР»Р¶РЅС‹ СѓРјРµСЂРµС‚СЊ)

| # | РњРµСЃС‚Рѕ | Р¤Р°Р№Р» / СЃРёРјРІРѕР» | Р¤Р°Р·Р° | РЎС‚Р°С‚СѓСЃ |
|---|--------|---------------|------|--------|
| 13 | start_step sync | `project_steps` в†’ `sync_project_xlsx` | 2 | closed |
| 14 | R48в†’anim sync | `sync_animation_prompts_from_xlsx` (start / soft-retry / reset) | 2 | closed |
| 15 | img bootstrap / apply R45 | `xlsx_v8_import.bootstrap_*`, `apply_image_prompts_from_xlsx*` в†ђ `generate_images` | 2 | closed |
| 16 | startup backfill sync | `main.py` backfill `sync_project_xlsx` | 2 | closed |
| 17 | sync_after_plan/split/img_pr | `xlsx_step_runners` | 2 | open |
| 18 | upload/ops auto-sync | `project_ops` (РѕСЃС‚Р°РІРёС‚СЊ С‚РѕР»СЊРєРѕ СЏРІРЅС‹Р№ Import) | 2 | open |
| 19 | ensure_frames + sync | `ensure_frames_from_disk` | 2 | open |
| 20 | empty R48 clears DB anim | Р»РѕРіРёРєР° РІРЅСѓС‚СЂРё `sync_animation_prompts_from_xlsx` | 2 | closed |

### C. Р§С‚РµРЅРёРµ Excel РєР°Рє SoT (РїРµСЂРµРІРµСЃС‚Рё РЅР° Р‘Р”)

| # | РњРµСЃС‚Рѕ | Р¤Р°Р№Р» / СЃРёРјРІРѕР» | Р¤Р°Р·Р° | РЎС‚Р°С‚СѓСЃ |
|---|--------|---------------|------|--------|
| 21 | audio VO РёР· R49 | `generate_audio` / plan readers | 3 | closed |
| 22 | anim_pr hydrate VO R49 | `make_animation_prompts` / `hydrate_voiceovers_from_plan` | 3 | closed |
| 23 | montage fallback R45/R48 | `montage_board*` | 3 | closed |
| 24 | img: РЅРµС‚ РІ Р‘Р” в†’ xlsx | `generate_images` bootstrap path | 3 | closed |
| 25 | R15 readers montage/assemble | `plan_timestamps` / plan_sheet reads | 3 | open |
| 26 | mass_factory В«xlsx Р·Р°РїРѕР»РЅРµРЅВ» | `mass_factory` readiness via R49 | 3 | open |

### D. РЎС‚СЂР°Р¶Рё / СЂРµРіСЂРµСЃСЃРёРё

| # | РџСЂРѕРІРµСЂРєР° | РљР°Рє | Р¤Р°Р·Р° | РЎС‚Р°С‚СѓСЃ |
|---|----------|-----|------|--------|
| 27 | РќРµС‚ openpyxl save РёР· orchestrator steps | unit/grep test | 4 | closed |
| 28 | `apply_ops` РЅРµ С‚СЂРѕРіР°РµС‚ С„Р°Р№Р» | tmp xlsx mtime test | 4 | closed |
| 29 | start_step Р±РµР· sync | mock/assert no call | 4 | closed |
| 30 | Smoke Import в†’ Р‘Р°Р·Р° в†’ Export | СЂСѓС‡РЅРѕР№ / e2e | 4 | open |

---

## 6. Р’РЅРµ СЃРєРѕСѓРїР°

- РЎРјРµРЅР° С„РѕСЂРјР°С‚Р° С€Р°Р±Р»РѕРЅР° v8 / СѓРґР°Р»РµРЅРёРµ Р»РёСЃС‚Р° В«РїР»Р°РЅВ».
- РњРёРіСЂР°С†РёСЏ Postgres.
- РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ Import РїСЂРё РѕР±РЅР°СЂСѓР¶РµРЅРёРё РЅРѕРІРѕРіРѕ mtime С„Р°Р№Р»Р° (СЏРІРЅРѕ РѕС‚РІРµСЂРіРЅСѓС‚Рѕ: A1).

---

## 7. РЎРІСЏР·Р°РЅРЅС‹Рµ РґРѕРєРё (РїРѕСЃР»Рµ РІРЅРµРґСЂРµРЅРёСЏ РѕР±РЅРѕРІРёС‚СЊ)

- `docs/DB_V2.md` вЂ” СѓР±СЂР°С‚СЊ В«write-through РїРѕСЃР»Рµ apply-opsВ» РєР°Рє РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№; Excel = Import/Export only.
- `docs/AGENT_MAP.md` / `.cursor/rules/video-pipeline-ops.mdc` вЂ” R48 РЅРµ SoT; РЅРµ sync РЅР° СЃС‚Р°СЂС‚Рµ.
- `docs/PROMPT_CONTRACT.md` вЂ” СЃС‚РѕРї-Р»РёСЃС‚ Excel/TSV СѓСЃРёР»РёС‚СЊ РґРѕ hard reject writeback.

---

## 8. Approval log

- 2026-08-17: A / A1 / T1 / РїРѕРґС…РѕРґ 2 / С„Р°Р·С‹ 1вЂ“4 / С„Р°Р·Р° 5 С‡РµРєР»РёСЃС‚ 30 вЂ” СЃРѕРіР»Р°СЃРѕРІР°РЅРѕ РІ С‡Р°С‚Рµ.


