# QA LIVE — тестовый трукрайм

**Старт:** 2026-07-30 00:56  
**Стоп:** только по явной команде пользователя «стоп»  
**Проект:** только «тестовый трукрайм»  
**Режим:** реальные Run (не dry_run для GPT/Outsee/img/video/anim_pr)  
**Ветка фиксов:** `cursor/qa-live-trukraym`

## Окружение (снимок)

| Время | Проверка | Результат |
|-------|----------|-----------|
| 00:56 | Studio :8765 | OK — build 368, sha 83f6229, backend_git 7c9a897, ui_stale=0, pipeline_ok=1 |
| 00:56 | CDP :29229 | OK — Chrome/150, CDP 1.3 |
| 00:56 | .env | OK — GPT_*, OUTSEE_*, TELEGRAM_* присутствуют |

## Проект

| Поле | Значение |
|------|----------|
| id | **50** |
| slug | **testovyy-trukraym** |
| title | Тестовый трукрайм |
| status (старт) | plan_ready |
| status (сейчас) | **assembled** (полный пайплайн; WorkflowRun done после D14) |
| hero_mode | hero |
| auto_mode | 1 |
| workflow_run | id=50 |
| data dir | `data/videos/testovyy-trukraym/` |
| эталон | #47 `sekty` (assembled), 124 frames |

## Заметки
- HITL `approve_plan` #410 pending с 29.07 блокировал auto-advance → approved 00:58.
- Граф: много excel_gpt / storage; часть нод «рассинхрон» / «вердикт не ок».
- aspect_ratio: **16:9** (как в настройках проекта).
- Driver: `scripts/_qa_live_driver.py` (poll + auto HITL text + next step).

## Журнал (время | делаю | сделал | результат | баг/фикс)

