# Аудит маршрута медиа: куда реально уходят картинки, видео и музыка

Вопрос: ветка Kie в `outsee_retry.generate_image_with_retries` достижима в рабочем (нативном) маршруте? От этого зависит, правда ли
«возобновление по task id Kie» у нод `items`, `images`, `hero`. Всё ниже взято из кода (файл:строка, HEAD `f9af7361`), без сети и без
платных вызовов, `.env` не читался. Тест, закрепляющий вывод: `tests/test_media_route_audit.py`.

## Итог одной фразой

Колега прав. В нативном маршруте слаг модели Kie для картинки (`flux-2-pro`, `seedream-5-pro`, `z-image`, `qwen3-image`) **не уходит в Kie**:
он уходит в Outsee (браузерный бот или, без бота, падает). Строки журнала платных задач с task id Kie для `items`, `images` и `hero`
нативный путь создать не может, поэтому возобновление по task id Kie у `items` в рабочем маршруте не достижимо; оно доказано только сухим
прогоном, который вызывает `kie_http.run_generation` напрямую. Для `videos` и `music` Kie в рабочем маршруте достижим.

## 1. Почему ветка Kie недостижима

`app/services/outsee_retry.py`:

- `:981` `backend = image_provider_for(slug)`: для слага из `KIE_IMAGE_IDS` это `"kie"` (`app/services/media_route.py:20-25, 67-73`).
- `:983-994` ветка «управляемый режим» (`assert_entry` вернул контекст): там `generate_kie_image` вызывается безусловно, для любого слага.
  Это единственное место, где Kie для картинки достижим. Но она работает только при синтетическом профиле: `media_jobs.py:158-159`
  (`media production transport has no authorized network profile`), `media_jobs.py:208-212` (режим `synthetic`, ключ приложения `synthetic*`,
  loopback). Это транспорт `MediaJob`, а не `provider_task_ledger` (`media_resume.enabled()` при `media_spend._managed()` ложно,
  `media_resume.py:62-63`).
- `:995` `use_outsee_api = backend == "outsee" and outsee_api_configured()`: при `backend == "kie"` всегда `False`.
- `:1027` `if use_outsee_api:` оборачивает всё: и ветку Kie (`:1049-1083`, `generate_kie_image`), и ветку Outsee API (`:1084-1118`).
  Внутри этого `if` проверка `if backend == "kie":` (`:1049`) никогда не бывает истинной, потому что внешнее условие требует `backend == "outsee"`.
- `:1123-1126` для слага Kie выполняется `media_spend.assert_submit_allowed("outsee", ...)` и `outsee.generate_image(send_prompt, out_path, **attempt_kwargs)`
  со слагом Kie. Раньше при `outsee is None` это был `AttributeError: 'NoneType' object has no attribute 'generate_image'`; теперь перед циклом отправки
  стоит страж: `backend == "kie" and outsee is None` бросает `KieSlugWithoutOutseeBotError` (наследник `OutseeImageError`, русское сообщение
  «модель Kie не используется в этом маршруте, бота Outsee нет», платный запрос не отправлялся). Маршрут и платное поведение не менялись.

Кто какой `outsee` передаёт:

- `items`: всегда настоящий `OutseeBot(browser_session())` (`generate_items.py:145-147`), слаг `img_gen.outsee_slug` (`:155`); у Kie-генераторов в
  `generation_options.py:41-70` это слаги Kie.
- `images`: при настроенном ключе Outsee бот не создаётся, `outsee = None` (`generate_images.py:94-102, 732-734`), вызовы `:1890, :1910`
  со слагом `generation_options` (`:1793`). Kie-слаг при ключе Outsee даёт `KieSlugWithoutOutseeBotError` до всякой отправки (раньше `AttributeError`, он выходил из шага мимо
  `except OutseeImageError`). Теперь шаг ловит его как обычный сбой кадра: кадр `failed`, владельцу уходит сообщение со своим заголовком.
- `hero`: тот же вызов (`generate_hero.py:846-861`, `:1383`), слаг `img_gen.outsee_slug`.
- `montage_board_regen.py:565`: тот же `generate_image_with_retries`.

