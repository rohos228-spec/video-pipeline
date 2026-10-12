# Тесты: как запускать и что считать

Числа в этом файле не печатаются руками. Блок между маркерами ниже собирает `scripts/test_report.py` из junit-xml прогона.

## Наборы

| Набор | Команда | Что внутри |
|---|---|---|
| orchestrator | `python scripts/run_orchestrator_tests.py` (`--fast` - только быстрые) | Тесты оркестратора. Без денег и сети: синтетические ключи, одноразовые SQLite. |
| legacy | `python scripts/run_legacy_chunked.py --chunk 40` | Остальные `tests/test_*.py` и `tests/*_test.py` кусками по 40 файлов, каждый кусок в новом процессе. |
| docker | задача `docker` в `.github/workflows/tests.yml` | Четыре модуля `tests/observe_v1`, которым нужен `/.dockerenv` (`_DOCKER_ONLY` в `tests/observe_v1/conftest.py`). Локально они пропущены; в контейнере включаются переменной `VP_RUN_DOCKER_ONLY=1`. |

Дополнительные ключи pytest раннер orchestrator передаёт как есть, например `--junitxml=reports/orchestrator.xml`.

## Маркеры

Зарегистрированы в `pyproject.toml`. Сами по себе маркеры ничего не пропускают и не выбирают: набор меняется только явным `-m`.

- `live` - ходит во внешний сервис (сеть). В CI не запускается никогда, только вручную и с явного разрешения.
- `paid` - тратит деньги. Только вручную, с потолком, запись в `docs/LIVE_RUNS.md`.
- `docker` - нужен изолированный Docker-раннер.
- `slow` - поднимает настоящий подпроцесс, сервер, ffmpeg или whisper; бесплатно и без сети, но дольше.
- `raw_client` - тест ходит в приложение без токена оператора (проверки защиты).

## Правила памяти

- Одновременно ровно ОДИН процесс pytest. Два параллельных прогона на этой машине роняют память.
- Весь legacy-набор целиком не запускать: только `run_legacy_chunked.py`, он гонит куски по одному.
- Перед ручным прогоном другого агента занять замок `mkdir C:\video-pipeline\_pytest.lock`, после прогона `rmdir`.

## Сборка блока чисел

```
python scripts/run_orchestrator_tests.py --junitxml=reports/orchestrator.xml
python scripts/run_legacy_chunked.py --junit-dir reports/legacy
python scripts/test_report.py --suite orchestrator=reports/orchestrator.xml \
    --suite "legacy=reports/legacy/chunk_*.xml" --deselected orchestrator=<N из итоговой строки pytest> \
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
- `test_check_analysis.py::test_every_pipeline_node_type_resolves`
- `test_check_analysis.py::test_prompts_declare_stable_check_ids`
- `test_montage_board_frames.py::test_split_scene_at_keeps_numbers`
- `test_montage_board_frames.py::test_split_scene_all_each_frame_own_scene`
- `test_montage_board_frames.py::test_split_all_visible_ids_hides_extra_shots`
- `test_montage_scene_anchors.py::test_split_sends_each_anchor_to_scene_with_its_text`
<!-- tests:end -->
