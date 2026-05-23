# Callback Inventory

Авто-сгенерировано из `app/telegram/callback_registry.CB` + AST-скана
`app/telegram/**/*.py` и `app/services/**/*.py`.

**58** CB-префиксов · **139** callback_data в коде.

> Для регенерации: `python -m scripts.cb_inventory -o docs/CALLBACK_INVENTORY.md`

---

## Используемые CB-константы

### `CB.EXCEL_PRM` = `'excel_prm'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/bot.py` (L5660): `excel_prm:{pid}:{cid_safe}:default`

**Handler'ы:**

- `app/telegram/bot.py:5666` — `dp.callback_query(F.data.startswith('excel_prm:'))`


### `CB.HERO_COUNT` = `'hero_cnt'`

Кнопок: **3** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6508, L6511, L6516): `hero_cnt:{pid}:0`, `hero_cnt:{pid}:{n}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.HERO_MENU` = `'hero_menu'`

Кнопок: **3** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6556, L6560, L6564): `hero_menu:{pid}:continue`, `hero_menu:{pid}:reset_all`, `hero_menu:{pid}:reset_briefs`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.HERO_RUN` = `'hero_run'`

Кнопок: **1** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6576): `hero_run:{pid}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.HERO_VAR` = `'hero_var'`

Кнопок: **1** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6542): `hero_var:{pid}:{hero_idx}:{n}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.HITL` = `'hitl'`

Кнопок: **5** · Handler-декораторов: **1**

**Кнопки:**

- `app/services/hitl.py` (L131, L132, L139, L148, L152): `hitl:{hitl_id}:approve`, `hitl:{hitl_id}:edit`, `hitl:{hitl_id}:original`
  - … и ещё 2 вариантов

**Handler'ы:**

- `app/telegram/bot.py:7627` — `dp.callback_query(F.data.startswith('hitl:'))`


### `CB.MASS_ADD_TEXT` = `'mass:add_text'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L181): `mass:add_text:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:852` — `dp.callback_query(F.data.startswith('mass:add_text:'))`


### `CB.MASS_DELETE` = `'mass:delete'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L103): `mass:delete:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1079` — `dp.callback_query(F.data.startswith('mass:delete:'))`


### `CB.MASS_DELETE_KEEP` = `'mass:delete_keep'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L246): `mass:delete_keep:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1122` — `dp.callback_query(F.data.startswith('mass:delete_keep:'))`


### `CB.MASS_DELETE_YES` = `'mass:delete_yes'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L242): `mass:delete_yes:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1106` — `dp.callback_query(F.data.startswith('mass:delete_yes:'))`


### `CB.MASS_DL_XLSX` = `'mass:dl_xlsx'`

Кнопок: **3** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L185, L218, L228): `mass:dl_xlsx:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:897` — `dp.callback_query(F.data.startswith('mass:dl_xlsx:'))`


### `CB.MASS_LIST` = `'mass:list'`

Кнопок: **2** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L107): `mass:list`
- `app/telegram/menu.py` (L393): `mass:list`

**Handler'ы:**

- `app/telegram/bot.py:736` — `dp.callback_query(F.data == 'mass:list')`


### `CB.MASS_NEW` = `'mass:new'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L42): `mass:new`

**Handler'ы:**

- `app/telegram/bot.py:747` — `dp.callback_query(F.data == 'mass:new')`


### `CB.MASS_NOOP` = `'mass:noop'`

Кнопок: **3** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L284, L308, L320): `mass:noop`

**Handler'ы:**

- `app/telegram/bot.py:1074` — `dp.callback_query(F.data == 'mass:noop')`


### `CB.MASS_OPEN` = `'mass:open'`

Кнопок: **7** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L47, L138, L193, L232, L250, +1 more): `mass:open:{b.id}`, `mass:open:{batch.id}`
- `app/telegram/mass_prompt_picker.py` (L72): `mass:open:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:805` — `dp.callback_query(F.data.startswith('mass:open:'))`


### `CB.MASS_PAUSE` = `'mass:pause'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L58): `mass:pause:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1232` — `dp.callback_query(F.data.startswith('mass:pause:'))`


### `CB.MASS_PROD` = `'mass:prod'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L82): `mass:prod:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1324` — `dp.callback_query(F.data.startswith('mass:prod:'))`


### `CB.MASS_PROD_CLEAR` = `'mass:prod_clear'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L134): `mass:prod_clear:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1404` — `dp.callback_query(F.data.startswith('mass:prod_clear:'))`


### `CB.MASS_PROD_DESC` = `'mass:prod_desc'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L124): `mass:prod_desc:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1365` — `dp.callback_query(F.data.startswith('mass:prod_desc:'))`