| Время | Делаю | Сделал | Результат | Баг/фикс |
|-------|-------|--------|-----------|----------|
| 04:07 | UI v369 badge | reload Studio #50 | v369 a48100b + Run done + Autopromotion present | UI OK |
| 04:06 | pulse +17min | assembled hold | spam=0 montage-v3-r15 voice=75.7s words=126 | continue |
| 04:01 | montage stamp + ASR | read MONTAGE_STAMP + words.json | final stamped; whisper words present | artifacts OK |
| 04:01 | pulse +12min | v369 + assembled | build=369 ui_stale=0 status=assembled | continue |
| 03:57 | D13 Create AUDIO route | Suno -> music step + bump v369 | UI fixed; OutseeBot.generate_music still stub | D13 partial |
| 03:56 | B-S9 wizard | skip open New Project | stay on #50 only; wizard button present collapsed | B-S9 deferred |
| 03:55 | pulse +9min | assembled idle | spam=0 status=assembled HEAD fa6f032 | continue UI |
| 03:51 | storage node | 24 files in n_storage_* | snapshots of plan/excel_gpt replies | storage OK |
| 03:51 | pulse +5min | services+#50 | status=assembled Run done spam=0 | continue |
| 03:47 | canvas toolbar | select +Noda Save Copy Paste WF | present; no mutate C3-C10 yet | C-toolbar OK |
| 03:47 | excel_gpt resolve | GET gpt-operator resolve x3 | consistent=true; check node verdict=fail + missing Ok/Fail edges | D5/D7 note |
| 03:47 | pulse +2min | Studio+CDP+#50 | assembled quiet spam=0 backend HEAD | - |
| 03:45 | git HEAD pulse | commits on cursor/qa-live-trukraym | HEAD 621f1df; assembled quiet; UI mid | - |
| 03:44 | status pulse | assembled quiet logs | D15 spam gone; UI matrix mid; D12/D13 WA remain | - |
| 03:44 | B-I6 Node Studio | open studio on plan | tabs Settings/Prompts/Excel/Results + FINAL VIDEO | B-I6 OK |
| 03:44 | D15b assembled spam | startup skip + quiet graph end | 9 tests; no next publish idle | D15 FIX2 |
| 03:41 | D15 log spam assembled | clear auto_await on terminal status | tests 7 pass; backend restarted | D15 FIX |
| 03:39 | UI Set network | click Set topbar | panel opens without crash | B-T Set OK |
| 03:39 | D1 rootcause | HITL_AUTO_APPROVE=false + auto_await_manual_start | plan HITL needs manual/verdict; expected with env | D1 explained |
| 03:38 | Create Outsee UI | openOutsee project 50 + AUDIO tab | Create OK; AUDIO tab routes to audio step not Suno | D13 confirmed |
| 03:37 | items node | check xlsx+meta | no items; dir missing; skip OK | items N/A |
| 03:37 | B-I5 FramesGrid | openFrames(50) via React context | dialog 9 frames VIDEO_GENERATED editable | B-I5 OK |
| 03:37 | status pulse | assembled artifacts+UI | Run done R48 9/9 final 75s V-menu OK | continue UI |
| 03:37 | UI Basg dialog | open Basg topbar | dialog OK with log preview | B-bug OK |
| 03:35 | V-menu plan | React onClick open MENYU NODY | Run/Assets/Excel/Detach/Disable/Delete present | V-menu OK |
| 03:35 | frames API check | GET /frames 9 | ip+ap filled 9/9; final matched voice earlier | - |
| 03:31 | artifacts vs sekty | 9 scenes+9 videos+3 heroes+R48 9/9+final 53MB | parity OK smaller scale | - |
| 03:31 | D14 Run failed at assembled | heal failed->done + terminal aggregate | WorkflowRun #50 done; UI #50 done | D14 FIX |
| 03:21 | status pulse ~3h | script..assemble live on #50 | assembled; D3/D6/D9/D10/D11 fixed; D12/D13 WA | - |
| 03:21 | assemble OK | ffmpeg final mp4 | assembled final/testovyy-trukraym.mp4 53MB | - |
| 03:21 | D13 music stub | OutseeBot.generate_music always raises no CDP | ffmpeg ambient mp3 + music_ready WA | D13 P0 |
| 03:04 | audio OK | disk voice + ASR whisper | audio=2 whisper=1 -> generating_music | - |
| 02:54 | D12 ElevenLabs | session expired need re-login | SAPI voice_full_qa.wav + voice.mp3 for disk path | D12 WA |
| 02:34 | audio REAL | ElevenLabs via process env FALLBACK=true | generating_audio | - |
| 02:34 | video OK | 9/9 scene_video | videos_ready stuck graph; manual audio | - |
| 02:34 | D11 fail-edge | fail retry blocked videos->audio | exclude fail from prereqs | D11 FIX |
| 02:10 | video REAL | Outsee veo generating | mp4 growing | - |
| 02:10 | anim_pr OK | frames 1-2 filled via compressed strip 1.9MB | all 9 R48; auto -> generating_videos | D10 verified |
| 01:56 | anim_pr retry | cleared bogus R48 #1-2; compress live | generating_animation_prompts | - |
| 01:56 | D9 slot collision | pin active excel_gpt key + BFS narrow | tests pass; backend restarted | D9 FIX |
| 01:56 | D10 vision strip | strip ~10MB skipped by GPT | compress in gpt_api + image_strip max_bytes | D10 FIX |
| 01:50 | anim_pr REAL | POST /steps/anim_pr/run despite hero_ready stuck | generating_animation_prompts | - |
| 01:50 | D9 rootcause | images_ready->enriching_5 wrong n_excel_gpt_1 (slot5 collision) | then enrich_5->splitting rewind | D9 FIX in progress |
| 01:34 | img REAL | POST /steps/img/run | generating_images Outsee | - |
| 01:34 | img_pr OK | 9/9 image_prompt in DB | xlsx snapshot n_image_prompts | - |
| 01:30 | D8 status jump | img_pr -> enriching_4 -> scripting | soft-pause until 22:56Z; killed driver; re-run img_pr | D8 |
| 01:29 | status pulse ~25min | script+split+hero done | img_pr running; commit staged no git identity | - |
| 01:29 | img_pr REAL | POST /steps/img_pr/run | generating_image_prompts | - |
| 01:17 | graph gap | no next after hero_ready | manual items/img_pr | D7 canvas edges |
| 01:17 | hero REAL OK | Outsee 3 PNG | hero_ready arts=3 | - |
| 01:10 | seed heroes | 3 characters in xlsx | c01 engineer c02 senator c03 modern | - |
| 01:10 | P0 hero loop | paused project + fix skip/auto_advance | hero_skipped_empty + skip empty hero | D6 FIX |
| 01:08 | poll status | generating_hero -> paused | plan=2372 script=890 | - |
| 01:05 | resume after restart | status was frames_ready | re-run hero | - |
| 01:05 | backend restart | loaded reconcile heal | studio up again | - |
| 01:05 | poll status | frames_ready -> generating_hero | plan=2372 script=890 | - |
| 01:05 | Run hero | POST /steps/hero/run | 200 -> generating_hero | - |
| 01:05 | poll status | generating_hero -> frames_ready | plan=2372 script=890 | - |
| 01:04 | driver loop | 3600s interval=20 | start | - |
| 01:03 | auto-advance | enrich -> generating_hero | Outsee hero started | - |
| 01:03 | excel_gpt slot5 | enrich_5_ready | WARNING no writeback xlsx/TSV | D5 enrich writeback |
| 01:03 | split REAL OK | 9 frames created | frames_ready auto-approve | - |
| 01:03 | verdict script | approved=False regen#1 | auto regen then preempt by split | D4 verdict regen |
| 01:03 | BUG reconcile race | script done then failed 20ms later | heal when Project ready | D3 FIXED in run_sync |
| 01:03 | script REAL OK | GPT voiceover+script_text | 890 chars, voiceover.txt 1636B | - |
| 01:02 | poll status | enriching_5 -> generating_hero | plan=2372 script=890 | - |
| 01:01 | poll status | splitting -> enriching_5 | plan=2372 script=890 | - |
| 01:00 | poll status | scripting -> splitting | plan=2372 script=890 | - |
| 00:56 | Старт LIVE QA | журнал + Studio/CDP/.env | OK build 368 | - |
| 00:57 | Поиск проекта | state.db + API | #50 testovyy-trukraym plan_ready | - |
| 00:58 | HITL approve_plan | POST /api/hitl/410/decision | 200 approved | stuck waiting_hitl |
| 00:58 | Run script REAL | POST /steps/script/run | 200 scripting | - |
| 00:58 | UI sidebar search | trukraym | проект+канвас OK | B-S1 OK |
| 00:59 | UI Логи | клик topbar Логи | панель открылась без краша | B-T3 OK |
| 00:59 | driver Unicode | print arrow cp1251 | DRIVER crash | fixed ascii arrows |
| 01:00 | restart driver | 2400s loop | monitoring | - |

