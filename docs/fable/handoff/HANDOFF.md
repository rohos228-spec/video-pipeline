# HANDOFF: Фейбл-оркестратор (rohos228-spec/video-pipeline)

Состояние на **18.10.2026 10:35 МСК** (обновлено после мержа #391; прошлая версия — HANDOFF_0630.md.bak). GitHub проверен живьём в 10:33 МСК. API-ключей в этом файле и в папке нет.

## 0. Как пользоваться папкой
- `HANDOFF.md` — этот файл.
- `prs/pr_review_map.txt` — для каждого открытого PR #342–#388: живой HEAD и три последних файла ревью с вердиктом и совпадением SHA.
- `prs/blockers_latest.txt` — список блокеров из последнего ревью по ключевым PR.
- `digests/` — копия `/workspace/reviews/digests` (дайджесты Heil → агентам).
- `reviews_latest/` — копия `/workspace/reviews/out`: свежие полные ревью Opus 06:16–06:27 (батч «y»). **Дайджестов по ним ещё нет** (см. §8).
- `r0cb_reports/` — копия отчётов `/workspace/r0cb/*.md`. Папки с деревьями (`p365c`, `p379d`, `b352`, …) не копировались, они лежат в `/workspace/r0cb/`.
- `local_checks/` — локальные проверки Heil на ПК (`local_*.txt`), скрипт гейта `gate.ps1`, сравнение имён падений `gnew.py`, скрипт последнего батча ревью `run_1018y.py`.
- `plan/` — PLAN.md, context.md, CRITIQUE_2.md (локальные копии от 17.10 12:37).
- `kie_switch.md`, `kie_model_switch.py`, `kie_pricing_raw_20261018.json` — подготовка к переходу на Kie при 5% лимита (сделано 05:40–06:00, ничего не переключено).

## 1. Цель
Встроить в студию video-pipeline ИИ-оркестратор «Фейбл» в стиле Grok Bot: живой чат с сообщениями посреди задачи, мультиагентность (supervisor → planner → critic → executor), чекпоинты и resume, роутер моделей fast/strong, RAG по правилам, нодам и промтам, полный контроль пайплайна сайта. Всё работает в вебе, в ветке **main**, в текущем дизайне и со всеми правками пользователя. План (Фейбл, claude-fable-5): `docs/fable/PLAN.md` в ветке `fable-context` (последний коммит по файлу 2ce25088, 17.10 17:11 МСК: «drop 2-5 s LLM batch stagger requirement for main»).
Этапы: R0a (housepc → main) → R0b (#342 Action Layer → main) → R1 каркас ‖ R2 действия → R3 мультиагентность ‖ R4 RAG → R5 эвалы → R6 канарейка. Heil — куратор: раздаёт задачи, гоняет ревью, проверяет гейты, мержит по решению пользователя.

## 2. Правила пользователя
Ниже — что записано в памяти Heil, с датой. Где это вывод Heil, а не слова пользователя, так и помечено. **Пользователь злится, когда его правила раздувают или выдают выводы Heil за его требования (18.10): при пересказе агентам цитировать дословно, с датой, свои выводы помечать отдельно.**

1. **Сцены и кадры.** Дословная формулировка в памяти не сохранилась. Записано так: «проверки, которые добавляет ассистент, никогда не уничтожают и не теряют существующие сцены и кадры» (11.10). Так же правило формулируется в промте ревью 18.10: «проверки и код не должны САМИ уничтожать или терять существующие сцены и кадры (тихая потеря или оживление шота — баг); осознанное удаление по решению пользователя разрешено. Мягкое удаление — активен: False». *В тексте рутины стоит «никогда не удалять сцены, кадры, персонажей». Это шире, чем записано в памяти: про персонажей и полный запрет удаления в памяти ничего нет. Опираться на узкую формулировку.*
2. **Без force-push.** Есть в тексте рутины от 18.10. Отдельной записи со словами пользователя в памяти нет.
3. **Ответы пользователю: коротко, по-русски, без уточняющих вопросов.** «ХВАТИТ МЕНЯ СПРАШИВАТЬ, МНЕ НУЖЕН ГОТОВЫЙ РЕЗУЛЬТАТ» (10.10). Доводить до готового результата. Длинный текст в чат не вываливать (09.10).
4. **Экономия лимита (18.10).** Агенты пишут в группу «Фейбл: оркестратор» только когда нужен Heil: готов SHA с гейтом на ревью или мерж, либо блокер, который они не могут снять. Без «принято» и статусов. Heil сообщает пользователю только о мержах в main и о падениях ПК.
5. **Падение ПК.** Если ПК пользователя ушёл в офлайн во время работы, сразу сообщить (11.10).
6. **Ревью через Kie на Claude Opus 5.5** (`claude-opus-5-5`), когда у 52pay нет баланса. Одобрено 17.10. Модель для ревью выбирает пользователь по цене.
7. **5% лимита (18.10, дословно 05:36):** «если останется 5% лимита переключи всех агентов на kie выбери оптимальный тариф цена качество антропик, что бы в итоге я надеюсь ушло меньше 3000р на все». И ещё: при остатке меньше 5% немедленно сохранить состояние и данные и написать handoff (этот файл; обновлять после каждого мержа).
8. **Stagger 2–5 с только для локального housepc**, в main и оркестратор не переносить (17.10, закоммичено в PLAN 2ce25088). *Наблюдение Heil: в описаниях ботов R0b и R0c-A до сих пор написано «stagger 2–5 с». Это расходится с решением 17.10, описания стоит поправить.*
9. **Агенты не должны простаивать** (17.10): активно передавать задачи и пинговать.
10. **Использовать уже сделанные анализы**, не переделывать с нуля (17.10).
11. Ветка `test-merge-housepc` — обновлённый дизайн (INT, `C:\Users\Admin\Desktop\wt\INT`, :8770). Обычная Studio работает на :8765.

## 3. Агенты (боты Grok Bot, куратор Heil)
Группа: «Фейбл: оркестратор» (id b2f73c09-9053-4289-944d-5e2c1ddd4ac3). Heil: 0ed59987-5cae-4a44-a933-650862fdae45.
| Бот | id | Зона |
|---|---|---|
| WS1 Пайплайн | d97b6ede-1d9e-4b01-a40b-5d4ae6075794 | R0a (#352/#356), валидаторы `app/orchestrator/validators/**`, tests/validators, pipeline_regression. PR: #351 #352 #356 #368 #371 #379 #380 #382 #383 #384 |
| WS2 Действия | a916995c-ef39-4fb3-bd89-d3fafd68f8fc | R0b (#342 → main), аудит 102 действий, белый список, node_store, 11 новых действий. PR: #344 (влит) #349 #353 #378 #381 #385 (#355 тест spend gate) |
| WS3 Ядро агентов | d5c5e8b3-3538-4a64-a72d-87c2aabee40d | supervisor/planner/critic/executor, чекпоинты, миграции (только WS3), orchestrator_chat. PR: #346 #350 #358 #367 #370 |
| WS4 Веб-интерфейс | 102d5ca4-47fe-4cc3-a0f1-d5ab46654cc9 | веб-чат оркестратора `web/src/app/orchestrator/**` и т.д. PR: #347 #357 #360 #361 #362 #364 #369 #372 #374 #375 #376 #377 #387 |
| WS5 Роутер и RAG (WS5+WS6) | 1df49689-c35e-4c52-8aba-992b886e2aca | роутер fast/strong, RAG, эвалы. PR: #345 #359 #348 #363 #366 #373 |
| R0b Помощник | 5bad279e-4c9f-4ea1-9980-9f23bae2bc02 | помощник WS2 по R0b, вернуть вырезанные символы #342. PR: #386 |
| R0c-A Параллельность | 7bd08459-5738-4b72-b138-47722adfe38b | убрать последовательные LLM-батчи поверх #352. **Открытого PR с веткой r0c-a на GitHub не найдено**, статус неизвестен |
| R0c-B Защита данных | e95275b4-d76a-4227-9876-075cf00f4114 | пометка вместо удаления кадров поверх #352. PR: #354 (заменяется #388), #365 #388; делает независимые проверки (verify1–6) |
Агенты сами запускают облачных агентов Cursor; транскрипты лежат в `/workspace/cloud-agent-transcripts/bc-*.jsonl`. Nikita (nikitasolonukha) — автор #342 и своего Action Layer в репо `video-pipeline-orchestrator`.

## 4. Что в main (живая проверка GitHub, 10:33 МСК)
HEAD main = **85bd226b** (10:32 МСК, merge #391). Хронология:
- **0111fc0d, 05:19:** merge #344 (WS2, аудит Action Layer + node_store atomic_write) @ 3c523d07.
- **ad192320, 05:24:** merge #370 без ревью (стек WS3), **откатан #389** → 4eeda869/**da4629bf** 05:49. Кода WS3 в main нет; повторно только revert of revert.
- **85bd226b, 10:32:** merge **#391** @ ad074129 (WS1 plan_sanity, замена #384; 4 файла +1116: validators/__init__.py — только plan_sanity, plan_sanity.py, result.py, tests/validators/test_plan_sanity.py). Гейт Heil серийно: #391 98F+3E/2042p/8s против main da4629bf 98F+3E/2012p/8s, 0 новых имён (digests/WS1_391_ad074129.md).
- Влиты: **#344, #389 (revert #370), #391**. #384 заменён #391 (закрыть).

## 5. Порядок мержа
*Выведено Heil из баз PR. Отдельного документа пользователя о порядке нет.*
1. **R0a:** #352 (база main) → #356 (база #352). После #391 у #352 конфликт add/add в validators/__init__.py → ребейз на 85bd226b.
2. **R1 валидаторы WS1:** #379 (база #356; конфликт add/add с #391 в __init__.py, plan_sanity.py, test_plan_sanity.py — взять __init__ стека, в plan_sanity сохранить фиксы #391) → #371 (база #351) / #351.
3. **R0c-B поверх #352:** #388 (замена #354) → #365 → #380 → #382; #383/#368 (db_apply, WS1).
4. **R0b:** #353 → #381, #385, #355; #386 (база main; трогает assemble.py и платные шаги — пересечение с #365).
5. **WS3:** #346→#350→#358→#367→#370 (revert of revert, после approve всего стека); потом #387.
6. **WS4:** #347 → дети; #387 после #370.
7. **WS5/WS6:** #345 → #359; #348 → #363 → #366 → #373; #390.

## 6. Открытые PR: HEAD (GitHub 10:33 МСК), последний вердикт Heil, дайджест
Дайджесты: `/workspace/reviews/digests/` (копия в `digests/`). «HEAD ≠ ревью» — после ревью пришли коммиты.

**Политика activity_flags (решение пользователя):** удалён/карантин: true/«да»/1/yes/on/истина → неактивен; «нет»/0/false/«ложь»/"0.0"/NaN/None/нет ключа → живой; неизвестная строка → живой + WARN. Текущий blob WS1 — **f62a0e30** (#371 04ea5dfa, #383 d25cf0c0): числовая строка = число (" 0,0 ", "-0" → 0; "1.0", "1,5" → не ноль). Проба Heil: таблица совпадает.

**WS1**
- **#352** R0a: HEAD **d91c63e8** (новый, 10:27). Последний вердикт на b61f0f38: **request changes** — экспорт/импорт xlsx: db_apply.py:1418-1428 гасит колонку неактивного кадра вместе с закадром, xlsx_v8_import.py:454-464, 666-690, 796-799 сдвигает данные следующих кадров (probe352.py). → WS1_352_b61f0f38.md. Дальше: probe352 в обоих режимах + гейт против 5d6925b1.
- **#356** HEAD **985b526e** (ревью на de2da667: merge чистый, блокирован #352). → WS1_356_8311b38a.md.
- **#379** HEAD **fb09550b** (ревью 1ef07735: RC N7-1). На fb09550b probe379 4/4 ок, R1b без ложных отказов. Гейт против 76198ecd и Opus-ревью идут. → WS1_379_1ef07735.md.
- **#371** HEAD **04ea5dfa**. 9e11e694: гейт 0 новых имён против da4629bf; инкремент 04ea5dfa закрывает «активен "0.0"». Нужен гейт 04ea5dfa. → WS1_371_8269248d.md (+ отчёт 10:31).
- **#383** HEAD **d25cf0c0**: **request changes** — db_apply.py:475-481 входящий по id шот уходит в первую старую копию: живой шот становится активен:False (проба). → WS1_383_d25cf0c0.md.
- **#384** @79edf020 — заменён #391 (влит), не мержить. → WS1_384_79edf020.md.
- **#380** 9069b690 approve with nits (WS1_380_9069b690.md). **#382** 7e18caf0, **#368** e091a8eb, **#351** b0c5e0c3 — без изменений с 06:30.

**R0c-B / R0b**
- **#365** HEAD **114df578** (ревью 8261c86a): блокеры 1 и 3 закрыты; **блокер:** activity_flags_fallback.py (46a44554) расходится с WS1 («ложь»/"0.0"/"-" → неактивен, без WARN; parity 6F/55p); должен совпасть с f62a0e30. → R0cB_365_8261c86a.md.
- **#388** 4f58d5ef: request changes (R0cB_388_4f58d5ef.md). **#354** 60790928: заменяется #388.
- **#386** HEAD **aba22958** (в очереди было a475a93f; ревью 2bd1c2b4: RC, 10 блокеров). → R0b_386_2bd1c2b4.md. Дальше: инкремент, пересечение с #365/#352, гейт.

**WS2**
- **#385** b2c18113: **request changes** (frame.restore не в whitelist; group.delete/prompt.delete_master необратимы, но soft; restore перезаписывает кадры; wiring #370 ValidationError). → WS2_385_b2c18113.md.
- **#381** 1250d5bd: гейт без регрессий PR (WS2_381_1250d5bd.md). **#353** 73a27b77: RC run_sync.py:776 (WS2_353_73a27b77.md). **#378** 2216f10e approve with nits (WS2_378_*). **#355**, **#349** — без изменений.

**WS3** (стек откатан): #350 e0e303ef, #358 60d71820, #367 6c799746, #370 b0dcf06a — HEAD ≠ ревью (WS3_*.md).
**WS4:** #364 2f562ea3, #387 dc5501ec — HEAD ≠ ревью; остальные как в 06:30 (WS4_*.md).
**WS5/WS6:** #390 21a71646 — **замер валиден** (hit@5 0.725, nDCG@8 0.585; WS5_390_eba4e90e.md). #363 5fc82aeb, #366 f38e1044, #373 13f8bb62, #345, #348, #359 — как в 06:30 (WS5_*.md).

## 7. Пути
**ПК пользователя (Windows):**
- репо: `C:\Users\Admin\Desktop\video-pipeline`; venv: `C:\Users\Admin\Desktop\video-pipeline\.venv\Scripts\python.exe`;
- скрипт ревью: `C:\Users\Admin\fable\review_prs.py` (`REVIEW_PROVIDER=kie`, `KIE_MODEL` строка 21; без провайдера идёт 52pay claude-fable-5), батчи: `C:\Users\Admin\fable\run_1018*.py`; переключатель модели: `kie_model_switch.py` (копия здесь);
- ревью и логи: `C:\Users\Admin\fable\reviews\` (полные тексты `pr<N>_<sha8>.md`, `run.log` с расходом, гейты `gate_<sha>.xml/.log/.time`);
- гейт: `gate.ps1 <sha>` (git archive в %TEMP%, pytest --continue-on-collection-errors, junit), сравнение имён: `gnew.py <head> <base>`;
- INT-дизайн: `C:\Users\Admin\Desktop\wt\INT` (:8770, `C:\Users\Admin\Desktop\wt\_tools\run_int.cmd`), Studio :8765.
**Box:** `/workspace/reviews/digests`, `/workspace/reviews/out` (батч y), `/workspace/reviews/{fresh,night}`, `/workspace/reviews/scripts`, `/workspace/r0cb` (отчёты и деревья R0c-B), `/workspace/fable` (PLAN, context, скрипты Фейбла), `/workspace/fable_reviews` (ревью 17.10).

## 8. Kie: расход
- Через Kie (всё на Opus 5.5): вечер 17.10 — $6.40; ночь 18.10 до 05:36 — $6.32. **Итого $12.72 ≈ 1064 ₽** (курс ЦБ 83.6177, по `run.log` на ПК).
- После 05:36: дайджесты 05:36–05:48 называют #388 $0.1023, #371 $0.0736, #383 $0.1070. Возможно, часть из них уже вошла в $12.72. Батч «y» 06:16–06:27 (7 PR) здесь не посчитан. *Оценка Heil по среднему $0.10–0.23 за ревью: ещё ~$0.7–1.6.* Точная сумма — в конце `C:\Users\Admin\fable\reviews\run.log`.
- Баланс Kie около 05:40: 6238.49 credits ≈ $31.19 ≈ 2608 ₽. Цель пользователя — уложить всё в 3000 ₽.
- Подготовка к 5% лимита: `kie_switch.md`. Вкратце: ревью уже идут через Kie. Ботов Grok Bot и облачных агентов Cursor на Kie переключить нельзя (нет инструмента и BYOK). Рекомендация: Opus 5.5 для сложных ревью, Sonnet 5.5 для рутины.

## 9. Незакрытые задачи
Конкретных сроков в файлах нет. Окно «до 10:00 МСК» в `kie_switch.md` — оценка Heil, а не срок от пользователя.
1. **Разослать вердикты батча «y»** (в `reviews_latest/`): WS1 — #352, #356, #379; WS3 — #350, #358, #367; R0b Помощник — #386. Формат как в старых дайджестах: «одно замечание = один коммит + один тест, новый SHA сообщить Heil». Дайджестов по ним ещё нет. Разосланы ли они агентам, по файлам не видно.
2. **Общий блокер activity_flags.py:69-74** (blob e8a3284a) сидит в #352, #371, #383, #386, а #388 держит свою матрицу живости. Нужен один предикат живости на все PR (WS1 владелец validators, R0c-B — frame_tombstones).
3. **Перепроверить PR с HEAD ≠ ревью:** #362 #364 #369 #372 #376 (обновлены 06:22), #365, #378, #384, #363, #351.
4. **Ревью нет вовсе:** #381, #385, #355, #382, #360, #361, #366, #373.
5. **WS3:** довести стек до approve. Повторное влитие #370 только через revert of revert и только по решению пользователя. Потом пересобрать #387.
6. **R0c-A:** выяснить статус, PR на GitHub не найден.
7. **#352 d91c63e8 / #356 985b526e / #365 114df578 / #386 aba22958** — новые HEAD, ревью инкремента. Падения ПК 18.10: 07:50–07:52, ~10:13, ~10:14:30, ~10:20, ~10:32 (pc_drops.log).
8. **Мерж в main** — только по решению пользователя. О мерже сообщить пользователю. После каждого мержа обновить этот файл.
9. **Следить за лимитом:** при 5% действовать по §2 п.7 и `kie_switch.md`.

## STATE 2026-10-18 12:10 MSK (all agents stopped at the user's request, limit)
- main = 85bd226b (#391). Nothing new merged.
- READY (pending a quick check of the stagger-removal commit): #352 HEAD 8f9caaa4, #356 HEAD f36abce5 (merge #352). Order: #352 then #356. Previous full review clean (digest WS1_352_c7885ab7_356_b3567ca4.md).
- #379 HEAD 458e964a: R0c-B verify10 ok; waiting on WS1 fix for the false reject when editing the text of a marked shot without an id; the other minors go in a separate PR after the merge.
- #385 WS2 e2af5b4c, #386 R0b a7093a62, #388 R0c-B f542e50e: in the review queue (after #352).
- WS3 stack: #350 e0e303ef, #358 e9513a8a, #367 a22303b8, #370 9b44c217, bridge 89f802ba. #370 requires a revert of #389 before merging.
- #353 @2f77beb8 request changes: PATCH meta.approval_status bypasses /spend-approval. #381 waits on #353.
- #365 R0c-B: fixing frame_tombstones.merge_kadry_keep_missing (loses удалён); SHA after gate on c7885ab7.
- #371/#383: gates pending. WS4 #362/#369/#372/#376: waiting on new SHAs.
- Rules: no stagger in main (user's rule 2026-10-17); deliberate user deletion allowed; quote user rules with the date.
- 12:11: WS1 posted #379 HEAD eb00f4a3 (fix for the false reject when editing the text of a marked shot without an id, test test_edit_text_of_marked_idless_shot_passes; agent gate 46F, names = 458e964a). Not reviewed: work stopped by the user.
- 12:31: WS2: the Cursor cloud agent limit ran out. The #353 agent broke off at the HTTP test (PATCH meta.approval_status). #353 and #381 are stuck until on-demand billing is enabled or the work moves to another executor. #385 @e2af5b4c is not affected.
- 12:32: R0c-B posted #365 HEAD 0c70877816345765c3b5404fa7d648bbdb89857a (agent claims: activity_flags 27038927, db_apply/db_v2 = c7885ab7, gate 0 new names, parity 68/0, test_merge_keeps_da_deleted green; intermediate b9f0bd86 rolled the flags back to 56a6e8cf and was fixed in 0c708778). Not reviewed: work stopped.
- 12:33: WS4: the move to POST /chat/{session_id}/approve broke off halfway because of the limit (a 409 re-enables the button, so a repeat click sends a second POST). Ready, not reviewed: #364 HEAD 2f562ea3, #387 HEAD dc5501ec (merge #370 9f6bd5a0). Everyone stopped by the user's order (12:32).

## STATE 2026-10-18 13:36 MSK (эконом-режим, Fable 5 через Kie)
- main = **a63f8a39** (merge #356 @f36abce5) поверх ac62863e (merge #352 @8f9caaa4). Задача 1 закрыта.
- 52pay недоступен (timeout/DNS), модель: claude-fable-5 через Kie, скрипт C:\Users\Admin\fable\eco\fcall.py, расход eco\spend.log.
- Git-проверки и тесты теперь на box: /workspace/vp-box (clone), venv /workspace/vp-venv (py3.12). ПК только для вызовов Kie.
- Решение по #352: VO_STAGGER_SEC=1.0 оставлен (как в main и спеке; правило 17.10 только про 2–5 с).
- Журнал мёржей: /workspace/fable_handoff/merges.log. Следующая: ЗАДАЧА 2 (#379 @eb00f4a3) — теперь гейт против a63f8a39.
- 13:58: #379 → main 4776dd9a. На ветку #379 запушены (fast-forward с ПК) 436e826c merge main, c127230f, 4ce98d20, 154e10a3. Fable 5 на Kie падал 13:41–13:53 (api_error/SSL), ревью шли на Opus 5.5.
- 14:12: остановка исполнителя на блокерах (R0c-B/R0b семейство #386/#388, стек #353→#385, паритет grammar #371). Подробно: /workspace/fable_handoff/eco_status_1410.md.