### `CB.MASS_PROD_NAME` = `'mass:prod_name'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L120): `mass:prod_name:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1346` — `dp.callback_query(F.data.startswith('mass:prod_name:'))`


### `CB.MASS_PROD_PHOTO` = `'mass:prod_photo'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L128): `mass:prod_photo:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1385` — `dp.callback_query(F.data.startswith('mass:prod_photo:'))`


### `CB.MASS_PROGRESS` = `'mass:progress'`

Кнопок: **2** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L91, L224): `mass:progress:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:928` — `dp.callback_query(F.data.startswith('mass:progress:'))`


### `CB.MASS_RESUME` = `'mass:resume'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L63): `mass:resume:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1284` — `dp.callback_query(F.data.startswith('mass:resume:'))`


### `CB.MASS_RETRY_PAUSED` = `'mass:retry_paused'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L87): `mass:retry_paused:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1303` — `dp.callback_query(F.data.startswith('mass:retry_paused:'))`


### `CB.MASS_SETTINGS` = `'mass:settings'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L95): `mass:settings:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:981` — `dp.callback_query(F.data.startswith('mass:settings:'))`


### `CB.MASS_SET_NUM` = `'mass:setnum'`

Кнопок: **2** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L280, L288): `mass:setnum:{batch.id}:{field}:+1`, `mass:setnum:{batch.id}:{field}:-1`

**Handler'ы:**

- `app/telegram/bot.py:1035` — `dp.callback_query(F.data.startswith('mass:setnum:'))`


### `CB.MASS_START` = `'mass:start'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L68): `mass:start:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:1156` — `dp.callback_query(F.data.startswith('mass:start:'))`


### `CB.MASS_SUB` = `'mass:sub'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L208): `mass:sub:{batch.id}:{p.id}`

**Handler'ы:**

- `app/telegram/bot.py:952` — `dp.callback_query(F.data.startswith('mass:sub:'))`


### `CB.MASS_TOGGLE` = `'mass:tog'`

Кнопок: **3** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L272, L317, L331): `mass:tog:{batch.id}:auto_review_kinds.{kind}`, `mass:tog:{batch.id}:{field}`

**Handler'ы:**

- `app/telegram/bot.py:1004` — `dp.callback_query(F.data.startswith('mass:tog:'))`


### `CB.MASS_TOPICS` = `'mass:topics'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L78): `mass:topics:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:829` — `dp.callback_query(F.data.startswith('mass:topics:'))`


### `CB.MASS_UPLOAD_XLSX` = `'mass:upload_xlsx'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L189): `mass:upload_xlsx:{batch.id}`

**Handler'ы:**

- `app/telegram/bot.py:878` — `dp.callback_query(F.data.startswith('mass:upload_xlsx:'))`


### `CB.MENU_LIST` = `'menu:list'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L392): `menu:list`

**Handler'ы:**

- `app/telegram/bot.py:629` — `dp.callback_query(F.data == 'menu:list')`


### `CB.MENU_MASS_PAUSE` = `'menu:mpause'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L413): `menu:mpause`

**Handler'ы:**

- `app/telegram/bot.py:4718` — `dp.callback_query(F.data == 'menu:mpause')`


### `CB.MENU_MASS_RESUME` = `'menu:mresume'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L406): `menu:mresume`

**Handler'ы:**

- `app/telegram/bot.py:4763` — `dp.callback_query(F.data == 'menu:mresume')`


### `CB.MENU_NEW` = `'menu:new'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L391): `menu:new`

**Handler'ы:**

- `app/telegram/bot.py:616` — `dp.callback_query(F.data == 'menu:new')`


### `CB.MENU_ROOT` = `'menu:root'`

Кнопок: **3** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/bot.py` (L662): `menu:root`
- `app/telegram/mass_menu.py` (L49): `menu:root`
- `app/telegram/menu.py` (L540): `menu:root`

**Handler'ы:**

- `app/telegram/bot.py:599` — `dp.callback_query(F.data == 'menu:root')`


### `CB.MPRM` = `'mprm'`

Кнопок: **15** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/mass_menu.py` (L99): `mprm:{batch.id}:overview`
- `app/telegram/mass_prompt_picker.py` (L66, L109, L115, L121, L125, +9 more): `mprm:{batch.id}:overview`, `mprm:{batch.id}:{step_code}:add`, `mprm:{batch.id}:{step_code}:del:{name}`
  - … и ещё 9 вариантов

**Handler'ы:**

- `app/telegram/bot.py:1494` — `dp.callback_query(F.data.startswith('mprm:'))`


### `CB.MPRM_SAVE` = `'mprm:save'`

