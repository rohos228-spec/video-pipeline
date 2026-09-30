export function actionStatus(status:string):string {
  return ({proposed:'Действие предложено',accepted:'Действие разрешено',rejected:'Действие отклонено',
    reserved:'Recovery разрешён: билет сохранён',queued:'Recovery разрешён: ожидает worker',
    running:'Recovery выполняется',claimed:'Recovery выполняется',submitted:'Запуск передан runtime',
    await_verify:'Worker завершил попытку: ожидается verification',verified:'Verification прошёл',
    human_review:'Нужен человек',
    revoked:'Execution завершён: разрешение отозвано'} as Record<string,string>)[status] ?? status;
}

export function actionReviewMessage(kind:string):string {
  return kind==='node_recovery' || kind==='node_profile_recovery'
    ? 'Результат recovery не подтверждён. Повторный recovery запрещён; требуется разбор человеком.'
    : 'Execution удержан. Причина передачи человеку указана в записи; это не подтверждает запуск recovery или провал verification.';
}

export function actionsModeLabel(state:{configured:boolean;enabled:boolean;held:boolean}|null):string {
  if (!state) return 'Состояние управления не подтверждено';
  if (state.enabled) return state.held ? 'Управление включено · execution удержан' : 'Управление включено';
  return state.configured ? 'Execution завершён · управление выключено' : 'Управление выключено';
}

export function managedLaunchError(options:{configured:boolean;request_limit_max:number;
  nodes:{node_key:string;supported:boolean}[];strategies:string[];
  profiles?:{profile_id:string}[];profile_catalog_hash?:string|null}|null,
  selection:{node_keys:string[];strategies:string[];request_limit:number;
    profile_ids?:string[];profile_catalog_hash?:string|null}):string|null {
  if(!options?.configured) return 'Управляемый запуск выключен: разрешённый маршрут Decision не настроен.';
  if(!selection.node_keys.length) return 'Выберите область выполнения.';
  if(new Set(selection.node_keys).size!==selection.node_keys.length ||
    selection.node_keys.some(key=>!options.nodes.some(node=>node.node_key===key && node.supported)))
    return 'Область содержит неподдержанную ноду. Выберите только принятые исполнители.';
  if(!Number.isSafeInteger(selection.request_limit) || selection.request_limit<1 || selection.request_limit>options.request_limit_max)
    return 'Лимит запросов должен быть целым числом в разрешённых пределах.';
  if(!selection.strategies.length || new Set(selection.strategies).size!==selection.strategies.length ||
    selection.strategies.some(strategy=>!options.strategies.includes(strategy)))
    return 'Выберите разрешённые стратегии.';
  const profiles=selection.profile_ids ?? [];
  if(selection.strategies.includes('retry_profile')) {
    if(!profiles.length || new Set(profiles).size!==profiles.length ||
      profiles.some(id=>!options.profiles?.some(profile=>profile.profile_id===id)))
      return 'Выберите конкретные разрешённые профили для восстановления.';
    if(!selection.profile_catalog_hash || selection.profile_catalog_hash!==options.profile_catalog_hash)
      return 'Каталог профилей изменился. Обновите состояние перед запуском.';
  } else if(profiles.length) return 'Профили требуют разрешения стратегии восстановления.';
  return null;
}
