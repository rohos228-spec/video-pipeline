export function actionStatus(status:string):string {
  return ({proposed:'Действие предложено',accepted:'Действие разрешено',rejected:'Действие отклонено',
    reserved:'Восстановление разрешено: билет сохранён',queued:'Восстановление разрешено: ожидает исполнителя',
    running:'Восстановление выполняется',claimed:'Восстановление выполняется',submitted:'Запуск передан исполнителю',
    await_verify:'Исполнитель завершил попытку: ожидается проверка',verified:'Проверка пройдена',
    human_review:'Нужен человек',
    revoked:'Запуск завершён: разрешение отозвано'} as Record<string,string>)[status] ?? status;
}

export function actionReviewMessage(kind:string):string {
  return kind==='node_recovery' || kind==='node_profile_recovery'
    ? 'Результат восстановления не подтверждён. Повторное восстановление запрещено; требуется разбор человеком.'
    : 'Запуск удержан. Причина передачи человеку указана в записи; это не подтверждает ни запуск восстановления, ни провал проверки.';
}

export function actionsModeLabel(state:{configured:boolean;enabled:boolean;held:boolean}|null):string {
  if (!state) return 'Состояние управления не подтверждено';
  if (state.enabled) return state.held ? 'Управление включено · запуск удержан' : 'Управление включено';
  return state.configured ? 'Запуск завершён · управление выключено' : 'Управление выключено';
}

export function scopedLaunchStrategies(options:{nodes:{node_key:string;supported:boolean;actions?:string[]}[];
  strategies:string[]}|null,nodeKeys:string[]):string[] {
  if(!options || !nodeKeys.length) return [];
  const selected=nodeKeys.map(key=>options.nodes.find(node=>node.node_key===key && node.supported));
  if(selected.some(node=>!node)) return [];
  const actions=new Set(selected.flatMap(node=>node?.actions ?? options.strategies));
  return options.strategies.filter(strategy=>actions.has(strategy));
}

export function managedLaunchError(options:{configured:boolean;request_limit_max:number;
  nodes:{node_key:string;supported:boolean;actions?:string[]}[];strategies:string[];
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
    selection.strategies.some(strategy=>!scopedLaunchStrategies(options,selection.node_keys).includes(strategy)))
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
