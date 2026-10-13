# Тесты: как запускать и что считать

Числа в этом файле не печатаются руками. Блок между маркерами ниже собирает `scripts/test_report.py` из junit-xml прогона.

## Наборы

| Набор | Команда | Что внутри |
|---|---|---|
| orchestrator | `python scripts/run_orchestrator_tests.py` (`--fast` - только быстрые) | Тесты оркестратора. Без денег и сети: синтетические ключи, одноразовые SQLite. |
| legacy | `python scripts/run_legacy_chunked.py --suite legacy --chunk 40` | Остальные `tests/test_*.py` и `tests/*_test.py` кусками по 40 файлов, каждый кусок в новом процессе. |
| observe | `python scripts/run_legacy_chunked.py --suite observe --chunk 12 --per-chunk-timeout 2400` | Весь `tests/observe_v1` (без пяти docker-модулей), тоже кусками. Раньше эти модули не входили ни в один набор. Набор тяжёлый: на этой машине около 70 минут (медиа-стенды), поэтому куски по 12 файлов и таймаут куска 2400 с. Без `--suite` раннер гонит legacy, потом observe. |
| docker | задача `docker` в `.github/workflows/tests.yml` | Пять модулей `tests/observe_v1`, которым нужен `/.dockerenv` (`_DOCKER_ONLY` в `tests/observe_v1/conftest.py`). Локально они пропущены; в контейнере включаются переменной `VP_RUN_DOCKER_ONLY=1`. |

Дополнительные ключи pytest раннер orchestrator передаёт как есть, например `--junitxml=reports/orchestrator.xml`.

Состав наборов считает `scripts/test_suites.py`. Тест `tests/test_testing_doc_drift.py` требует, чтобы любой файл `test_*.py` или `*_test.py` под `tests/` входил хотя бы в один набор: новый файл или папка без набора роняют сборку.

CI (`.github/workflows/tests.yml`): `orchestrator`, `observe` и `docker` идут на каждый push и pull request, `legacy` по ночам и вручную. На GitHub этот файл пока ни разу не прогонялся: зелёность там не подтверждена, проверено только локально и чтением.

## Маркеры

Зарегистрированы в `pyproject.toml`. Сами по себе маркеры ничего не пропускают и не выбирают: набор меняется только явным `-m`.

- `live` - ходит во внешний сервис (сеть). В CI не запускается никогда, только вручную и с явного разрешения.
- `paid` - тратит деньги. Только вручную, с потолком, запись в `docs/LIVE_RUNS.md`.
- `docker` - нужен изолированный Docker-раннер.
- `slow` - поднимает настоящий подпроцесс, сервер, ffmpeg или whisper; бесплатно и без сети, но дольше.
- `raw_client` - тест ходит в приложение без токена оператора (проверки защиты).
- `ffmpeg` - нужны ffmpeg и ffprobe. Метку ставит `tests/conftest.py` сам на тесты со `skipif` по ffmpeg; `-m ffmpeg` выбирает их, `-m "not ffmpeg"` исключает. Без ffmpeg такие тесты пропускаются, и итог прогона печатает, сколько их и в скольких файлах. С `VP_STRICT_SKIPS=1` (в CI включено) такой пропуск делает прогон красным.

## Правила памяти

- Одновременно ровно ОДИН процесс pytest. Два параллельных прогона на этой машине роняют память.
- Замок настоящий, в коде (`tests/_pytest_guard.py`, подключён в `tests/conftest.py`): файл `_pytest.run.lock` в папке над репозиторием с pid держателя. Второй pytest сразу останавливается с сообщением, кто держит замок; `VP_PYTEST_LOCK_WAIT=секунды` заставляет его ждать. Замок упавшего прогона снимается сам. `--collect-only` замок не берёт. Выключить: `VP_PYTEST_NO_LOCK=1`.
- Старая договорённость `mkdir C:\video-pipeline\_pytest.lock` остаётся очередью между агентами (занять, прогнать, `rmdir`) и настоящему замку не мешает.
- Весь legacy-набор целиком не запускать: только `run_legacy_chunked.py`, он гонит куски по одному.

## Боевая база

Глобальный движок БД создаётся при импорте приложения и берёт путь из `SQLITE_PATH`. Раньше без переменной тесты смотрели в боевой `data/state.db` и один тест записал туда проект. Теперь `tests/conftest.py` до импорта приложения ставит `SQLITE_PATH` и `DATA_DIR` во временную папку, а явный путь внутрь `data/` репозитория отклоняет с ошибкой; в начале сессии движок проверяется ещё раз.

## Сборка блока чисел

```
python scripts/run_orchestrator_tests.py --junitxml=reports/orchestrator.xml
python scripts/run_legacy_chunked.py --junit-dir reports/legacy
python scripts/test_report.py --suite orchestrator=reports/orchestrator.xml \
    --suite "legacy=reports/legacy/chunk_*.xml" --suite "observe=reports/legacy/observe_*.xml" \
    --deselected orchestrator=<N из итоговой строки pytest> \
    --write docs/TESTING.md
```

Если набора в блоке нет, значит его не запускали. «Не выбрано» берётся из итоговой строки pytest (junit его не хранит); без ключа `--deselected` стоит `н/д`.
Известные красные тесты читаются из `docs/audit/G01_RED_TESTS.md` (класс C). Тест `tests/test_testing_doc_drift.py` проверяет, что блок имеет формат, который выдаёт скрипт, и что строки сходятся.

Реестр живых прогонов: `docs/LIVE_RUNS.md`; формат строк проверяет `tests/test_live_runs_registry.py`.

<!-- tests:begin -->
Блок сгенерирован scripts/test_report.py из junit-xml; руками не править.

| Набор | Пройдено | Упало | Ошибок | Пропущено | Не выбрано | Всего |
|---|---|---|---|---|---|---|
| orchestrator-fast | 127 | 0 | 0 | 0 | 0 | 127 |

Дата прогона: 2026-10-12

Известные красные тесты (G01, класс C, нужно решение владельца):
- `test_prompt_library_topic.py::test_nii67_prompt_is_the_file_even_with_blocks_v2`
<!-- tests:end -->
