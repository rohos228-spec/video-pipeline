# W49: поставка (G-02, G-03, G-04, G-05, Q-13, Q-20..Q-24, G-01)

Источник находок: `_audit_w40/05_quality_delivery.md`; место в плане: `docs/audit/W40_FINAL_PLAN.md`, фаза 8. Документ написан без git-команд на запись: то, что требует `git rm --cached`, перечислено в разделе 1 для основной сессии.

## 0. Срочно: ключ в .env.example

В отслеживаемом `.env.example` (и в копии `ai-pack/03_config/env.example`) в строке `VIBECODE_API_KEY=` стояло значение длиной 35 символов вида `vk-...`, похожее на настоящий ключ. В рабочей копии значение удалено (строка теперь `VIBECODE_API_KEY=`), но оно остаётся в истории git (минимум коммиты, менявшие `.env.example`, например `a1a92b6c`).
- Считать ключ скомпрометированным: перевыпустить у провайдера (vibecode.moe) и записать новый только в локальный `.env`.
- Поставлять заказчику срез без истории (`git archive HEAD`, как делает `_publish.sh`), а не клон с историей, иначе ключ уедет вместе с ней.
- Значение при проверке не читалось и не печаталось; скрипт `scripts/check_delivery.py` ловит такой ключ шаблоном `sk_vk_key`.

## 1. G-04: чистка индекса (команды для основной сессии)

Что и почему убирается из индекса git (файлы на диске остаются):

| Путь | Причина |
|---|---|
| `exports/` (9 файлов) | выгрузки по проектам заказчика (Спесивцев, Ткач), `tkach-shots-report.html` 800 КБ; в `.gitignore` теперь `/exports/` |
| `tasks/` (16 файлов) | внутренние отчёты потоков с путями чужих профилей; код пишет туда по умолчанию (`run_scene_design_group.py`, `scene_space_validate.py`), поэтому каталог в `.gitignore` целиком (`/tasks/`) |
| `.ai-orchestra-mcp/` (4 файла) | локальный MCP-инструмент агентов, не продукт |
| `.clinerules`, `.clineignore` | правила стороннего агента |
| `.skill.deep.txt`, `.skill.longrun.txt`, `.skill.planTheBuild.txt`, `.skilllong.run.txt` | служебные подсказки агентов |
| `START_AI_ORCHESTRA_PROMPT.txt` | промпт запуска агентов |

`ai-pack/` оставлен: его собирает и проверяет `scripts/build_ai_pack.py` / `scripts/verify_ai_pack.py`.

РЕШЕНИЕ основной сессии (14.10.2026): `git rm --cached` НЕ выполнялся - эти файлы добавили авторы базового репозитория заказчика (Cursor Agent, video-pipeline, rohos125), и их удаление из ветки удалило бы их при слиянии. Вместо этого `C:ideo-pipeline\_publish.sh` удаляет перечисленные пути из публикуемой копии (`rm -rf` в конце), а `check_delivery.py --root <копия> --walk` проверяет результат.

Команды (выполнить в корне репозитория; `.gitignore` уже изменён рабочей копией):

```
git rm -r --cached exports tasks .ai-orchestra-mcp
git rm --cached .clinerules .clineignore .skill.deep.txt .skill.longrun.txt .skill.planTheBuild.txt .skilllong.run.txt START_AI_ORCHESTRA_PROMPT.txt
git add .gitignore .env.example ai-pack/03_config/env.example
git status --short
python scripts/check_delivery.py
```

После этого `python scripts/check_delivery.py` не должен показывать ошибок (предупреждения про имена `scripts/_audit_tkach_*.py`, `scripts/run_spesivtsev_script_frames_qc.py` и пути чужих профилей в `docs/*`, `tasks/*` остаются как предупреждения; решение по ним: либо убрать эти скрипты из поставки в `_publish.sh`, либо оставить как код без данных).

Сделано в рабочей копии без git:
- убраны личные пути владельца и профиля песочницы из `docs/ORCHESTRATOR_COMPLETION_REMAINING_20260930.md`, `docs/orchestrator-qc-noop-v1-acceptance.md`, `docs/audit/AUDIT_5_QUALITY_SECURITY.md` (заменены на `<корень стенда>`, `<путь другой копии репозитория>`, `pytest-of-<user>`);
- в `tests/w5_d12_idempotency_test.py` тестовая строка оператора больше не содержит имя владельца (проверка по-прежнему на не-ASCII и переводы строк);
- в `.env.example` и `ai-pack/03_config/env.example` стёрто значение ключа (раздел 0).