Кнопок: **2** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/mass_prompt_picker.py` (L216, L220): `mprm:save:{batch_id}:{step_code}:{variant_name}:glob`, `mprm:save:{batch_id}:{step_code}:{variant_name}:loc`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.MPRM_TXT_SAVE` = `'mprm:txtsave'`

Кнопок: **2** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/mass_prompt_picker.py` (L234, L238): `mprm:txtsave:{batch_id}:{step_code}:glob`, `mprm:txtsave:{batch_id}:{step_code}:loc`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.NOOP` = `'noop'`

Кнопок: **0** · Handler-декораторов: **1**

**Handler'ы:**

- `app/telegram/bot.py:2144` — `dp.callback_query(F.data == 'noop')`


### `CB.POV` = `'pov'`

Кнопок: **1** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/menu.py` (L501): `pov:{project.id}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.PRM` = `'prm'`

Кнопок: **15** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/prompt_picker.py` (L82, L90, L98, L104, L110, +10 more): `prm:{pid}:hero:msgmenu`, `prm:{pid}:{step_code}:add`, `prm:{pid}:{step_code}:cancel`
  - … и ещё 10 вариантов

**Handler'ы:**

- `app/telegram/bot.py:3396` — `dp.callback_query(F.data.startswith('prm:'))`


### `CB.PROJ_MENU` = `'proj'`

Кнопок: **29** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L587, L657, L2701, L5990, L5993, +2 more): `proj:{p.id}:menu`, `proj:{pid}:delete_yes`, `proj:{pid}:menu`
- `app/telegram/menu.py` (L511, L523, L530, L535, L536, +14 more): `proj:{pid}:menu`, `proj:{pid}:script_regen`, `proj:{pid}:script_replace`
  - … и ещё 15 вариантов
- `app/telegram/prompt_picker.py` (L250): `proj:{pid}:menu`
- `app/telegram/wizard.py` (L214, L360): `proj:{project.id}:menu`, `proj:{project_id}:menu`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.RESET_ASK` = `'reset_ask'`

Кнопок: **3** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6608): `reset_ask:{pid}:{step_code}`
- `app/telegram/menu.py` (L648): `reset_ask:{project.id}:img`
- `app/telegram/prompt_picker.py` (L146): `reset_ask:{pid}:{step_code}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.RESET_DO` = `'reset_do'`

Кнопок: **1** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L2697): `reset_do:{pid}:{step_code}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.STEP_RUN` = `'step_run'`

Кнопок: **1** · Handler-декораторов: **0**

**Кнопки:**

- `app/telegram/bot.py` (L6601): `step_run:{pid}:{step_code}`

⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.


### `CB.TEST_LIST` = `'test:list'`

Кнопок: **1** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L396): `test:list`

**Handler'ы:**

- `app/telegram/bot.py:4871` — `dp.callback_query(F.data == 'test:list')`


### `CB.TEST_NEW` = `'test:new'`

Кнопок: **0** · Handler-декораторов: **1**

**Handler'ы:**

- `app/telegram/bot.py:4881` — `dp.callback_query(F.data == 'test:new')`


### `CB.TEST_NOOP` = `'test:noop'`

Кнопок: **0** · Handler-декораторов: **1**

**Handler'ы:**

- `app/telegram/bot.py:4893` — `dp.callback_query(F.data == 'test:noop')`


### `CB.WIZ` = `'wiz'`

Кнопок: **8** · Handler-декораторов: **1**

**Кнопки:**

- `app/telegram/menu.py` (L444, L490, L494): `wiz:{project.id}:reset`, `wiz:{project.id}:start`
- `app/telegram/wizard.py` (L201, L348, L354, L389, L400): `wiz:{project.id}:edit:{q.field}`, `wiz:{project.id}:reset`, `wiz:{project_id}:set:{field}:{ch.id}`
  - … и ещё 2 вариантов

**Handler'ы:**

- `app/telegram/bot.py:2152` — `dp.callback_query(F.data.startswith('wiz:'))`


---

## Неиспользуемые CB-константы (8)

Эти префиксы определены в CB Enum, но не встречаются ни в кнопках, ни в handler-декораторах. Возможно — кандидаты на удаление, либо префиксы для будущих фич.

- `CB.AI_APPROVE` = `'ai:approve'`
- `CB.AI_CANCEL` = `'ai:cancel'`
- `CB.AI_CLARIFY` = `'ai:clarify'`
- `CB.AI_NOOP` = `'ai:noop'`
- `CB.AI_REGEN` = `'ai:regen'`
- `CB.AI_REJECT` = `'ai:reject'`
- `CB.AI_STATUS` = `'ai:status'`
- `CB.TEST` = `'test'`