Дальше `OutseeBot.generate_image` идёт в Outsee HTTP API (`outsee.py:2101`, `_try_http_image` `:1961-1986`) или в браузерную страницу по слагу
(`outsee.py:2132-2138`). Принимает ли Outsee слаги `flux-2-pro`, `seedream-5-pro`, `z-image`, `qwen3-image`, по коду не проверено (живого
Outsee не было). Если не принимает, шаг упадёт на стороне Outsee, а не уйдёт в Kie.

Прямых вызовов `generate_kie_image` в приложении больше нет: только `outsee_retry.py:989` (управляемая ветка) и `:1052` (недостижимая нативная);
закреплено тестом `test_the_only_production_caller_of_generate_kie_image_...`. `kie_http.run_generation` вызывают ещё `generate_kie_video`
(`kie_http.py:660`), `generate_music.py:160` и вкладка Create без проекта (`kie_create.py:143`, журнал не используется).

## 2. Таблица: нода x семейство модели -> провайдер -> строка журнала -> возобновление

«Строка журнала» это `provider_task_ledger` (`media_resume.py`, `outsee_guard.py`). «Возобновление» это `resume_poll`: опрос уже известного
task id Kie (`media_recovery.classify_states`, `media_recovery.py:117-138`); строки Outsee только намерения без task id и дают `denied`
(`unknown_outcome_outsee`), возобновить их нельзя.

| Нода (ключ) | Семейство модели | Куда уходит в нативном маршруте | Строка журнала | Возобновление по task id |
|---|---|---|---|---|
| `items` (`predmetN`) | Outsee: `gpt-image-2-vip`, `nano-banana-2`, `nano-banana-2-lite` | Outsee API или браузер (`outsee_retry.py:1084` / `outsee.py:2101,2132`) | да, только намерение, api `outsee` (`outsee_guard.py:43,72-91`) | нет (нет task id) |
| `items` | Kie: `flux-2-pro`, `seedream-5-pro`, `z-image`, `qwen3-image` | **Outsee** (браузерный бот со слагом Kie, `outsee_retry.py:1123-1126`), не Kie | да, только намерение Outsee; строки Kie нет | **нет** |
| `images` (`frame_N`) | Outsee-модели | Outsee API (при ключе) или браузер | намерение Outsee | нет |
| `images` | Kie-модели | при ключе Outsee `outsee=None`: типизированная ошибка `KieSlugWithoutOutseeBotError` до отправки (раньше `AttributeError`); без ключа браузер Outsee со слагом Kie | нет (при падении до отправки) / намерение Outsee | **нет** |
| `hero` (`hero_N_vK`) | Outsee-модели (по умолчанию GPT Image 2 VIP) | Outsee API или браузер | намерение Outsee | нет |
| `hero` | Kie-модели | Outsee (как у `items`) | намерение Outsee | **нет** |
| любая из трёх | любая модель, управляемый синтетический режим | Kie loopback (`outsee_retry.py:983-994`, `kie_http.py:483-525`) | нет в `provider_task_ledger`; есть `MediaJob` | нет (другой транспорт, только синтетика) |
| `videos` (`clip_N`) | Kie: Seedance, Kling, Hailuo, Wan, PixVerse, Topaz, Kling 2.6 (`media_route.py:29-41`) | **Kie** (`outsee_retry.py:1653-1654` -> `generate_kie_video` `:1473-1487` -> `run_generation` `kie_http.py:447-480`) | да, `bound` с task id (`media_resume.py:154-183`) | **да** |
| `videos` | Outsee: `veo-3-1-lite` | Outsee API или браузер | намерение Outsee | нет |
| `music` (`music`) | Suno через Kie при настроенном ключе | **Kie** (`generate_music.py:141-160` -> `run_generation`) | да, `bound` с task id | **да** |
| `music` | Ключа Kie нет | браузерный Outsee (`generate_music.py:161-164`) | нет | нет |

Поведение при неизвестном слаге: `image_provider_for` берёт `settings.image_provider` (`media_route.py:73`, по умолчанию `outsee`,
`settings.py:82`); при значении `kie` картинка идёт тем же недостижимым путём.

## 3. Достижимость допуска `items` / `images`