Не исправлено намеренно: пути чужих профилей (`C:\Users\Love...`, `C:\Users\Admin...`, `C:\Users\AiCreator...`) в старых отчётах `docs/QA-*.md`, `docs/FULL-VERIFICATION.md`, `docs/HANDOFF-STUDIO-WEB.md`, `docs/AUTONOMOUS-AUDIT-REPORT.md`, `scripts/guardian/README.md`, `scripts/_emit_chrono_dyn_canvas.py`, `tests/test_action_layer_attachments.py`, `tests/test_nvidia_asr_lock.py`. Это не данные владельца, а следы чужих машин; `check_delivery.py` показывает их предупреждением. Массовая замена в исторических отчётах рискует исказить цитируемые команды.

Вне репозитория (чистятся перед сборкой архива, не через git): `web/.next` (212 МБ), `data/` (105 МБ), `logs/`, `__pycache__`, `video_pipeline.egg-info`, копии `_pub36/_pub37`, `C:\video-pipeline\_*.log/_*.txt`. Всё это уже в `.gitignore`; опасность только при упаковке каталога целиком вместо `git archive`.

## 2. G-02 / G-03 / G-05: требования к `_publish.sh` и автопроверка

`C:\video-pipeline\_publish.sh` лежит вне репозитория и не версионируется. Требования к нему (чтобы публикуемое равнялось проверенному):

1. Источник только `git archive HEAD` чистого дерева (без `git status` изменений). Копировать `.git` предыдущей копии (`cp -r $OLD/.git`) нельзя: вместе с ним едет локальный `config` (удалённые адреса, учётные записи). Делать `git init` в новой копии и ставить remote явно.
2. Правки публикуемого кода (удаление комментариев `_strip.py`, `_strip_ts.mjs`, встроенный python, `sed` по `docs/*`) допустимы только если после них гоняется ТОТ ЖЕ набор проверок над ТЕМ ЖЕ деревом: `python -m compileall app scripts tests`, `python scripts/run_orchestrator_tests.py`, затем `python scripts/check_delivery.py --strict-tests` над каталогом публикации (`--root <NEW> --walk`). Проверка до правок не заменяет проверку после.
3. Жёстких путей к файлам вне репозитория нет: `cp` патча с рабочего стола (`poc-node-packages.patch`) заменить на файл из репозитория или убрать.
4. Финальный скан секретов блокирующий: `python scripts/check_delivery.py --root <NEW> --walk --strict-tests` должен вернуть 0, иначе `_publish.sh` завершается с ошибкой (`set -e`, не `grep ... | head`). Шаблон проверки шире старого `grep -rIl`: Telegram-токены, JWT, Bearer, Supabase, Google, GitHub, `sk-/vk-`, подписанные ссылки (`scripts/scan_secrets.py`).
5. `git add -A` в `_push_*.sh` допустим только после успешной проверки пункта 4, и только в каталоге публикации.
6. Перед push: точная сверка цели (G-01):
   `python scripts/check_delivery.py --root <NEW> --walk --check-push-url origin --expected-push-url <ТОЧНЫЙ_URL> --forbid-push-substr rohos228-spec/video-pipeline --forbid-push-substr rohos228-spec/WebVideoPipeline`.
   Сравнивается `git remote get-url --push origin` с ожидаемым значением целиком; прежняя проверка `git remote -v | grep -q <имя>` совпадала по подстроке в любой строке (fetch или push) и не отвергала запрещённые цели.
7. В рабочей копии диагностики push-URL остаётся `DISABLED_PUSH_FOR_DIAGNOSTICS`; любой push из неё должен падать. Дополнительно желателен хук `pre-push` с той же точной сверкой (основная сессия решает, ставить ли).

Что делает `scripts/check_delivery.py` (тест: `tests/test_check_delivery.py`, без сети и денег):
- запрещённые файлы и каталоги: `.env`/`.env.*` (кроме `*.example`), `*.db`/`*.sqlite`, `*.pem`, `data/`, `exports/`, `logs/`, `web/.next/`, `node_modules/`, `__pycache__/`, `*.pyc`, `*.egg-info/`, служебные файлы агентов; содержимое `.env` не читается;
- секреты по шаблонам `scan_secrets.py`, значения не печатаются; в `tests/` находки по умолчанию предупреждения (синтетические ключи), с `--strict-tests` ошибки;
- личные пути владельца и профиля песочницы: ошибка; чужие `C:\Users\<имя>`: предупреждение;
- данные заказчика: имена проектов заказчика в пути (вне `scripts/`, `tests/`) - ошибка;
- размер: общий до 150 МБ, файл до 20 МБ (параметры);
- обязательные документы (`REQUIRED_DOCS`: README, `.env.example`, TESTING, ARCHITECTURE, SECURITY_DEFAULTS, PUBLISH_SETUP, ORCHESTRATOR_QUICKSTART, CUSTOMER_REPORT, W40_FINAL_PLAN, этот файл);
- по запросу точная проверка push-URL.

