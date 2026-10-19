# QC recovery → image: настоящий synthetic worker

30.09.2026. Продолжение от `916b821c9a250d967718fb7999ffec997b7c1e4c`.
Production-код не менялся; проверено исправление `d38a9a8a`.
Результат: **GREEN**, container exit0, OOMKilled=false, без shutdown_gap.

## Что выполнено

Существующий `scripts/test_observe_offline.ps1`, target `group`, режим
`media-recovery`. Он использует прежние `group_acceptance.py`,
`media_acceptance.py`, HTTP fixtures и кэшированный образ. Новый стенд,
executor, adapter, proxy или зависимость не создавались.

Свежая синтетическая копия, отдельный execution
`082d90a4-8060-46ad-93f8-54613d4972c6`, run_id
`observe-20260928-0f4129e9-3c8a-4642-9fd2-99a331c0c303`.
Префикс старой даты — существующий формат runner; сам UUID новый.

Граф этой точечной проверки: пять исходных text-нод до QC → `n_images` →
`n_videos` вне разрешённого scope. HTML-нода отсутствует в этом тестовом графе,
поскольку проверяется именно непосредственный переход QC→image при recovery.
Это **не повтор полной группы script_frames_qc с HTML**; её прежняя приёмка
сохранена в основном handoff.

| Шаг | Фактический результат |
|---|---|
| API start | Один старт первой text-ноды; дальнейшие обычные переходы делает runtime |
| Сценарий, проверка, действие, кадры | Done; по одному loopback LLM POST |
| QC native failure | 9 реальных worker-попыток, 18 malformed JSON fixture ответов; штатные JSON retry, паузы и native exhaustion |
| Diagnosis | Актуальный incident последней неудачной попытки; не выставлен runner вручную |
| Operator Actions retry | Один ticket; второй идентичный API POST возвращает idempotent replay |
| QC recovery | Один дополнительный model POST, один node_start, один data_applied; настоящая verification |
| Продолжение | Сохранённый hash конфигурации восстановлен в reconcile; один managed_successor |
| Изображение | Один submit, один status, один download; настоящий image executor, verifier и Artifact |
| Граница | Пять text-нод и image done; video pending; pilot #1 new; лишних media-запросов ещё 12 секунд нет |

Доказанный порядок событий: **230 recovery_verification → 234 managed_successor →
236 image node_start**. Recovery action:
`d518466c-e7c9-4459-a3ab-e2449fe791ba`.
Исходная attempt: `302027f0-600d-4496-83da-d92ffeb52fff`;
recovery attempt: `e686e24b-9986-4263-874b-47207f29c2f1`.
Incident после проверки закрыт `resolved_confirmed`; execution не held.

Общий счётчик **22 до recovery → 26 после**, лимит32:
4 обычных text POST + 18 native QC POST + 1 recovery QC POST +
1 image submit + 1 status + 1 download. Это категории синтетических операций,
не число платных запросов и не денежный потолок. **Реальных API-вызовов — 0**.

## Целостность и артефакт

- Один frame до и после, один UUID, текст/промпт и поля биты/действие/кадры
  совпадают с exhausted checkpoint. QC fixture возвращает прежний полный список
  кадров, поэтому это проверка применения/сохранения, а не семантического улучшения.
- NodeRun status/attempts в project и master совпадают. В этом исходном пути
  NodeRun.attempts остаётся0; 9 native failures подтверждены step_failure и событиями
  worker, не этим полем. Runner не сбрасывал ни один счётчик.
- Один PNG 256×384, 295737 bytes; SHA256
  `b3c695ed3e47705f4557c4489734b84a721360122d886adae6b4add24f17d09c`.
  Проверена структура файла и provenance, художественное качество не оценивалось.
- Все **271 загруженный app-модуль** и **735 файлов source manifest** совпадают
  с рабочими исходниками. `actions_recovery.py` SHA256:
  `c28b609b5520a50472b5d60b2cd64646464826865f278a09135f13482ac8e085`.

## Материалы

Корень: `outputs/media-v1-20260930/recovery-worker-image-01/`.

- `artifacts/worker-recovery-summary.json` — короткий итог и счётчики;
- `artifacts/recovery-timeline.json` — отбор значимых событий; исходные события сохранены;
- `artifacts/api-history.json`, `group-result.json` — полный журнал и проверки;
- `artifacts/recovery-data-integrity.json` — сравнение frames, состояний и бюджета;
- `artifacts/exhausted-checkpoint/`, `checkpoint-provenance.json` — настоящая
  точка после native exhaustion, до recovery, с SQLite backup;
- `artifacts/free-run-data/` — конечные отдельные БД, тела, файлы и PNG;
- `artifacts/fixture-media-requests.json` — только synthetic HTTP fixture;
- `artifacts/loaded-modules.json`, `container-state.json`, `run-config.json`,
  `source/source-manifest.json`, `artifacts/test-output.txt` — версии и завершение.

Контейнер `vp-observe-test-20260930110117-cb2960a0` сохранён и остановлен.
Работа 08:01:18–08:07:45 UTC, network none, cap-drop ALL, no-new-privileges.
Приложение и fixture общались через loopback. `.env`, реальные ключи,
Docker socket, домашняя папка и внешняя сеть не передавались.

## Вмешательства и граница результата

1. До запуска построен небольшой граф из существующих нод и согласованные
   синтетические входы, явно задан image prompt. Успешные статусы не записывались.
2. Только внешний LLM fixture для QC возвращал прежний malformed JSON до recovery;
   новая попытка получила существующий корректный ответ. Prompt/model/input
   работающей ноды ради успеха не менялись.
3. Тестовый оператор вызвал Actions API после native exhaustion и повторил тот
   же idempotency key. Это не самостоятельный выбор стратегии реальной LLM.
4. Verification/continuation выполнял Observer.loop; runner не вызывал reconcile,
   не запускал image вручную и не сокращал штатные паузы.
5. Runner завершил приложение после конечного состояния и сохранения данных.
   Прежний механизм bounded shutdown/exit не менялся.

Потеря in-memory контекста и восстановление Observer/permissions проверены ранее
в [recovery-config-followup.md](recovery-config-followup.md). Здесь проверен
настоящий отложенный worker→reconcile путь до готового image. **Принудительный
restart процесса между worker finish и verification в этом прогоне не делался.**
Новая проверка UI не заявляется; UI/transport/production конфигурация не менялись.
Предыдущие результаты, исходные журналы и failed контейнеры не изменены.

Для воспроизведения, последовательно и в новую папку:

```powershell
.\scripts\test_observe_offline.ps1 -Target group -GroupMode media-recovery `
  -ResultsRoot 'НОВАЯ-ПАПКА-РЕЗУЛЬТАТОВ'
```

Изменения только в двух существующих тестовых модулях и документации.
Production остаётся выключенным. Push/PR/deploy, установки и платных запросов нет.
