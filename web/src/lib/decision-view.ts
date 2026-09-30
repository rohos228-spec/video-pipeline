export function decisionStatus(status:string):string {
  const labels:Record<string,string>={ready:'Ожидает решения',model_pending:'Модель получает контекст',
    acting:'Policy разрешила действие',waiting_evidence:'Ожидает новых событий',waiting_worker:'Ожидает worker и verify',done:'Цикл завершён',human_review:'Нужен человек'};
  return labels[status]??`Неизвестное состояние: ${status}`;
}

export function decisionRuntimeLabel(runtime?:{mode:string;configured:boolean}):string {
  if(!runtime?.configured)return 'Модель принятия решений не настроена';
  return runtime.mode==='synthetic'?'Тестовый режим · внешний ответ синтетический':'Рабочий адаптер · провайдер подменён только в локальной приёмке либо задан явно';
}
export function decisionActionLabel(action?:string):string {
  const labels:Record<string,string>={read_events:'Прочитать события',read_contract:'Прочитать контракт ноды',
    retry_same:'Повторить ноду без изменения настроек',retry_profile:'Повторить с одобренным профилем',
    human_review:'Обратиться к человеку',wait:'Подождать',finish:'Завершить после проверки'};
  return action?labels[action]??'Действие не распознано':'Ещё не получено';
}
export function decisionPolicyLabel(policy:{allowed?:boolean;reason?:string}):string {
  if(policy.allowed===true)return 'Разрешено сервером';
  const reason=policy.reason??'';
  if(!reason)return 'Ожидается проверка разрешения';
  if(/wait expired/.test(reason))return 'Ожидание истекло без новых данных — нужен человек';
  if(/version|config|stale/.test(reason))return 'Контекст или конфигурация изменились';
  if(/stop/.test(reason))return 'Процесс остановлен пользователем';
  if(/budget|limit/.test(reason))return 'Лимит исчерпан';
  if(/uncertain/.test(reason))return 'Результат запроса неизвестен — нужен человек';
  if(/verification|recovery/.test(reason))return 'Дополнительная попытка не подтверждена — нужен человек';
  if(/repeated/.test(reason))return 'Повторяющееся решение — нужен человек';
  return 'Сервер остановил цикл. Подробности ниже';
}

export function decisionWaitLabel(policy:{wait?:{until?:string}}):string|null {
  if(!policy.wait)return null;
  const until=policy.wait.until;
  if(!until || !/^\d{4}-\d{2}-\d{2}T/.test(until) || Number.isNaN(Date.parse(until)))return 'Срок ожидания неизвестен — нужен человек';
  return `Ожидание до ${until.replace('T',' ').split('.')[0]} UTC · новые запросы пока не отправляются`;
}