Запуск без параметров смотрит `git ls-files` текущего репозитория. До чистки индекса (раздел 1) он вернёт ошибки по `exports/`, `.ai-orchestra-mcp/`, `.clinerules` и т.п.; после чистки ошибок быть не должно.

## 3. Q-20..Q-24: документы приведены к фактам

- Q-20 `docs/SECURITY_DEFAULTS.md`: CORS описан по коду (явный список origin, без `*`; проверка `Host`/`Origin`; токен оператора); добавлено, что вход (`AuthDep`) покрывает не весь API (Q-01) и не делает безопасным открытие Studio в сеть. Тесты `tests/test_w1_8_docs_drift.py` по этому файлу не затронуты.
- Q-21 `docs/ORCHESTRATOR_QUICKSTART.md`, `docs/PUBLISH_SETUP.md`: пометка «снимок на 2026-10-09», ссылка на актуальные замеры (`docs/LIVE_RUNS.md`: наборы №1-№10, цель 0,90 достигнута один раз из десяти).
- Q-22 `HANDOVER.md`, `HOW_TO_RUN.md`, `ARCHITECTURE_AUDIT.md`, `BACKLOG.md`: сверху баннер «УСТАРЕЛО» со ссылкой на актуальные документы; `docs/ORCHESTRATOR_STATUS_CURRENT.md`: баннер «исторический снимок». `docs/CUSTOMER_REPORT.md` и `docs/STAGE_STATUS_REPORT.md`: добавлена строка с актуальной цифрой набора orchestrator на 2026-10-14: 463 passed (старая цифра «268» осталась с пометкой «историческая», этого требует тест).
- Q-23 `docs/audit/AUDIT_5_QUALITY_SECURITY.md`: баннер «исторический документ» с перечнем уже исправленного (CORS, CI, замок pytest).
- Q-24 `docs/TESTING.md` не менялся (уже переписан в этой волне): блок цифр там называется по набору (`orchestrator-fast`) и собирается `scripts/test_report.py`.
- Не сделано: `docs/G01_RED_TESTS.md` (29 разобранных) и `docs/LEGACY_TEST_FAILURES.md` (21 красный) остаются как записи на 2026-10-02; число красных в legacy на текущем коде требует отдельного прогона по `docs/TESTING.md` (основная сессия).

## 4. Q-13: тесты «на исходники»

Масштаб (по аудиту): 23 файла с `inspect.getsource`, 99 читают исходники, 7 читают `web/src`. Усилены три теста:
1. `tests/w1_2_auth_test.py`: вместо «в тексте `login` нет `==`» теперь поведение ручки входа: неверное имя и неверный пароль дают 401 с одинаковым телом, `compare_digest` вызывается по обоим полям в каждом случае, правильный вход даёт токен.
2. `tests/w5_d04_5_managed_auth_test.py`: вместо поиска строки в исходнике `create_task` теперь вызов `create_task` (без платного шва, через `inspect.unwrap`) с подменённым транспортом: проверяется, что в управляемом режиме уходит заголовок Authorization и возвращается taskId.
3. `tests/test_montage_sqlite_lock.py::test_db_busy_timeout_at_least_60s`: вместо поиска «busy_timeout=60000» в исходнике модуля теперь настоящее соединение sqlite проходит через `_configure_sqlite_connection`, и читаются PRAGMA (busy_timeout >= 60000, WAL, synchronous NORMAL, foreign_keys), плюс тип пула `NullPool`.

Не тронуто (описано): остальные тесты `test_montage_sqlite_lock.py` (`test_apply_commits_after_each_op`: счёт `await session.commit()` в тексте; `test_finalize_retries_on_sqlite_lock`: `range(1, 8)` в тексте) и `tests/test_excel_io_guards.py::test_project_steps_no_auto_excel_sync` (отрицательный grep по модулю). Их поведенческая замена требует поднятия сессии БД и подмены `database is locked`, это отдельная работа. `tests/test_testing_doc_drift.py` проверяет формат блока цифр, а не их истинность: истинность обеспечивает только прогон `scripts/test_report.py` из junit-xml.