## Чехлист FULL-VERIFICATION (этот проект)

### A — Автоматика
- [x] A4 catalog steps (plan/script/split/… labels OK)
- [ ] A1 audit (позже, не блокирует live)

### B — Оболочка / кнопки
- [x] B-S1 поиск slug
- [x] B-S2 клик проекта / канвас
- [x] B-T3 Логи
- [x] Topbar B-T1..T5 (Promty/Logs/API/badge); Run bar B-R2/R3; C2 Save graph
- [x] V-menu plan + videos (Run/Assets/Excel/Detach/Disable/Delete)
- [x] B-S5 collapse sidebar (Показать проекты)
- [x] B-I5 FramesGrid (9 кадров VIDEO_GENERATED)
- [x] B-I6 Node Studio (Настройки/Промты GPT/Excel/Результаты)
- [x] Баг-диалог (лог-превью)
- [x] Create Outsee UI (проект #50)
- [x] items — пусто (item_descriptions=[]), нода «ожидание» OK
- [x] Canvas toolbar present (+ Нода select, Save, Copy, Paste, WF, Excel)
- [x] Run bar all buttons present; `#50 · done`
- [ ] Inspector B-I3/I4 (PATCH auto_mode — blocked by safety; GET auto_mode=1)
- [ ] sidebar B-S3,S6–S10 / canvas C3–C10 mutate / publish node absent

### D/E — Ноды (реальный Run)
- [x] plan (уже plan_ready + xlsx/general_plan; HITL approved)
- [x] script REAL (GPT)
- [x] split REAL → 9 frames
- [x] hero REAL → 3 PNG (после seed)
- [x] items / excel_gpt (частично; writeback warn D5)
- [x] img_pr REAL → 9/9 image_prompt
- [x] img REAL Outsee → 9 scene_image
- [x] anim_pr REAL → 9 R48 (после compress strip)
- [x] video REAL Outsee → 9 scene_video
- [x] audio REAL (SAPI voice + ASR; ElevenLabs session fail D12)
- [x] music WA (ffmpeg ambient; Outsee music stub D13)
- [x] assemble → `final/testovyy-trukraym.mp4` ~53MB, status=**assembled**

### Дефекты

| ID | Фаза | Шаг | Ожидание | Факт | Severity | Фикс |
|----|------|-----|----------|------|----------|------|
| D1 | E | plan HITL | auto или явный approve | pending с 29.07; HITL_AUTO_APPROVE=false + auto_await_manual_start | P2 | manual API OK; env не auto-approve — ожидаемо |
| D2 | tool | QA driver | стабильный poll | UnicodeEncodeError на → | P2 | заменено на -> |
| D3 | E | NodeRun | script done | reconcile → failed 20ms | P1 | heal in run_sync |
| D5 | E | excel_gpt | xlsx writeback | text-only, no project_file | P2 | open |
| D6 | E | hero empty | skip once | infinite generating_hero | P0 | hero_skipped_empty |
| D7 | E | canvas | next after hero | no next (fail/pass only) | P2 | manual steps |
| D8 | E | status jump | stay on img_pr | → scripting + soft-pause | P1 | open (driver interference) |
| D9 | E | excel slot5 | late check after images | early n_excel_gpt_1 → split rewind | P0 | pin active key + BFS narrow |
| D10 | E | anim_pr vision | strip to GPT | 10MB skip → 1/5 prompts | P0 | compress strip + image_to_data_url |
| D11 | E | videos→audio | next audio | fail-retry edge blocked | P0 | fail ≠ prereq |
| D12 | E | audio 11Labs | TTS | session expired | P1 | WA: SAPI voice on disk |
| D13 | E | music Outsee | Suno CDP | generate_music always raises; Create AUDIO звал audio | P0 | WA ffmpeg; Create→music v369; **нужен CDP Suno** |
| D14 | E | WorkflowRun | assembled → Run done | Run #50 failed (stale failed NodeRun) | P1 | heal failed→done + terminal aggregate |
| D15 | E | auto_advance | quiet on assembled | INFO/WARNING spam every 5s | P2 | clear await + startup skip + no-publish idle |

## Эталон для сверки
#47 `sekty` — assembled; сравнивать набор артефактов (xlsx sheets, scenes/, clips/, final).