- `items` допущена надзором (`media_admission.py:40-42`, `revisions.MEDIA_RESUME_NODES`), но допуск решает `media_recovery.assess` по журналу.
  `resume_poll` требует строку с task id Kie, ключ `predmetN` (`media_recovery.py:39,124-129`). Нативный шаг `items` такую строку не создаёт
  (раздел 1). Единственные источники: тест, который сам вызывает `kie_http.run_generation` с файлом `predmetN_*.png`
  (`tests/observe_v1/test_media_dry_run_items.py:29-34`), и старые строки журнала, если они остались от прежнего кода внутри окна
  `MEDIA_RESUME_WINDOW_S` (3600 с, `settings.py:399`). Значит в рабочем маршруте допуск `items` практически даёт `nothing_to_do` /
  `verdict_denied` (неизвестный исход Outsee), а не `resume_poll`.
- Сухой прогон `items` честно говорит, что «браузерная лестница Outsee и GPT-перепись в прогоне не участвуют»
  (`test_media_dry_run_items.py:4-5`); поэтому он доказывает механику допуска и возобновления, но не то, что шаг `items` порождает задачу Kie.
- `images` надзором не допущена (`MEDIA_RESUME_NODES` её не содержит): «под управлением (только синтетический путь)». Нативные кадры идут в
  Outsee, возобновление по task id для них невозможно.
- `hero` уже честно «под наблюдением, `no_resumable_task`» (`node_capabilities.py:345-351`); тест `test_hero_dry_run.py:163-180` закрепляет
  маршрут Kie-слага к Outsee для hero.
- `videos` и `music`: Kie в рабочем маршруте достижим, возобновление реально возможно. Сухие прогоны тоже вызывают `run_generation` напрямую
  (`test_media_dry_run_videos.py:37`), но для видео путь шага до `run_generation` существует в коде (`outsee_retry.py:1653-1654`) и проверен
  лестничными тестами (`tests/test_media_resume_scenarios.py`, R15).

## 4. Что утверждалось и что исправлено

| Где | Было | Что не так | Стало |
|---|---|---|---|
| `docs/NODE_COVERAGE.md`, таблица, строка `items` | «возобновление по task id Kie» без оговорки | В нативном шаге Kie для предметов недостижим | оговорка: возобновление работает только для строки журнала с task id Kie; нативный шаг `items` такую строку не создаёт |
| `docs/NODE_COVERAGE.md`, раздел «По нодам», `items` | «рестарт возобновляет ту же задачу Kie» | то же | уточнено: это проверено сухим прогоном через `run_generation`, шаг `items` маршрутом Kie не идёт |
| `docs/NODE_COVERAGE.md`, «Не сделано» (последняя строка про `hero`/`items`) | формула «недостижима... требует решения владельца» уже была | неполно: нет списка нод, нет `AttributeError` у `images` | добавлена ссылка на этот аудит и дополнение про `images` |
| `docs/MEDIA_NODES_READINESS.md`, шапка | «возобновление по известному task id» для `items` | то же | оговорка со ссылкой |
| `docs/CUSTOMER_REPORT.md`, строки `items`, `images` | «То же для картинок предметов» | читается как «Kie возобновляется в бою» | честный текст |
| `app/services/node_capabilities.py`, `CapabilitySpec items.limits`, `MEDIA_RECOVERY_DECISIONS` | перечисляли Outsee как «нет task id», но не говорили, что Kie-слаг в шаге идёт в Outsee | недосказано | добавлена одна строка ограничения; поведение кода не менялось |

Уровень покрытия не менялся: `items` остаётся «под управлением, только возобновление», потому что допуск и возобновление реально
работают для известной задачи Kie (механика доказана), а изменение уровня требует решения владельца (оно затрагивает `revisions.py`,
`actions_recovery.py`, `node_spec`, их правит другой исполнитель).

## 5. Что не проверено

- Живой Outsee/Kie не запускался: принимает ли Outsee слаги Kie, неизвестно.
- Решения по коду не принимались: маршрут не исправлялся (в `outsee_retry.py` ничего не менялось). Варианты для владельца: (а) разрешить
  ветку Kie для `backend == "kie"` вне `if use_outsee_api`, тогда у `items`/`hero`/`images` появятся задачи Kie и возобновление станет реальным,
  но это меняет платную маршрутизацию; (б) запретить Kie-слаги в нодах картинок, пока маршрут не исправлен.
- `.env` не читался, сеть не использовалась.
